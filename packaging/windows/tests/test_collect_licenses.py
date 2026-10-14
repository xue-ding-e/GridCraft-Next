import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("collect_licenses", Path(__file__).parents[1] / "collect-licenses.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


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
        self.messages = [{"reason": "compiler-artifact", "package_id": "gui", "target": {"name": "gridcraft"}, "executable": "gridcraft.exe"}, {"reason": "compiler-artifact", "package_id": "cli", "target": {"name": "gridcraft-cli"}, "executable": "gridcraft-cli.exe"}, {"reason": "compiler-artifact", "package_id": self.pid}, {"reason": "build-finished", "success": True}]
        self.lock = f'[[package]]\nname = "example"\nversion = "1.0.0"\nsource = "{self.source}"\nchecksum = "synthetic-checksum"\n'.encode()
        (self.package / "LICENSE-MIT").write_bytes(b"Synthetic original legal text\r\n")

    def collect(self, output="output", supplements=None):
        return MODULE.collect(self.meta, self.messages, self.lock, self.root / output, supplements)

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

    def test_flat_names_do_not_collide_for_nested_notices(self):
        nested = self.package / "vendor"
        nested.mkdir()
        (nested / "LICENSE-MIT").write_text("Different embedded attribution")
        records = self.collect()["packages"][0]["files"]
        self.assertEqual(len(records), 2)
        self.assertEqual(len({r["name"] for r in records}), 2)


if __name__ == "__main__":
    unittest.main()
