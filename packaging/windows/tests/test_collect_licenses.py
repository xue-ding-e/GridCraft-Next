import importlib.util
import copy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("collect_licenses", Path(__file__).parents[1] / "collect-licenses.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RepositorySupplementTests(unittest.TestCase):
    def test_checked_in_supplements_match_reviewed_original_bytes(self):
        # Runs in the cheap Windows script job, before the real Rust build.
        root = Path(__file__).parents[1] / "license-supplements"
        config = json.loads((root / "supplements.json").read_text(encoding="utf-8"))
        checked = set()
        for package_id, items in config.items():
            for item in items:
                with self.subTest(package=package_id, path=item["path"]):
                    self.assertRegex(item["origin_url"], r"\Ahttps://raw\.githubusercontent\.com/[^/]+/[^/]+/[0-9a-f]{40}/[^?#]+\Z")
                    data = MODULE.safe_file(root, item["path"])
                    self.assertEqual(MODULE.digest(data), item["sha256"])
                    checked.add(item["path"])
        files = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and path.name != "supplements.json"}
        self.assertTrue(checked)
        self.assertEqual(checked, files)


class LicenseCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "package"
        self.package.mkdir()
        self.source = "registry+https://github.com/rust-lang/crates.io-index"
        self.pid = self.source + "#example@1.0.0"
        self.dep = {"id": self.pid, "name": "example", "version": "1.0.0", "source": self.source, "manifest_path": str(self.package / "Cargo.toml"), "license": "MIT OR Apache-2.0", "license_file": None, "repository": "https://example.invalid/source", "authors": ["Synthetic Test Author"]}
        self.meta = {"packages": [self.dep, {"id": "gui"}, {"id": "cli"}], "workspace_members": ["gui", "cli"]}
        self.messages = [{"reason": "compiler-artifact", "package_id": "gui", "target": {"name": "gridcraft", "kind": ["bin"]}, "features": [], "executable": "gridcraft.exe"}, {"reason": "compiler-artifact", "package_id": "cli", "target": {"name": "gridcraft-cli", "kind": ["bin"]}, "features": [], "executable": "gridcraft-cli.exe"}, {"reason": "compiler-artifact", "package_id": self.pid, "features": [], "target": {"name": "example", "kind": ["lib"]}}, {"reason": "build-finished", "success": True}]
        self.target = "x86_64-pc-windows-msvc"
        self.lock = f'[[package]]\nname = "example"\nversion = "1.0.0"\nsource = "{self.source}"\nchecksum = "synthetic-checksum"\n'.encode()
        (self.package / "LICENSE-MIT").write_bytes(b"Synthetic original legal text\r\n")

    def collect(self, output="output", supplements=None):
        return MODULE.collect(self.meta, self.messages, self.lock, self.root / output, self.target, supplements)

    def test_original_bytes_and_metadata_are_preserved(self):
        result = self.collect()
        self.assertEqual(result["problems"], [])
        record = result["packages"][0]
        self.assertEqual(record["license"], "MIT OR Apache-2.0")
        self.assertEqual(record["registry_checksum"], "synthetic-checksum")
        legal = record["files"][0]
        self.assertEqual((self.root / "output" / legal["name"]).read_bytes(), b"Synthetic original legal text\r\n")
        self.assertEqual(legal["sha256"], MODULE.digest(b"Synthetic original legal text\r\n"))

    def test_fingerprint_is_repeatable_and_changes_with_legal_bytes(self):
        first = self.collect()
        second = self.collect("second")
        self.assertEqual(first["review_fingerprint"], second["review_fingerprint"])
        (self.package / "LICENSE-MIT").write_bytes(b"Updated synthetic attribution")
        self.assertNotEqual(first["review_fingerprint"], self.collect("third")["review_fingerprint"])

    def test_features_change_fingerprint(self):
        first = self.collect()
        self.messages[2]["features"] = ["agl"]
        second = self.collect("changed")
        self.assertNotEqual(first["review_fingerprint"], second["review_fingerprint"])
        self.assertEqual(second["packages"][0]["compiler_artifacts"], [{"features": ["agl"], "target_kind": ["lib"]}])

    def test_target_kind_changes_fingerprint(self):
        first = self.collect()
        self.messages[2]["target"]["kind"] = ["proc-macro"]
        self.assertNotEqual(first["review_fingerprint"], self.collect("changed")["review_fingerprint"])

    def test_explicit_target_changes_fingerprint(self):
        first = self.collect()
        self.target = "aarch64-pc-windows-msvc"
        second = self.collect("changed")
        self.assertEqual(second["target"], self.target)
        self.assertNotEqual(first["review_fingerprint"], second["review_fingerprint"])

    def test_artifact_instances_preserved_and_order_duplicates_normalized(self):
        self.messages[2]["features"] = ["z", "a", "a"]
        self.messages[2]["target"]["kind"] = ["rlib", "lib", "lib"]
        other = copy.deepcopy(self.messages[2])
        other.update(features=["b"])
        other["target"]["kind"] = ["custom-build"]
        self.messages.insert(-1, other)
        self.messages.insert(-1, {"reason": "build-script-executed", "package_id": self.pid})
        first = self.collect()
        self.assertEqual(first["packages"][0]["compiler_artifacts"], [
            {"features": ["a", "z"], "target_kind": ["lib", "rlib"]},
            {"features": ["b"], "target_kind": ["custom-build"]},
        ])
        self.messages[2]["features"] = ["a", "z"]
        self.messages[2]["target"]["kind"] = ["lib", "rlib"]
        self.messages.insert(-1, copy.deepcopy(self.messages[2]))
        self.messages = list(reversed(self.messages[:-1])) + self.messages[-1:]
        self.assertEqual(first["review_fingerprint"], self.collect("reordered")["review_fingerprint"])

    def test_missing_or_invalid_features_fail_before_output(self):
        for value in (None, "agl", {}, [None], [1], [""], ["bad\nfeature"], [" padded"]):
            with self.subTest(value=value):
                self.messages[2]["features"] = value
                with self.assertRaisesRegex(MODULE.CollectionError, "features metadata"):
                    self.collect()
                self.assertFalse((self.root / "output").exists())
        del self.messages[2]["features"]
        with self.assertRaisesRegex(MODULE.CollectionError, "features metadata"):
            self.collect()

    def test_missing_or_invalid_kind_fail_before_output(self):
        for value in (None, [], "lib", {}, [None], [1], [""], ["lib\n"], ["unrecognized-kind"]):
            with self.subTest(value=value):
                self.messages[2]["target"]["kind"] = value
                with self.assertRaisesRegex(MODULE.CollectionError, "target.kind"):
                    self.collect()
                self.assertFalse((self.root / "output").exists())
        del self.messages[2]["target"]["kind"]
        with self.assertRaisesRegex(MODULE.CollectionError, "target.kind"):
            self.collect()

    def test_missing_or_invalid_artifact_target_fails_closed(self):
        for value in (None, [], "lib"):
            with self.subTest(value=value):
                self.messages[2]["target"] = value
                with self.assertRaisesRegex(MODULE.CollectionError, "target metadata"):
                    self.collect()

    def test_build_script_only_input_lacks_required_artifact_scope(self):
        self.messages[2] = {"reason": "build-script-executed", "package_id": self.pid}
        with self.assertRaisesRegex(MODULE.CollectionError, "lacks compiler-artifact scope"):
            self.collect()

    def test_workspace_root_features_are_bound_without_paths(self):
        first = self.collect()
        self.messages[0]["features"] = ["changed-root-feature"]
        second = self.collect("changed")
        self.assertNotEqual(first["review_fingerprint"], second["review_fingerprint"])
        self.assertEqual(second["root_artifacts"][0]["name"], "gridcraft")
        self.assertEqual(second["root_artifacts"][0]["features"], ["changed-root-feature"])
        self.assertNotIn("gridcraft.exe", json.dumps(second))

    def test_missing_or_invalid_explicit_target_fails_before_output(self):
        for value in (None, "", "windows", " x86_64-pc-windows-msvc", "x86_64-pc-windows-msvc\n", "../secret/path", 1):
            with self.subTest(value=value):
                self.target = value
                with self.assertRaisesRegex(MODULE.CollectionError, "target triple"):
                    self.collect()
                self.assertFalse((self.root / "output").exists())

    def test_cli_requires_explicit_target(self):
        argv = ["collect-licenses.py", "--metadata", "not-read.json", "--build-messages", "not-read.jsonl", "--lockfile", "not-read.lock", "--output", str(self.root / "output")]
        with patch("sys.argv", argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            MODULE.main()
        self.assertEqual(error.exception.code, 2)
        self.assertFalse((self.root / "output").exists())

    def test_unobserved_workspace_packages_are_not_added(self):
        self.meta["packages"].append({"id": "unused"})
        self.assertEqual(len(self.collect()["packages"]), 1)

    def test_missing_original_legal_text_is_blocking(self):
        (self.package / "LICENSE-MIT").unlink()
        self.assertIn("no original license", " ".join(self.collect()["problems"]))

    def test_cargo_failure_or_missing_binary_is_rejected(self):
        self.messages[-1]["success"] = False
        with self.assertRaises(MODULE.CollectionError):
            self.collect()
        self.messages.pop()
        with self.assertRaises(MODULE.CollectionError):
            self.collect()
        self.messages = self.messages[1:] + [{"reason": "build-finished", "success": True}]
        with self.assertRaises(MODULE.CollectionError):
            self.collect()

    def test_stale_output_is_rejected(self):
        self.collect()
        with self.assertRaises(MODULE.CollectionError):
            self.collect()

    def test_escape_symlink_and_binary_are_rejected(self):
        outside = self.root / "outside.txt"
        outside.write_text("Do not copy unrelated data")
        with self.assertRaises(MODULE.CollectionError):
            MODULE.safe_file(self.package, "../outside.txt")
        link = self.package / "NOTICE"
        try:
            link.symlink_to(outside)
        except OSError:
            pass  # Windows developer-mode policy may prevent fixture creation.
        else:
            with self.assertRaises(MODULE.CollectionError):
                MODULE.safe_file(self.package, "NOTICE")
        (self.package / "COPYING").write_bytes(b"bad\0binary")
        with self.assertRaises(MODULE.CollectionError):
            MODULE.safe_file(self.package, "COPYING")

    def test_supplements_require_source_hash_and_origin(self):
        path = self.root / "extra-license.txt"
        path.write_bytes(b"Synthetic supplemental text")
        item = {"source": self.source, "path": str(path), "sha256": MODULE.digest(path.read_bytes()), "label": "LICENSE-APACHE", "origin_url": "https://example.invalid/fixed-sha/LICENSE-APACHE"}
        result = self.collect(supplements={self.pid: [item]})
        self.assertEqual(len(result["packages"][0]["files"]), 2)
        item["sha256"] = "0" * 64
        with self.assertRaises(MODULE.CollectionError):
            self.collect("bad", {self.pid: [item]})

    def test_lock_source_mismatch_blocks_distribution(self):
        self.lock = self.lock.replace(b"1.0.0", b"2.0.0")
        self.assertIn("Cargo.lock", " ".join(self.collect()["problems"]))

    def test_supplement_newline_conversion_is_rejected(self):
        path = self.root / "extra-license.txt"
        original = b"Synthetic supplemental text\nOriginal byte sequence\n"
        item = {"source": self.source, "path": str(path), "sha256": MODULE.digest(original), "label": "LICENSE-APACHE", "origin_url": "https://example.invalid/fixed-sha/LICENSE-APACHE"}
        path.write_bytes(original.replace(b"\n", b"\r\n"))
        with self.assertRaisesRegex(MODULE.CollectionError, "unverified supplement"):
            self.collect("rewritten", {self.pid: [item]})
        path.write_bytes(original)
        self.assertEqual(len(self.collect("original", {self.pid: [item]})["packages"][0]["files"]), 2)

    def test_flat_names_do_not_collide_for_nested_notices(self):
        nested = self.package / "vendor"
        nested.mkdir()
        (nested / "LICENSE-MIT").write_text("Different embedded attribution")
        records = self.collect()["packages"][0]["files"]
        self.assertEqual(len(records), 2)
        self.assertEqual(len({r["name"] for r in records}), 2)

    def test_original_authors_and_contributors_are_preserved(self):
        originals = {"AUTHORS": b"Synthetic Original Author\n", "CONTRIBUTORS.md": b"Synthetic Contributor\r\n"}
        for name, data in originals.items():
            (self.package / name).write_bytes(data)
        (self.package / "authors.py").write_text("# Not a legal notice")
        records = self.collect()["packages"][0]["files"]
        self.assertEqual(len(records), 3)
        for name, data in originals.items():
            record = next(row for row in records if row["original_path"] == name)
            self.assertEqual((self.root / "output" / record["name"]).read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
