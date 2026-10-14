#!/usr/bin/env python3
"""Stage exact approved toolchain originals before WiX/portable packaging.

No review is generated and no toolchain is installed. Exit 2 is an unapproved
candidate: the CI wrapper may still build an explicitly internal-only package
for native diagnostics, but its final distribution gate must remain red.
"""
import argparse
import sys
from pathlib import Path

from release_bundle import (TARGET, canonical, check_toolchain_results, file_fact,
                            load_public_toolchain, read_json, require,
                            review_toolchain, sha256)


def stage_originals(root, inventory_path, legal_directory, selection, links, toolchain, gui, cli):
    inventory = read_json(inventory_path)
    fingerprint = inventory.pop("review_fingerprint")
    require(inventory["schema"] == 2 and inventory["target"] == TARGET and inventory["problems"] == []
            and sha256(canonical(inventory)) == fingerprint
            and inventory["lock_sha256"] == file_fact(Path(root) / "Cargo.lock")["sha256"], "exact successful Cargo inventory required")
    chain = load_public_toolchain(selection, links, toolchain)
    review = review_toolchain(root, fingerprint, chain)
    check_toolchain_results(chain)
    binaries = {"gridcraft.exe": gui, "gridcraft-cli.exe": cli}
    for executable in chain["links"]["executables"]:
        require(file_fact(binaries[executable["name"]])["sha256"] == executable["sha256"], "final linker bytes changed before packaging")
    destination = Path(legal_directory)
    require(destination.is_dir() and not destination.is_symlink(), "package legal directory required")
    originals = {row["sha256"]: Path(toolchain) / "legal" / row["legal_file"]
                 for row in chain["toolchain"]["files"] if row["legal_file"] is not None}
    planned = []
    names = {path.name.casefold(): path.name for path in destination.iterdir()}
    # Validate the complete copy plan before writing any package notice. Original
    # bytes are never newline-normalized, renamed in evidence, or overwritten.
    for name, digest in sorted(review["required_toolchain_notices"].items()):
        require(name.casefold() not in names or names[name.casefold()] == name, "case-insensitive notice collision")
        names[name.casefold()] = name
        original = originals[digest]
        require(file_fact(original)["sha256"] == digest, "reviewed original changed")
        target = destination / name
        require(not target.is_symlink(), "notice destination symlink forbidden")
        if target.exists():
            require(file_fact(target)["sha256"] == digest, "notice would overwrite a different legal file")
        else:
            planned.append((original, target, digest))
    for original, target, digest in planned:
        # Exclusive creation prevents a race from replacing existing legal text.
        with original.open("rb") as source, target.open("xb") as output:
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
        require(file_fact(target)["sha256"] == digest, "staged toolchain notice hash mismatch")
    return len(review["required_toolchain_notices"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    for name in ("inventory", "legal-directory", "selection", "links", "toolchain", "gui", "cli"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        count = stage_originals(args.root, args.inventory, args.legal_directory, args.selection, args.links, args.toolchain, args.gui, args.cli)
    except (ValueError, KeyError, TypeError, OSError):
        # Missing/stale approval or incomplete originals are diagnostic-only.
        # Do not leak source paths or change the original collector's statuses.
        print("TOOLCHAIN_REVIEW_NOT_APPROVED: exact review, linked inputs and downstream originals are required.", file=sys.stderr)
        return 2
    print(f"Exact toolchain review and {count} downstream originals verified before packaging.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
