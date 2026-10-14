import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

SCRIPT = Path(__file__).parents[1] / "check_validation_result.py"
SPEC = importlib.util.spec_from_file_location("check_validation_result", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ValidationResultTests(unittest.TestCase):
    def test_all_three_layers_must_succeed(self):
        self.assertEqual(MODULE.failed_layers("success", "success", "success"), [])
        for index in range(3):
            for outcome in ("failure", "cancelled", "skipped", "", None, "in_progress", "timed_out", "action_required"):
                with self.subTest(layer=index, outcome=outcome):
                    results = ["success"] * 3
                    results[index] = outcome
                    self.assertEqual(len(MODULE.failed_layers(*results)), 1)

    def test_linux_failure_cannot_be_hidden_by_windows_success(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--linux", "failure", "--windows-tests", "success", "--windows-native", "success"], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Linux complete quality gate", result.stderr)
        self.assertNotIn("All source", result.stdout)

    def test_native_evidence_has_an_independent_required_gate(self):
        root = SCRIPT.parents[2]
        workflow = (root / ".github/workflows/ci.yml").read_text()
        harness = (SCRIPT.parent / "build-with-toolchain-evidence.ps1").read_text()
        collection = (SCRIPT.parent / "collect-build-toolchain-evidence.ps1").read_text()
        self.assertIn("needs: [gate, windows_tests, windows_package]", workflow)
        self.assertIn("TOOLCHAIN_OUTCOME: ${{ steps.toolchain_evidence.outcome }}", workflow)
        self.assertIn("$env:NATIVE_OUTCOME, $env:TOOLCHAIN_OUTCOME", workflow)
        self.assertIn("$env:TOOLCHAIN_CAPTURE_COMPLETE -ne 'true'", workflow)
        self.assertIn("$captureComplete = $false", harness)
        self.assertIn("collect-build-toolchain-evidence.ps1", harness)
        self.assertIn("$captureStatus -eq 0", collection)
        self.assertIn("$technicalIssues.Count -eq 0", collection)
        self.assertNotIn("continue-on-error:", workflow)

    def test_link_probe_clears_expected_failure_only_after_assertions(self):
        probe = (SCRIPT.parent / "test-msvc-link-capture.ps1").read_text()
        self.assertIn("if($capturedFailure -ne $expectedFailure){throw 'MSVC_PROBE_FAILURE_EXIT_DRIFT'}", probe)
        self.assertLess(probe.rindex("if($failed){throw $failed}"), probe.rindex("$global:LASTEXITCODE = 0"))
        self.assertTrue(probe.rstrip().endswith("$global:LASTEXITCODE = 0"))

    def test_missing_job_result_is_not_a_pass(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--windows-tests", "success", "--windows-native", "success"], capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
