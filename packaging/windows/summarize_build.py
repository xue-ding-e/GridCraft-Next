#!/usr/bin/env python3
"""Reduce runner-local Cargo JSON to public build evidence, without raw source/env."""
import argparse
import hashlib
import json
import re
from pathlib import Path


def file_fact(path):
    path = Path(path)
    if not path.is_file():
        return {"present": False}
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return {"present": True, "bytes": path.stat().st_size, "sha256": h.hexdigest()}


def reduce_messages(lines):
    packages, diagnostics, malformed = set(), [], 0
    artifacts = set()
    feature_metadata_complete = True
    success = None
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            malformed += 1
            continue  # Never expose an unexpected raw line.
        reason = record.get("reason")
        if reason == "build-finished":
            success = record.get("success") is True
        elif reason == "compiler-artifact":
            package = record.get("package_id", "")
            # Local source package IDs contain absolute paths; names/versions are
            # already in the reviewed source. Do not upload the runner path.
            if package.startswith(("registry+", "git+")):
                packages.add(package)
                features = record.get("features")
                target = record.get("target", {})
                kinds = target.get("kind") if isinstance(target, dict) else None
                valid_features = isinstance(features, list) and all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*", value) for value in features)
                valid_kinds = isinstance(kinds, list) and all(value in ("lib", "rlib", "dylib", "cdylib", "staticlib", "proc-macro", "bin", "example", "test", "bench", "custom-build") for value in kinds)
                if valid_features and valid_kinds:
                    artifacts.add((package, tuple(sorted(features)), tuple(sorted(kinds))))
                else:
                    feature_metadata_complete = False
        elif reason == "compiler-message":
            message = record.get("message", {})
            if message.get("level") not in ("error", "warning", "failure-note"):
                continue
            spans = [{"file": re.split(r"[/\\]", str(s.get("file_name", "")))[-1],
                      "line": s.get("line_start")} for s in message.get("spans", []) if s.get("is_primary")]
            diagnostics.append({"level": message.get("level"), "message": str(message.get("message", ""))[:4096], "spans": spans})
        # Explicitly omit rendered source excerpts, build-script env/link values,
        # output directories, arbitrary future record fields, and credential data.
    return {"build_finished_success": success, "observed_external_package_ids": sorted(packages),
            "observed_external_artifacts": [{"package_id": package, "features": list(features), "target_kinds": list(kinds)} for package, features, kinds in sorted(artifacts)],
            "external_feature_metadata_complete": feature_metadata_complete and bool(artifacts),
            "compiler_diagnostics": diagnostics, "malformed_line_count": malformed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("build-log", "lockfile", "gui", "cli", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-fA-F]{40}", args.source_sha):
        parser.error("source SHA must be exactly 40 hex characters")
    lines = args.build_log.read_text(encoding="utf-8-sig").splitlines() if args.build_log.is_file() else []
    result = {"schema": 1, "source_sha": args.source_sha, "target": args.target,
              "lockfile": file_fact(args.lockfile), "gui": file_fact(args.gui), "cli": file_fact(args.cli),
              **reduce_messages(lines)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
