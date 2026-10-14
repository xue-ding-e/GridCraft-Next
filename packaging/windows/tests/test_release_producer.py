"""Synthetic staging tests; no build, installation, credential or API access."""
import copy
import hashlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import release_bundle as bundle
import stage_reviewed_toolchain as staging
from test_public_toolchain_evidence import samples
from test_release_bundle import FULL_VERSION, create_fixture


class ProducerStagingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        output, evidence, _ = create_fixture(self.root)
        self.paths = {name: self.root / name for name in ("selection", "links", "toolchain", "legal")}
        for directory in self.paths.values(): directory.mkdir()
        (self.paths["toolchain"] / "legal").mkdir()
        self.gui, self.cli, self.inventory = (self.root / name for name in ("gridcraft.exe", "gridcraft-cli.exe", "inventory.json"))
        self.gui.write_bytes(b"SYNTHETIC GUI; NOT EXECUTABLE")
        self.cli.write_bytes(b"SYNTHETIC CLI; NOT EXECUTABLE")
        with zipfile.ZipFile(output / bundle.package_names(FULL_VERSION)[1]) as archive:
            name = next(name for name in archive.namelist() if name.endswith("/THIRD_PARTY_INVENTORY.json"))
            self.inventory.write_bytes(archive.read(name))
        schema = samples()
        self.chain = {"selection": {**schema["selection-summary.json"], **evidence["toolchain"]["selection"]},
                      "links": {**schema["link-summary.json"], **evidence["toolchain"]["links"]},
                      "toolchain": {**schema["toolchain-summary.json"], **evidence["toolchain"]["toolchain"]}}
        self.chain["selection"]["unresolved"] = ["LEGAL_TERMS_NOT_REVIEWED", "FINAL_LINK_INPUTS_NOT_YET_PROVEN"]
        self.original = b"SYNTHETIC standard-library original notice\r\nPreserve these bytes.\r\n"
        digest = bundle.sha256(self.original)
        notice = self.chain["toolchain"]["files"][-1]
        notice.update({"sha256": digest, "bytes": len(self.original), "legal_file": digest[:16] + "-COPYRIGHT-library.html"})
        (self.paths["toolchain"] / "legal" / notice["legal_file"]).write_bytes(self.original)
        self.original_path = self.paths["toolchain"] / "legal" / notice["legal_file"]
        for key, filename in (("selection", "selection-summary.json"), ("links", "link-summary.json"), ("toolchain", "toolchain-summary.json")):
            bundle.write_json(self.paths[key] / filename, self.chain[key])
        self.review_path = self.root / "packaging/windows/windows-release-review.json"
        self.review = bundle.read_json(self.review_path)
        self.review["toolchain_review_fingerprint"] = bundle.toolchain_fingerprint(self.chain)
        self.review["required_toolchain_notices"] = {"toolchain-rust-std-original.txt": digest}
        bundle.write_json(self.review_path, self.review)

    def stage(self):
        return staging.stage_originals(self.root, self.inventory, self.paths["legal"], self.paths["selection"], self.paths["links"], self.paths["toolchain"], self.gui, self.cli)

    def test_actual_public_schema_and_original_bytes_stage_before_package(self):
        raw = {key: (self.paths[key] / name).read_bytes() for key, name in (("selection", "selection-summary.json"), ("links", "link-summary.json"), ("toolchain", "toolchain-summary.json"))}
        self.assertEqual(self.stage(), 1)
        self.assertEqual((self.paths["legal"] / "toolchain-rust-std-original.txt").read_bytes(), self.original)
        self.assertEqual(self.original_path.read_bytes(), self.original)
        for key, name in (("selection", "selection-summary.json"), ("links", "link-summary.json"), ("toolchain", "toolchain-summary.json")):
            self.assertEqual((self.paths[key] / name).read_bytes(), raw[key])
        self.assertEqual(self.stage(), 1)  # Identical staged bytes are not overwritten.

    def test_missing_either_review_stages_nothing(self):
        for path in (self.review_path, self.root / "packaging/windows/license-review.json"):
            original = path.read_bytes()
            path.unlink()
            with self.assertRaises(ValueError): self.stage()
            self.assertEqual(list(self.paths["legal"].iterdir()), [])
            path.write_bytes(original)

    def test_wrong_fingerprint_stages_nothing(self):
        for key in ("inventory_review_fingerprint", "toolchain_review_fingerprint"):
            bundle.write_json(self.review_path, {**self.review, key: "f" * 64})
            with self.assertRaises(ValueError): self.stage()
            self.assertEqual(list(self.paths["legal"].iterdir()), [])

    def test_changed_original_or_final_executable_stays_unapproved(self):
        self.original_path.write_bytes(self.original + b"changed")
        with self.assertRaises(ValueError): self.stage()
        self.assertEqual(list(self.paths["legal"].iterdir()), [])
        self.original_path.write_bytes(self.original)
        self.gui.write_bytes(b"changed executable")
        with self.assertRaisesRegex(ValueError, "final linker bytes"): self.stage()
        self.assertEqual(list(self.paths["legal"].iterdir()), [])

    def test_unknown_issue_not_discharged_by_matching_review(self):
        self.chain["selection"]["unresolved"].append("TOOLCHAIN_SELECTION_FAILED")
        bundle.write_json(self.paths["selection"] / "selection-summary.json", self.chain["selection"])
        with self.assertRaisesRegex(ValueError, "unresolved selection/link"): self.stage()

    def test_inventory_content_cannot_be_relabelled_with_old_fingerprint(self):
        inventory = bundle.read_json(self.inventory)
        inventory["lock_sha256"] = "f" * 64
        bundle.write_json(self.inventory, inventory)
        with self.assertRaisesRegex(ValueError, "Cargo inventory"): self.stage()

    def test_existing_different_notice_and_case_collision_are_never_overwritten(self):
        destination = self.paths["legal"] / "toolchain-rust-std-original.txt"
        destination.write_bytes(b"existing original")
        with self.assertRaisesRegex(ValueError, "overwrite"): self.stage()
        self.assertEqual(destination.read_bytes(), b"existing original")
        destination.rename(destination.with_name(destination.name.upper()))
        with self.assertRaisesRegex(ValueError, "case-insensitive"): self.stage()

    def test_cli_missing_review_reports_expected_unapproved_without_raw_paths(self):
        self.review_path.unlink()
        args = [sys.executable, str(Path(staging.__file__)), "--root", str(self.root), "--inventory", str(self.inventory),
                "--legal-directory", str(self.paths["legal"]), "--selection", str(self.paths["selection"]),
                "--links", str(self.paths["links"]), "--toolchain", str(self.paths["toolchain"]), "--gui", str(self.gui), "--cli", str(self.cli)]
        result = subprocess.run(args, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("TOOLCHAIN_REVIEW_NOT_APPROVED", result.stderr)
        self.assertNotIn(str(self.root), result.stdout + result.stderr)


class ProducerSourceContractTests(unittest.TestCase):
    def test_collection_and_notices_precede_both_package_formats(self):
        scripts = Path(__file__).parents[1]
        package = (scripts / "package.ps1").read_text()
        cargo = package.index("cargo build --release --locked")
        collect = package.index("'collect-build-toolchain-evidence.ps1'")
        stage = package.index("'stage_reviewed_toolchain.py'")
        legal_manifest = package.index("Write-PackageLegalManifest $LegalDir")
        wix = package.index("wix build (Join-Path")
        portable = package.index("Compress-Archive -Path $Portable")
        self.assertLess(cargo, collect)
        self.assertLess(collect, stage)
        self.assertLess(stage, legal_manifest)
        self.assertLess(legal_manifest, wix)
        self.assertLess(wix, portable)
        self.assertIn("$DistributionApproved = ($LegalExit -eq 0 -and $ToolchainReviewApproved)", package)
        self.assertIn("$ReviewedOrValidation -and -not $DistributionApproved", package)
        self.assertIn("if ($InternalValidation)", package)
        self.assertIn("distribution_approved = ($DistributionApproved -and -not $InternalValidation)", package)

    def test_finally_reuses_source_binary_bound_receipt(self):
        scripts = Path(__file__).parents[1]
        helper = (scripts / "collect-build-toolchain-evidence.ps1").read_text()
        harness = (scripts / "build-with-toolchain-evidence.ps1").read_text()
        self.assertIn("-Arch x64 -ReviewedOrValidation", harness)
        self.assertNotIn("collect-installed-toolchain.ps1", harness)
        self.assertLess(harness.index("finally {"), harness.index("'collect-build-toolchain-evidence.ps1'"))
        self.assertLess(helper.index("if (Test-Path -LiteralPath $state)"), helper.index("'collect-installed-toolchain.ps1'"))
        for identity in ("$saved.source_sha -ceq $SourceSha", "$saved.gui_sha256 -ceq $result.gui_sha256", "$saved.cli_sha256 -ceq $result.cli_sha256"):
            self.assertIn(identity, helper)
        self.assertIn("$captureStatus -eq 0", helper)
        self.assertIn("$technicalIssues.Count -eq 0", helper)

    def test_artifact_upload_waits_for_existing_windows_gates_not_own_aggregate(self):
        workflow = (Path(__file__).parents[3] / ".github/workflows/ci.yml").read_text()
        self.assertIn("needs: [gate, windows_tests, windows_package]", workflow)
        self.assertIn("steps.eligibility.outcome == 'success' && steps.build_summary.outcome == 'success'", workflow)
        self.assertIn("steps.release_bundle.outcome == 'success'", workflow)
        self.assertIn("windows-release-${{ github.sha }}-attempt-${{ github.run_attempt }}", workflow)
        self.assertIn("overwrite: false", workflow)
        self.assertIn("path: ${{ runner.temp }}/gridcraft-next-release-bundle/*", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("needs: [validation_result]", workflow)


if __name__ == "__main__":
    unittest.main()
