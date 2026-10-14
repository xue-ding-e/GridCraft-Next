import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("summarize_build", Path(__file__).parents[1] / "summarize_build.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BuildSummaryTests(unittest.TestCase):
    def test_omits_env_links_rendered_source_and_absolute_paths(self):
        messages = [
            {"reason": "build-script-executed", "env": [["SYNTHETIC_TEST_SECRET", "do-not-export"]], "linked_paths": ["private-path"]},
            {"reason": "compiler-artifact", "package_id": "path+file:///private/path#gridcraft@0.6.1-next.0"},
            {"reason": "compiler-artifact", "package_id": "registry+https://github.com/rust-lang/crates.io-index#example@1.0.0", "filenames": ["private-output"]},
            {"reason": "compiler-message", "message": {"level": "error", "message": "synthetic failure", "rendered": "private source excerpt", "spans": [{"file_name": "C:\\private\\source.rs", "line_start": 7, "is_primary": True, "text": ["private line"]}]}},
            {"reason": "build-finished", "success": False},
        ]
        reduced = MODULE.reduce_messages([json.dumps(m) for m in messages])
        text = json.dumps(reduced)
        self.assertNotIn("private", text)
        self.assertNotIn("do-not-export", text)
        self.assertEqual(reduced["compiler_diagnostics"][0]["spans"], [{"file": "source.rs", "line": 7}])
        self.assertFalse(reduced["build_finished_success"])

    def test_missing_and_malformed_build_are_not_success(self):
        self.assertIsNone(MODULE.reduce_messages([])["build_finished_success"])
        result = MODULE.reduce_messages(["not-json-private-content"])
        self.assertEqual(result["malformed_line_count"], 1)
        self.assertNotIn("private-content", json.dumps(result))

    def test_file_hash_and_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "synthetic.bin"
            self.assertEqual(MODULE.file_fact(p), {"present": False})
            p.write_bytes(b"synthetic")
            result = MODULE.file_fact(p)
            self.assertTrue(result["present"])
            self.assertEqual(result["bytes"], 9)
            self.assertEqual(len(result["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
