#!/usr/bin/env python3
"""Inventory exact Cargo build inputs and preserve their original legal texts.

This is a collection/review gate, not a legal-opinion engine. An inventory without
an exact reviewed fingerprint is deliberately not approved for distribution.
No network is used. Run after a successful, locked GUI+CLI build with Cargo JSON
messages and cargo metadata for the same source/target. Host build dependencies
are conservatively retained and labelled; they are not claimed to be linked.
"""

import argparse
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path


class CollectionError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def legal_name(path):
    name = path.name.lower()
    credit_file = path.stem.lower() in ("authors", "contributors") and path.suffix.lower() in ("", ".txt", ".md", ".rst")
    return name.startswith(("license", "licence", "copying", "copyright", "notice", "unlicense")) or name in ("ofl.txt", "ufl.txt") or name.endswith("-license.txt") or credit_file


def safe_file(root, relative):
    """Reject symlinks/path escapes instead of copying an unrelated local file."""
    root = root.resolve()
    path = root / relative
    try:
        path.resolve().relative_to(root)
    except ValueError as error:
        raise CollectionError(f"legal path escapes package root: {relative}") from error
    if any(part.is_symlink() for part in [path, *path.parents] if part != root and root in part.parents):
        raise CollectionError(f"symlink legal path is not accepted: {relative}")
    if not path.is_file() or path.stat().st_size == 0 or path.stat().st_size > 2 * 1024 * 1024:
        raise CollectionError(f"legal file missing, empty, or oversized: {relative}")
    data = path.read_bytes()
    if b"\0" in data:
        raise CollectionError(f"binary material in legal payload: {relative}")
    return data


def artifact_scope(message, package_id):
    """Keep only actual feature/kind instances, never paths or build-script env."""
    target = message.get("target")
    if not isinstance(target, dict):
        raise CollectionError(f"compiler-artifact target metadata missing or invalid: {package_id}")
    normalized = []
    for name, values, allow_empty in (("features", message.get("features"), True), ("target.kind", target.get("kind"), False)):
        if not isinstance(values, list) or (not values and not allow_empty) or any(
            not isinstance(value, str) or not value or value != value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            for value in values
        ):
            raise CollectionError(f"compiler-artifact {name} metadata missing or invalid: {package_id}")
        normalized.append(tuple(sorted(set(values))))
    allowed_kinds = {"bin", "lib", "rlib", "dylib", "cdylib", "staticlib", "proc-macro", "example", "test", "bench", "custom-build"}
    if not set(normalized[1]).issubset(allowed_kinds):
        raise CollectionError(f"compiler-artifact target.kind is not recognized: {package_id}")
    return tuple(normalized)


def scope_records(instances):
    return [{"features": list(features), "target_kind": list(kinds)} for features, kinds in sorted(instances)]


def artifact_packages(messages, metadata):
    known = {p["id"]: p for p in metadata["packages"]}
    observed = {}
    instances = {}
    root_instances = set()
    roots = set()
    success = False
    for message in messages:
        if not isinstance(message, dict):
            raise CollectionError("Cargo JSON message must be an object")
        reason = message.get("reason")
        if reason == "build-finished":
            success = message.get("success") is True
        if reason not in ("compiler-artifact", "build-script-executed"):
            continue
        package_id = message.get("package_id")
        if not isinstance(package_id, str) or package_id not in known:
            raise CollectionError(f"build package missing from metadata: {package_id}")
        observed.setdefault(package_id, set()).add(reason)
        if reason == "compiler-artifact":
            scope = artifact_scope(message, package_id)
            instances.setdefault(package_id, set()).add(scope)
            name = message["target"].get("name")
            if message.get("executable") and name in ("gridcraft", "gridcraft-cli"):
                if "bin" not in scope[1]:
                    raise CollectionError(f"root executable artifact is not a bin target: {name}")
                roots.add(name)
                root_instances.add((name, *scope))
    if not success:
        raise CollectionError("Cargo JSON messages do not end in a successful build-finished record")
    if roots != {"gridcraft", "gridcraft-cli"}:
        raise CollectionError("both actual gridcraft and gridcraft-cli executable artifacts are required")
    for package_id in observed:
        if package_id not in instances:
            raise CollectionError(f"observed build package lacks compiler-artifact scope metadata: {package_id}")
    root_records = [{"name": name, "features": list(features), "target_kind": list(kinds)} for name, features, kinds in sorted(root_instances)]
    return known, observed, instances, root_records


def collect(metadata, messages, lock_bytes, output, target, supplements=None):
    """Supplements explicitly bind reviewed text bytes to an exact package/source."""
    supplements = supplements or {}
    if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+){2,}", target):
        raise CollectionError("an explicit valid build target triple is required")
    known, observed, instances, root_records = artifact_packages(messages, metadata)
    locked = tomllib.loads(lock_bytes.decode("utf-8"))["package"]
    workspace = set(metadata.get("workspace_members", []))
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise CollectionError("output must be empty; stale licenses must not enter the package")
    output.mkdir(parents=True, exist_ok=True)
    records, problems = [], []
    for package_id in sorted(observed):
        package = known[package_id]
        if package_id in workspace:
            continue  # Project LICENSE/NOTICE are separately staged verbatim.
        root = Path(package["manifest_path"]).parent
        identity = {k: package.get(k) for k in ("name", "version", "source", "license", "repository", "authors")}
        record = {**identity, "package_id": package_id, "build_evidence": sorted(observed[package_id]), "compiler_artifacts": scope_records(instances[package_id]), "files": []}
        matches = [p for p in locked if p.get("name") == package["name"] and p.get("version") == package["version"] and p.get("source") == package.get("source")]
        if len(matches) != 1:
            problems.append(f"{package_id}: not uniquely matched to exact Cargo.lock source")
        else:
            record["registry_checksum"] = matches[0].get("checksum")
        paths = set()
        # Legal filenames anywhere in the exact package catch bundled native sources
        # and font notices. No source, font binary, screenshot, or example is copied.
        for path in root.rglob("*"):
            if path.is_file() and legal_name(path):
                paths.add(path.relative_to(root).as_posix())
        if package.get("license_file"):
            license_file = Path(package["license_file"])
            if license_file.is_absolute():
                try:
                    license_file = license_file.relative_to(root)
                except ValueError as error:
                    raise CollectionError(f"license_file is outside {package_id}") from error
            paths.add(license_file.as_posix())
        # Hack's attribution file is not named LICENSE. The four pinned font
        # notices all matter; epaint's MIT/Apache metadata does not replace them.
        if package["name"] == "epaint_default_fonts":
            if package["version"] != "0.36.2":
                problems.append(f"{package_id}: font package version requires renewed review")
            paths.update(("fonts/Hack-Regular.txt", "fonts/OFL.txt", "fonts/UFL.txt", "fonts/emoji-icon-font-mit-license.txt"))
        inputs = []
        for relative in sorted(paths):
            try:
                inputs.append((relative, safe_file(root, relative), "cargo-package"))
            except CollectionError as error:
                problems.append(f"{package_id}: {error}")
        supplemental = supplements.get(package_id, supplements.get(f"{package['name']}@{package['version']}", []))
        for item in supplemental:
            if item.get("source") != package.get("source"):
                raise CollectionError(f"supplement source does not match {package_id}")
            supplement_path = Path(item["path"])
            supplement_root = Path(item.get("_root", supplement_path.parent))
            relative = supplement_path if "_root" in item else supplement_path.name
            data = safe_file(supplement_root, relative)
            if digest(data) != item["sha256"] or not item.get("origin_url", "").startswith("https://"):
                raise CollectionError(f"unverified supplement for {package_id}")
            if not data or b"\0" in data or len(data) > 2 * 1024 * 1024:
                raise CollectionError(f"invalid supplement text for {package_id}")
            inputs.append((item["label"], data, item["origin_url"]))
        for relative, data, origin in inputs:
            identifier = digest(canonical([package_id, relative]))[:16]
            base = re.sub(r"[^A-Za-z0-9._-]", "_", Path(relative).name)
            filename = f"third-party-{package['name']}-{package['version']}-{identifier}-{base}.txt"
            (output / filename).write_bytes(data)
            record["files"].append({"original_path": relative, "name": filename, "sha256": digest(data), "bytes": len(data), "origin": origin})
        if not record["files"]:
            problems.append(f"{package_id}: no original license/notice text collected")
        if not package.get("license") and not package.get("license_file"):
            problems.append(f"{package_id}: no license expression or license_file declared")
        records.append(record)
    inventory = {"schema": 2, "scope": "Observed GUI/CLI Cargo build package set, conservatively including host build inputs; feature/kind instances do not prove final linkage or distinguish host from target compilation", "target": target, "root_artifacts": root_records, "lock_sha256": digest(lock_bytes), "packages": records, "problems": problems}
    inventory["review_fingerprint"] = digest(canonical(inventory))
    (output / "THIRD_PARTY_INVENTORY.json").write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = ["GridCraft-Next third-party build-input notices", "", inventory["scope"], "", "Original license and copyright texts are preserved in the named files.", "Declared SPDX expressions are recorded without replacing AND obligations or", "silently treating OR alternatives as a single universal license.", ""]
    for record in records:
        lines.extend((f"{record['name']} {record['version']}", f"Source: {record['source']}", f"Declared license: {record['license']}", f"Repository: {record['repository']}"))
        lines.extend(f"  {item['name']} (SHA256 {item['sha256']})" for item in record["files"])
        lines.append("")
    (output / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(lines), encoding="utf-8")
    return inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--build-messages", type=Path, required=True)
    parser.add_argument("--lockfile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", required=True, help="Explicit Cargo build target triple; host build inputs remain conservatively included")
    parser.add_argument("--supplements", type=Path)
    parser.add_argument("--reviewed-fingerprint", help="Exact fingerprint approved after license/notice review; no wildcard or skip mode")
    args = parser.parse_args()
    try:
        metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
        messages = [json.loads(line) for line in args.build_messages.read_text(encoding="utf-8").splitlines() if line.strip()]
        supplements = json.loads(args.supplements.read_text(encoding="utf-8")) if args.supplements else None
        if supplements is not None:
            for items in supplements.values():
                for item in items:
                    relative = Path(item["path"])
                    if relative.is_absolute() or ".." in relative.parts:
                        raise CollectionError("supplement paths must stay inside the reviewed supplement directory")
                    item["_root"] = str(args.supplements.parent)
                    item["path"] = relative.as_posix()
        inventory = collect(metadata, messages, args.lockfile.read_bytes(), args.output, args.target, supplements)
        print(json.dumps({"packages": len(inventory["packages"]), "problems": inventory["problems"], "review_fingerprint": inventory["review_fingerprint"]}))
        if inventory["problems"] or args.reviewed_fingerprint != inventory["review_fingerprint"]:
            print("Legal collection is not distribution-approved: resolve issues and review the exact fingerprint.", file=sys.stderr)
            return 2
        return 0
    except (CollectionError, OSError, ValueError, KeyError) as error:
        print(f"License collection failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
