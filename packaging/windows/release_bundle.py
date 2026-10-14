#!/usr/bin/env python3
"""Bind already-passed Windows gates to a small, immutable release bundle.

This does not build, install, approve licenses, or replace the existing CI gates.
The producer calls `prepare` only after those gates. The publisher independently
requires the original complete CI run to succeed using GitHub's read-only API.
No approval file is generated here: absent or stale review is a hard failure.
"""
import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import stat
import sys
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

TARGET = "x86_64-pc-windows-msvc"
WORKFLOW = ".github/workflows/ci.yml"
MARKER = "INTERNAL-VALIDATION-ONLY"
LIMIT = 1024 * 1024 * 1024
HASH = re.compile(r"[0-9a-f]{64}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
REQUIRED_CHECKS = {
    "read-only MSI metadata, independent identity, payload scope, and no additional agreement/system action",
    "portable file/legal parity, real CLI, full version, PE identity, unsigned status, and static CRT imports",
    "installer-only variant metadata generated from unchanged real Rust payloads",
    "unchanged official MSI installed as an isolated coexistence fixture",
    "real Next MSI installed beside official GridCraft with default desktop shortcut off",
    "identical-MSI repair allowed; different-ProductCode equal version rejected without mutation",
    "real MSI explicit desktop opt-in, repair, and uninstall preserve official GridCraft and defaults",
    "real-byte installer variants: next.0 to next.1, next.254 to stable, stable to next patch; every reverse rejected unchanged",
    "final matching-version candidate artifacts stayed immutable throughout all installer-only tests",
    *{f"real Rust {mode} native GUI, UI interaction, screenshot, save/reopen, and CLI smoke" for mode in ("portable", "installed")},
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON field")
        result[key] = value
    return result


def read_json(path):
    require(Path(path).is_file() and not Path(path).is_symlink(), "regular JSON file required")
    require(Path(path).stat().st_size <= 16 * 1024 * 1024, "JSON too large")
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique_object,
                      parse_constant=lambda _: require(False, "nonfinite JSON number"))


def file_fact(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= LIMIT, "regular bounded file required")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def write_json(path, value):
    Path(path).write_bytes(canonical(value) + b"\n")


def version(root):
    value = tomllib.loads((Path(root) / "Cargo.toml").read_text())["workspace"]["package"]["version"]
    match = re.fullmatch(r"(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})(?:-next\.(0|[1-9][0-9]{0,2}))?", value)
    require(match is not None, "unsupported package version")
    major, minor, patch = map(int, match.group(1, 2, 3))
    rank = int(match.group(4)) if match.group(4) is not None else 255
    require(max(major, minor, patch) <= 255 and (match.group(4) is None or rank <= 254), "MSI version out of range")
    return value, f"{major}.{minor}.{256 * patch + rank}"


def package_names(full_version):
    base = f"gridcraft-next-{full_version}-windows-x64"
    return (base + ".msi", base + "-portable.zip")


def safe_zip(archive):
    """Inspect before extraction/read; reject traversal, aliases, links and zip bombs."""
    seen, total = set(), 0
    infos = archive.infolist()
    require(len(infos) <= 10000, "archive has too many entries")
    for info in infos:
        name = info.filename
        parts = PurePosixPath(name).parts
        require(name and not name.startswith("/") and "\\" not in name and ":" not in name
                and all(p not in (".", "..") for p in parts)
                and str(PurePosixPath(name)) == name.rstrip("/")
                and not any(ord(c) < 32 or ord(c) == 127 for c in name), "unsafe archive path")
        key = name.rstrip("/").casefold()
        require(key not in seen and MARKER.casefold() not in key, "duplicate/internal archive entry")
        seen.add(key)
        mode = info.external_attr >> 16
        require(not stat.S_ISLNK(mode) and (stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR)), "archive special file")
        require(not info.flag_bits & 1, "encrypted archive forbidden")
        total += info.file_size
        require(info.file_size <= LIMIT and total <= 2 * LIMIT, "archive too large")
    return infos


def toolchain_fingerprint(toolchain):
    """Keep ALL tool/standard-library/runtime/legal hashes; drop only run-local outputs.

    Final-link-evidence contains temporary paths and output bytes. Executable
    SHA256 is separately bound to the exact package/build/native evidence below.
    """
    summary = toolchain["toolchain"]
    stable = {k: v for k, v in summary.items() if k not in {"files", "evidence_only", "distribution_approved", "unresolved"}}
    stable["files"] = sorted((row for row in summary["files"] if row["role"] != "final-link-evidence"), key=lambda row: canonical(row))
    links = [{k: v for k, v in row.items() if k not in {"sha256", "unresolved"}} for row in toolchain["links"]["executables"]]
    for link in links:
        link["runtime_libraries"] = sorted(link["runtime_libraries"], key=lambda row: canonical(row))
    selection = {k: v for k, v in toolchain["selection"].items() if k not in {"evidence_only", "distribution_approved", "unresolved", "applied_to_process", "reviewed_versions_requested"}}
    selection["selected_files"] = sorted(selection["selected_files"], key=lambda row: canonical(row))
    return sha256(canonical({"toolchain": stable, "links": sorted(links, key=lambda row: row["name"]), "selection": selection}))


def review_toolchain(root, inventory_fingerprint, toolchain):
    """One exact review contract shared by pre-package staging and promotion."""
    review = read_json(Path(root) / "packaging/windows/windows-release-review.json")
    require(set(review) == {"schema", "status", "inventory_review_fingerprint", "toolchain_review_fingerprint", "required_toolchain_notices"}
            and review["schema"] == 1 and review["status"] == "approved", "explicit release review required")
    cargo_review = read_json(Path(root) / "packaging/windows/license-review.json")
    require(HASH.fullmatch(review["inventory_review_fingerprint"]) is not None
            and review["inventory_review_fingerprint"] == cargo_review["reviewed_fingerprint"]
            == inventory_fingerprint, "Cargo review fingerprint mismatch")
    require(review["toolchain_review_fingerprint"] == toolchain_fingerprint(toolchain), "toolchain review fingerprint mismatch")
    required_notices = review["required_toolchain_notices"]
    require(type(required_notices) is dict and bool(required_notices), "reviewed downstream toolchain notices are required")
    collected = {row["sha256"] for row in toolchain["toolchain"]["files"] if row["legal_file"] is not None}
    mandatory_rust = {row["sha256"] for row in toolchain["toolchain"]["files"]
                      if row["role"] in {"rust-std-original-notices", "rust-std-original-license", "rust-runtime-fixed-commit-license"} and row["legal_file"] is not None}
    require(bool(mandatory_rust) and mandatory_rust <= set(required_notices.values()), "Rust standard-library original notices must travel with the packages")
    for name, digest in required_notices.items():
        require(type(name) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", name) is not None
                and not name.endswith(".") and MARKER.casefold() not in name.casefold()
                and type(digest) is str and HASH.fullmatch(digest) is not None
                and digest in collected, "required toolchain notice is not an original collected file")
    return review


def check_toolchain_results(chain):
    require(chain["toolchain"]["missing_files"] == [] and set(chain["toolchain"]["unresolved"]) <= {"MICROSOFT_TERMS_APPLICABILITY_NOT_REVIEWED"}, "unresolved toolchain evidence")
    # Selection precedes the build and deliberately preserves these two factual
    # placeholders. Exact review/notices and both actual final links discharge
    # them without rewriting the original evidence as approved.
    selection_placeholders = {"LEGAL_TERMS_NOT_REVIEWED", "FINAL_LINK_INPUTS_NOT_YET_PROVEN"}
    require(type(chain["selection"]["unresolved"]) is list
            and all(type(code) is str and code in selection_placeholders for code in chain["selection"]["unresolved"])
            and chain["selection"]["applied_to_process"] is True
            and chain["links"]["unresolved"] == [], "unresolved selection/link evidence")
    links = chain["links"]["executables"]
    require(len(links) == 2 and {x["name"] for x in links} == {"gridcraft.exe", "gridcraft-cli.exe"}, "two real final links required")
    for link in links:
        require(link["unresolved"] == [] and bool(link["runtime_libraries"]), "final executable/link mismatch")


def load_public_toolchain(selection, links, toolchain):
    spec = importlib.util.spec_from_file_location("public_toolchain", Path(__file__).with_name("validate-public-toolchain-evidence.py"))
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    result = {}
    for key, directory, filename in (("selection", selection, "selection-summary.json"), ("links", links, "link-summary.json"), ("toolchain", toolchain, "toolchain-summary.json")):
        validator.check(directory)
        result[key] = read_json(Path(directory) / filename)
    return result


def check_evidence(evidence, root, source_sha, full_version, msi_version, packages):
    require(evidence["schema"] == 1 and evidence["source_sha"] == source_sha, "evidence source mismatch")
    require(evidence["full_version"] == full_version and evidence["msi_version"] == msi_version, "evidence version mismatch")
    legal = evidence["legal_status"]
    require(legal["source_sha"] == source_sha and legal["full_version"] == full_version and legal["msi_version"] == msi_version, "legal identity mismatch")
    require(legal["distribution_approved"] is True and legal["legal_review_approved"] is True
            and legal["validation_only"] is False and type(legal["collector_exit"]) is int and legal["collector_exit"] == 0, "package is not distribution eligible")
    review = review_toolchain(root, evidence["inventory_review_fingerprint"], evidence["toolchain"])
    native_legal = {row["Name"]: row["Sha256"] for row in evidence["native"]["legal_payload"]}
    for name, digest in review["required_toolchain_notices"].items():
        require(native_legal.get(name) == digest, "required toolchain notice is not a native-tested package file")
    build = evidence["build"]
    require(build["source_sha"] == source_sha and build["target"] == TARGET and build["build_finished_success"] is True
            and build["external_feature_metadata_complete"] is True and build["malformed_line_count"] == 0, "real Cargo build evidence required")
    require(build["lockfile"] == {"present": True, **file_fact(Path(root) / "Cargo.lock")}, "lockfile drift")
    native = evidence["native"]
    require(native["status"] == "passed" and native["cleanup_errors"] == []
            and set(native["checks"]) == REQUIRED_CHECKS, "complete original native gate evidence required")
    require(native["inputs"] == packages, "native-tested package bytes differ")
    require(len(native["pe"]) == 2 and {x["name"] for x in native["pe"]} == {"gridcraft-next.exe", "gridcraft-next-cli.exe"}, "native PE identities missing")
    for row in native["pe"]:
        require(row["signature"] == "NotSigned" and row["machine"] == 0x8664
                and row["subsystem"] == (2 if row["name"] == "gridcraft-next.exe" else 3), "native PE identity mismatch")
    chain = evidence["toolchain"]
    check_toolchain_results(chain)
    links = chain["links"]["executables"]
    for link in links:
        which = "gui" if link["name"] == "gridcraft.exe" else "cli"
        require(link["unresolved"] == [] and link["runtime_libraries"] and link["sha256"] == build[which]["sha256"], "final executable/link mismatch")
    return review


def check_portable(path, evidence, full_version):
    prefix = f"gridcraft-next-{full_version}-windows-x64-portable/"
    native = evidence["native"]
    expected = {row["Name"]: row["Sha256"] for row in native["legal_payload"]}
    require(len(expected) == len(native["legal_payload"]) and len(expected) >= 6, "legal payload missing/duplicate")
    require(all("/" not in name and "\\" not in name and MARKER.casefold() not in name.casefold() for name in expected), "invalid/internal legal payload")
    expected.update({"gridcraft-next.exe": evidence["build"]["gui"]["sha256"], "gridcraft-next-cli.exe": evidence["build"]["cli"]["sha256"]})
    with zipfile.ZipFile(path) as archive:
        infos = safe_zip(archive)
        actual = {}
        for info in infos:
            require(info.filename.startswith(prefix), "portable root differs from full version")
            if info.is_dir():
                require(info.filename == prefix, "nested portable directory")
                continue
            name = info.filename[len(prefix):]
            require(name in expected and "/" not in name, "unexpected portable payload")
            data = archive.read(info)
            actual[name] = sha256(data)
            if not name.endswith(".exe"):
                require(b"internal validation only" not in data.lower() and MARKER.lower().encode() not in data.lower(), "internal validation marker in package content")
            if name == "THIRD_PARTY_INVENTORY.json":
                inventory = json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique_object)
                fingerprint = inventory.pop("review_fingerprint")
                require(inventory["schema"] == 2 and inventory["target"] == TARGET and inventory["problems"] == []
                        and inventory["lock_sha256"] == evidence["build"]["lockfile"]["sha256"]
                        and sha256(canonical(inventory)) == fingerprint == evidence["inventory_review_fingerprint"], "portable inventory differs from reviewed build")
                require(sha256(data) == evidence["legal_status"]["inventory_sha256"], "inventory bytes differ")
        require(actual == expected and "THIRD_PARTY_INVENTORY.json" in actual, "portable payload differs from native-tested/staged bytes")


def validate_bundle(directory, root, source_sha, repository, run_id, attempt):
    directory = Path(directory)
    require(SHA.fullmatch(source_sha) is not None and REPO.fullmatch(repository) is not None, "invalid source/repository")
    full_version, msi_version = version(root)
    names = package_names(full_version)
    expected_files = {*names, "release-manifest.json", "RELEASE-EVIDENCE.json", "SHA256SUMS"}
    require(directory.is_dir() and not directory.is_symlink()
            and {p.name for p in directory.iterdir()} == expected_files, "bundle must contain only the five release assets")
    facts = {name: file_fact(directory / name) for name in sorted(expected_files)}
    manifest = read_json(directory / "release-manifest.json")
    require(set(manifest) == {"schema", "repository", "source_sha", "full_version", "msi_version", "target", "ci", "packages", "evidence", "signing"}, "unexpected manifest fields")
    require(manifest["schema"] == 1 and manifest["repository"] == repository and manifest["source_sha"] == source_sha
            and manifest["full_version"] == full_version and manifest["msi_version"] == msi_version
            and manifest["target"] == TARGET and manifest["signing"] == "unsigned", "manifest identity mismatch")
    require(manifest["ci"] == {"run_id": run_id, "run_attempt": attempt, "workflow": WORKFLOW}, "producer run/attempt mismatch")
    packages = {name: facts[name] for name in names}
    require(manifest["packages"] == packages and manifest["evidence"] == facts["RELEASE-EVIDENCE.json"], "asset hash mismatch")
    evidence = read_json(directory / "RELEASE-EVIDENCE.json")
    check_evidence(evidence, root, source_sha, full_version, msi_version, packages)
    check_portable(directory / names[1], evidence, full_version)
    with (directory / names[0]).open("rb") as msi:
        require(msi.read(8) == bytes.fromhex("d0cf11e0a1b11ae1"), "MSI container required")
    sums = "".join(f"{facts[name]['sha256']}  {name}\n" for name in sorted(expected_files - {"SHA256SUMS"}))
    require((directory / "SHA256SUMS").read_text() == sums, "SHA256SUMS mismatch")
    return manifest


def prepare(args):
    root = args.root.resolve()
    require(SHA.fullmatch(args.source_sha) is not None and REPO.fullmatch(args.repository) is not None
            and type(args.run_id) is int and args.run_id > 0 and type(args.attempt) is int and args.attempt > 0, "invalid producer identity")
    full_version, msi_version = version(root)
    native = read_json(args.native_evidence / "package-evidence.json")
    require(native["schemaVersion"] == 1 and native["sourceSha"] == args.source_sha
            and native["fullVersion"] == full_version and native["msiVersion"] == msi_version, "native gate identity mismatch")
    toolchain = load_public_toolchain(*(args.toolchain_evidence / folder for folder in ("selection", "links", "toolchain")))
    build = read_json(args.build_evidence / "build-summary.json")
    # The original build summary remains in CI diagnostics. No compiler text, local
    # paths, raw installer logs, or environment values enter the Release evidence.
    build = {key: build[key] for key in ("source_sha", "target", "lockfile", "gui", "cli", "build_finished_success", "external_feature_metadata_complete", "malformed_line_count")}
    packages = {name: file_fact(args.dist / name) for name in package_names(full_version)}
    inputs = {re.split(r"[/\\]", row["path"])[-1]: {k: row[k] for k in ("bytes", "sha256")} for row in native["inputs"]}
    require(len(native["inputs"]) == len(inputs) == 2, "exactly two native-tested release inputs required")
    inventory = read_json(args.build_evidence / "third-party/THIRD_PARTY_INVENTORY.json")
    evidence = {"schema": 1, "source_sha": args.source_sha, "full_version": full_version, "msi_version": msi_version,
                "legal_status": read_json(args.build_evidence / "legal-status.json"), "inventory_review_fingerprint": inventory["review_fingerprint"],
                "build": build, "toolchain": toolchain,
                "native": {"status": native["status"], "checks": native["checks"], "cleanup_errors": native["cleanupErrors"],
                           "inputs": inputs, "legal_payload": native["legalPayload"], "pe": [{k: row[k] for k in ("name", "machine", "subsystem", "signature")} for row in native["pe"]]}}
    check_evidence(evidence, root, args.source_sha, full_version, msi_version, packages)
    check_portable(args.dist / package_names(full_version)[1], evidence, full_version)
    require(not args.output.exists(), "release bundle output must be new")
    args.output.mkdir(parents=True)
    for name in packages:
        shutil.copyfile(args.dist / name, args.output / name)
    write_json(args.output / "RELEASE-EVIDENCE.json", evidence)
    manifest = {"schema": 1, "repository": args.repository, "source_sha": args.source_sha, "full_version": full_version,
                "msi_version": msi_version, "target": TARGET, "ci": {"run_id": args.run_id, "run_attempt": args.attempt, "workflow": WORKFLOW},
                "packages": packages, "evidence": file_fact(args.output / "RELEASE-EVIDENCE.json"), "signing": "unsigned"}
    write_json(args.output / "release-manifest.json", manifest)
    sums = "".join(f"{file_fact(path)['sha256']}  {path.name}\n" for path in sorted(args.output.iterdir()))
    (args.output / "SHA256SUMS").write_text(sums, encoding="utf-8", newline="\n")
    validate_bundle(args.output, root, args.source_sha, args.repository, args.run_id, args.attempt)
    print("Validated five-asset release bundle; complete CI success is still required before publication.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    for name in ("dist", "build-evidence", "native-evidence", "toolchain-evidence", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    try:
        prepare(parser.parse_args())
    except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile) as error:
        print(f"RELEASE_BUNDLE_BLOCKED: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
