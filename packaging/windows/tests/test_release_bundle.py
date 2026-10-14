"""Synthetic negative/contract tests only; these fixtures are not real packages."""
import copy
import importlib.util
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).parents[1]
sys.path.insert(0, str(SCRIPTS))
import release_bundle as bundle
import publish_release as release

SOURCE = "a" * 40
REPOSITORY = "owner/GridCraft-Next"
FULL_VERSION = "0.6.2-next.1"


def create_fixture(root):
    """In-memory-style tiny fixture; no installed application or legal approval."""
    (root / "packaging/windows").mkdir(parents=True)
    (root / "Cargo.toml").write_text(f'[workspace.package]\nversion = "{FULL_VERSION}"\n')
    (root / "Cargo.lock").write_text("# SYNTHETIC TEST ONLY\n")
    output = root / "bundle"
    output.mkdir()
    inventory = {"schema": 2, "target": bundle.TARGET, "problems": [], "lock_sha256": bundle.file_fact(root / "Cargo.lock")["sha256"]}
    fingerprint = bundle.sha256(bundle.canonical(inventory))
    inventory["review_fingerprint"] = fingerprint
    legal_files = {name: b"SYNTHETIC TEST ONLY" for name in ("LICENSE-MIT", "LICENSE-APACHE", "NOTICE", "ATTRIBUTION.md", "app-icon-LICENSE.txt", "LICENSE-brand.txt")}
    legal_files["THIRD_PARTY_INVENTORY.json"] = bundle.canonical(inventory)
    legal_files["toolchain-rust-std-original.txt"] = b"SYNTHETIC STANDARD-LIBRARY NOTICE"
    gui, cli = b"SYNTHETIC GUI; NOT EXECUTABLE", b"SYNTHETIC CLI; NOT EXECUTABLE"
    names = bundle.package_names(FULL_VERSION)
    (output / names[0]).write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"SYNTHETIC; NOT AN MSI")
    with zipfile.ZipFile(output / names[1], "w") as archive:
        for name, data in {**legal_files, "gridcraft-next.exe": gui, "gridcraft-next-cli.exe": cli}.items():
            archive.writestr(f"gridcraft-next-{FULL_VERSION}-windows-x64-portable/{name}", data)
    facts = {name: bundle.file_fact(output / name) for name in names}
    toolchain = {
        "toolchain": {"schema": 2, "target": bundle.TARGET, "evidence_only": True, "distribution_approved": False,
                      "missing_files": [], "unresolved": ["MICROSOFT_TERMS_APPLICABILITY_NOT_REVIEWED"],
                      "files": [{"role": role, "name": name, "sha256": "1" * 64, "bytes": 1, "file_version": None, "legal_file": None}
                                for role, name in (("rust-std-target-input", "std.rlib"), ("observed-linked-runtime-input", "libcmt.lib"), ("final-link-evidence", "final-link.json"))]},
        "selection": {"unresolved": [], "applied_to_process": True, "selected_files": [{"name": "link.exe", "sha256": "2" * 64}]},
        "links": {"unresolved": [], "executables": [
            {"name": name, "sha256": bundle.sha256(data), "linker_name": "link.exe", "linker_sha256": "2" * 64,
             "runtime_libraries": [{"name": "libcmt.lib", "sha256": "1" * 64, "bytes": 1}], "unresolved": []}
            for name, data in (("gridcraft.exe", gui), ("gridcraft-cli.exe", cli))]},
    }
    std_notice_hash = bundle.sha256(legal_files["toolchain-rust-std-original.txt"])
    toolchain["toolchain"]["files"].append({"role": "rust-std-original-notices", "name": "COPYRIGHT-library.html", "sha256": std_notice_hash,
                                           "bytes": len(legal_files["toolchain-rust-std-original.txt"]), "file_version": None,
                                           "legal_file": std_notice_hash[:16] + "-COPYRIGHT-library.html"})
    review = {"schema": 1, "status": "approved", "inventory_review_fingerprint": fingerprint, "toolchain_review_fingerprint": bundle.toolchain_fingerprint(toolchain),
              "required_toolchain_notices": {"toolchain-rust-std-original.txt": std_notice_hash}}
    bundle.write_json(root / "packaging/windows/windows-release-review.json", review)
    bundle.write_json(root / "packaging/windows/license-review.json", {"reviewed_fingerprint": fingerprint})
    evidence = {
        "schema": 1, "source_sha": SOURCE, "full_version": FULL_VERSION, "msi_version": "0.6.513",
        "legal_status": {"source_sha": SOURCE, "full_version": FULL_VERSION, "msi_version": "0.6.513", "distribution_approved": True,
                         "legal_review_approved": True, "validation_only": False, "collector_exit": 0, "inventory_sha256": bundle.sha256(legal_files["THIRD_PARTY_INVENTORY.json"])},
        "inventory_review_fingerprint": fingerprint, "toolchain": toolchain,
        "build": {"source_sha": SOURCE, "target": bundle.TARGET, "build_finished_success": True, "external_feature_metadata_complete": True,
                  "malformed_line_count": 0, "lockfile": {"present": True, **bundle.file_fact(root / "Cargo.lock")},
                  "gui": {"present": True, "bytes": len(gui), "sha256": bundle.sha256(gui)}, "cli": {"present": True, "bytes": len(cli), "sha256": bundle.sha256(cli)}},
        "native": {"status": "passed", "cleanup_errors": [], "checks": sorted(bundle.REQUIRED_CHECKS), "inputs": facts,
                   "legal_payload": [{"Name": name, "Sha256": bundle.sha256(data)} for name, data in legal_files.items()],
                   "pe": [{"name": name, "signature": "NotSigned", "machine": 0x8664, "subsystem": subsystem}
                          for name, subsystem in (("gridcraft-next.exe", 2), ("gridcraft-next-cli.exe", 3))]},
    }
    bundle.write_json(output / "RELEASE-EVIDENCE.json", evidence)
    manifest = {"schema": 1, "repository": REPOSITORY, "source_sha": SOURCE, "full_version": FULL_VERSION, "msi_version": "0.6.513", "target": bundle.TARGET,
                "ci": {"run_id": 123, "run_attempt": 2, "workflow": bundle.WORKFLOW}, "packages": facts,
                "evidence": bundle.file_fact(output / "RELEASE-EVIDENCE.json"), "signing": "unsigned"}
    bundle.write_json(output / "release-manifest.json", manifest)
    refresh_sums(output)
    return output, evidence, manifest


def refresh_sums(output):
    # WindowsPath ordering folds case; the format orders exact filename strings.
    # Use explicit LF bytes too, independent of the fixture writer's host OS.
    paths = sorted(output.iterdir(), key=lambda path: path.name)
    (output / "SHA256SUMS").write_text(
        "".join(f"{bundle.file_fact(path)['sha256']}  {path.name}\n" for path in paths if path.name != "SHA256SUMS"),
        encoding="utf-8", newline="\n")


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output, self.evidence, self.manifest = create_fixture(self.root)

    def validate(self):
        return bundle.validate_bundle(self.output, self.root, SOURCE, REPOSITORY, 123, 2)

    def rewrite_evidence(self):
        bundle.write_json(self.output / "RELEASE-EVIDENCE.json", self.evidence)
        self.manifest["evidence"] = bundle.file_fact(self.output / "RELEASE-EVIDENCE.json")
        bundle.write_json(self.output / "release-manifest.json", self.manifest)
        refresh_sums(self.output)

    def replace_portable_legal(self, replacements):
        path = self.output / bundle.package_names(FULL_VERSION)[1]
        prefix = f"gridcraft-next-{FULL_VERSION}-windows-x64-portable/"
        with zipfile.ZipFile(path) as archive:
            files = {info.filename: archive.read(info) for info in archive.infolist()}
        legal = {row["Name"]: row["Sha256"] for row in self.evidence["native"]["legal_payload"]}
        for name, data in replacements.items():
            files[prefix + name] = data
            legal[name] = bundle.sha256(data)
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in files.items(): archive.writestr(name, data)
        self.evidence["native"]["legal_payload"] = [{"Name": name, "Sha256": digest} for name, digest in legal.items()]
        fact = bundle.file_fact(path)
        self.evidence["native"]["inputs"][path.name] = fact
        self.manifest["packages"][path.name] = fact
        self.rewrite_evidence()

    def install_real_source_descriptions(self):
        descriptions = {name: (SCRIPTS.parents[1] / name).read_bytes() for name in ("NOTICE", "README.md")}
        for name, data in descriptions.items():
            self.assertIn(b"internal-validation-only", data.lower())
            (self.root / name).write_bytes(data)
        self.replace_portable_legal(descriptions)
        return descriptions

    def test_consistent_synthetic_contract_is_accepted(self):
        self.assertEqual(self.validate()["source_sha"], SOURCE)

    def test_exact_real_source_notice_and_readme_history_is_not_a_package_marker(self):
        descriptions = self.install_real_source_descriptions()
        self.validate()
        for name, data in descriptions.items():
            self.assertEqual((self.root / name).read_bytes(), data)

    def test_source_description_exemption_requires_exact_complete_source_bytes(self):
        descriptions = self.install_real_source_descriptions()
        self.replace_portable_legal({"NOTICE": descriptions["NOTICE"] + b"\nChanged after source review.\n"})
        with self.assertRaisesRegex(ValueError, "outside exact source"): self.validate()

    def test_source_description_exemption_does_not_apply_to_other_names(self):
        descriptions = self.install_real_source_descriptions()
        self.replace_portable_legal({"copied-NOTICE.txt": descriptions["NOTICE"]})
        with self.assertRaisesRegex(ValueError, "outside exact source"): self.validate()

    def test_renamed_actual_internal_banner_remains_blocked(self):
        banner = b"INTERNAL VALIDATION ONLY. Do not distribute this package. Review and rebuild without -ValidationOnly before publishing.\r\n"
        self.install_real_source_descriptions()
        self.replace_portable_legal({"renamed-notice.txt": banner})
        with self.assertRaisesRegex(ValueError, "internal validation marker"): self.validate()

    def test_actual_banner_is_forbidden_even_when_renamed_to_exact_source_notice(self):
        for banner in (b"INTERNAL VALIDATION ONLY. Do not distribute this package.\n",
                       b"\xef\xbb\xbfINTERNAL-VALIDATION-ONLY\r\nDo not distribute this package.\r\n",
                       "INTERNAL VALIDATION ONLY. Do not distribute this package.\r\n".encode("utf-16-le")):
            with self.subTest(banner=banner):
                (self.root / "NOTICE").write_bytes(banner)
                self.replace_portable_legal({"NOTICE": banner})
                with self.assertRaisesRegex(ValueError, "internal validation marker"): self.validate()

    def test_source_identical_history_plus_new_inline_banner_is_still_rejected(self):
        descriptions = self.install_real_source_descriptions()
        for name in ("NOTICE", "README.md"):
            with self.subTest(name=name):
                changed = descriptions[name] + b"\nWARNING: INTERNAL VALIDATION ONLY. Do not distribute this package.\n"
                (self.root / name).write_bytes(changed)
                self.replace_portable_legal({name: changed})
                with self.assertRaisesRegex(ValueError, "beyond the known source explanation"): self.validate()
                (self.root / name).write_bytes(descriptions[name])
                self.replace_portable_legal({name: descriptions[name]})

    def test_new_source_description_is_not_an_automatic_marker_exemption(self):
        descriptions = self.install_real_source_descriptions()
        changed = descriptions["NOTICE"] + b"\nThis package is internal-validation-only.\n"
        (self.root / "NOTICE").write_bytes(changed)
        self.replace_portable_legal({"NOTICE": changed})
        with self.assertRaisesRegex(ValueError, "beyond the known source explanation"): self.validate()

    def test_source_identical_sentence_or_markdown_banner_is_not_exempted(self):
        for banner in (b"This package is INTERNAL VALIDATION ONLY. Do not distribute.\n",
                       b"# INTERNAL-VALIDATION-ONLY\nDo not distribute.\n"):
            with self.subTest(banner=banner):
                (self.root / "NOTICE").write_bytes(banner)
                self.replace_portable_legal({"NOTICE": banner})
                with self.assertRaisesRegex(ValueError, "beyond the known source explanation"): self.validate()

    def test_internal_marker_filename_remains_forbidden_with_innocent_content(self):
        self.install_real_source_descriptions()
        self.replace_portable_legal({"INTERNAL-VALIDATION-ONLY.txt": b"innocent replacement content"})
        with self.assertRaisesRegex(ValueError, "internal"): self.validate()

    def test_exact_source_descriptions_never_override_internal_or_unapproved_legal_status(self):
        self.install_real_source_descriptions()
        for key, invalid in (("validation_only", True), ("distribution_approved", False), ("legal_review_approved", False)):
            with self.subTest(key=key):
                original = self.evidence["legal_status"][key]
                self.evidence["legal_status"][key] = invalid
                self.rewrite_evidence()
                with self.assertRaisesRegex(ValueError, "not distribution eligible"): self.validate()
                self.evidence["legal_status"][key] = original

    def test_checksum_fixture_preserves_canonical_name_order_and_lf(self):
        data = (self.output / "SHA256SUMS").read_bytes()
        self.assertNotIn(b"\r", data)
        names = [line.split("  ", 1)[1] for line in data.decode("utf-8").splitlines()]
        self.assertEqual(names, sorted(names))
        self.assertEqual(names[0], "RELEASE-EVIDENCE.json")

    def test_missing_real_review_fails_closed(self):
        (self.root / "packaging/windows/windows-release-review.json").unlink()
        with self.assertRaises(ValueError):
            self.validate()

    def test_internal_or_unapproved_status_cannot_be_relabelled(self):
        for key, value in (("validation_only", True), ("distribution_approved", False), ("legal_review_approved", False), ("collector_exit", 2)):
            with self.subTest(key=key):
                original = self.evidence["legal_status"][key]
                self.evidence["legal_status"][key] = value
                self.rewrite_evidence()
                with self.assertRaises(ValueError): self.validate()
                self.evidence["legal_status"][key] = original

    def test_native_failure_missing_gate_and_cleanup_failure_rejected(self):
        for update in ({"status": "failed"}, {"checks": []}, {"cleanup_errors": ["uninstall failed"]}, {"inputs": {}}):
            native = copy.deepcopy(self.evidence["native"])
            self.evidence["native"].update(update)
            self.rewrite_evidence()
            with self.assertRaises(ValueError): self.validate()
            self.evidence["native"] = native

    def test_changed_binary_even_with_rehashed_bundle_rejected(self):
        path = self.output / bundle.package_names(FULL_VERSION)[0]
        path.write_bytes(path.read_bytes() + b"changed")
        self.manifest["packages"][path.name] = bundle.file_fact(path)
        bundle.write_json(self.output / "release-manifest.json", self.manifest)
        refresh_sums(self.output)
        with self.assertRaisesRegex(ValueError, "native-tested"): self.validate()

    def test_wrong_sha_attempt_repository_and_version_rejected(self):
        for args in (("b" * 40, REPOSITORY, 123, 2), (SOURCE, "other/repo", 123, 2), (SOURCE, REPOSITORY, 123, 1), (SOURCE, REPOSITORY, 124, 2)):
            with self.assertRaises(ValueError): bundle.validate_bundle(self.output, self.root, *args)
        (self.root / "Cargo.toml").write_text('[workspace.package]\nversion = "0.6.3"\n')
        with self.assertRaises(ValueError): self.validate()

    def test_checksum_and_extra_artifact_rejected(self):
        (self.output / "SHA256SUMS").write_text("fake")
        with self.assertRaisesRegex(ValueError, "SHA256SUMS"): self.validate()
        refresh_sums(self.output)
        (self.output / "private-env.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "five release assets"): self.validate()

    def test_final_link_hashes_must_match_binaries(self):
        self.evidence["toolchain"]["links"]["executables"][0]["sha256"] = "f" * 64
        self.rewrite_evidence()
        with self.assertRaisesRegex(ValueError, "executable/link"): self.validate()

    def test_unknown_toolchain_issue_cannot_be_approved(self):
        self.evidence["toolchain"]["toolchain"]["unresolved"].append("MISSING_LICENSE")
        self.rewrite_evidence()
        with self.assertRaisesRegex(ValueError, "unresolved toolchain"): self.validate()

    def install_real_selector_shape(self):
        selection = {
            "schema": 1, "evidence_only": True, "distribution_approved": False,
            "target": bundle.TARGET, "visual_studio_version": "17.14.37710.0",
            "visual_studio_product": "Microsoft.VisualStudio.Product.Enterprise",
            "msvc_toolset_version": "14.44.35207", "windows_sdk_version": "10.0.26100.0",
            "selected_files": [{"name": "link.exe", "sha256": "2" * 64}],
            "applied_to_process": True, "reviewed_versions_requested": False,
            "unresolved": ["LEGAL_TERMS_NOT_REVIEWED", "FINAL_LINK_INPUTS_NOT_YET_PROVEN"],
        }
        self.evidence["toolchain"]["selection"] = selection
        review_path = self.root / "packaging/windows/windows-release-review.json"
        review = bundle.read_json(review_path)
        review["toolchain_review_fingerprint"] = bundle.toolchain_fingerprint(self.evidence["toolchain"])
        bundle.write_json(review_path, review)
        self.rewrite_evidence()
        return selection, review_path

    def test_real_selector_placeholders_remain_raw_and_need_review_notices_and_final_links(self):
        selection, review_path = self.install_real_selector_shape()
        original = copy.deepcopy(selection)
        self.validate()
        self.assertEqual(self.evidence["toolchain"]["selection"], original)
        saved = bundle.read_json(self.output / "RELEASE-EVIDENCE.json")["toolchain"]["selection"]
        self.assertEqual(saved, original)
        review = bundle.read_json(review_path)
        stale = copy.deepcopy(review)
        stale["toolchain_review_fingerprint"] = "f" * 64
        bundle.write_json(review_path, stale)
        with self.assertRaisesRegex(ValueError, "review fingerprint"): self.validate()
        bundle.write_json(review_path, review)
        no_notices = copy.deepcopy(review)
        no_notices["required_toolchain_notices"] = {}
        bundle.write_json(review_path, no_notices)
        with self.assertRaisesRegex(ValueError, "notices are required"): self.validate()
        bundle.write_json(review_path, review)
        self.evidence["toolchain"]["links"]["executables"][0]["unresolved"] = ["STATIC_RUNTIME_INPUTS_UNPROVEN"]
        self.rewrite_evidence()
        with self.assertRaisesRegex(ValueError, "executable/link"): self.validate()

    def test_selector_unknown_issue_and_non_list_still_block(self):
        selection, _ = self.install_real_selector_shape()
        for issues in ([*selection["unresolved"], "TOOLCHAIN_SELECTION_FAILED"], ["MSVC_SDK_SELECTION_INCOMPLETE"], [], "LEGAL_TERMS_NOT_REVIEWED", {}, None):
            if issues == []:
                continue  # An empty array is also structurally valid evidence.
            self.evidence["toolchain"]["selection"]["unresolved"] = issues
            self.rewrite_evidence()
            with self.assertRaisesRegex(ValueError, "unresolved selection/link"): self.validate()

    def test_fingerprint_preserves_std_runtime_linker_and_legal_hashes(self):
        original = self.evidence["toolchain"]
        digest = bundle.toolchain_fingerprint(original)
        for index in (0, 1, 3):
            changed = copy.deepcopy(original)
            changed["toolchain"]["files"][index]["sha256"] = "f" * 64
            self.assertNotEqual(bundle.toolchain_fingerprint(changed), digest)
        changed = copy.deepcopy(original)
        changed["links"]["executables"][0]["linker_sha256"] = "f" * 64
        self.assertNotEqual(bundle.toolchain_fingerprint(changed), digest)
        changed = copy.deepcopy(original)
        changed["toolchain"]["files"][2]["sha256"] = "f" * 64
        changed["links"]["executables"][0]["sha256"] = "f" * 64
        self.assertEqual(bundle.toolchain_fingerprint(changed), digest)

    def test_toolchain_originals_cannot_be_absent_uncollected_or_renamed_away(self):
        path = self.root / "packaging/windows/windows-release-review.json"
        review = bundle.read_json(path)
        for notices in ({}, {"toolchain-rust-std-original.txt": "f" * 64}, {"absent.txt": next(iter(review["required_toolchain_notices"].values()))}):
            changed = copy.deepcopy(review)
            changed["required_toolchain_notices"] = notices
            bundle.write_json(path, changed)
            with self.assertRaises(ValueError): self.validate()
        bundle.write_json(path, review)
        self.evidence["native"]["legal_payload"] = [row for row in self.evidence["native"]["legal_payload"] if row["Name"] != "toolchain-rust-std-original.txt"]
        self.rewrite_evidence()
        with self.assertRaises(ValueError): self.validate()

    def test_safe_zip_rejects_traversal_duplicate_symlink_internal_and_absolute(self):
        for entries in (("../escape",), ("/absolute",), ("a", "A"), ("a\\b",), ("C:/path",), ("INTERNAL-VALIDATION-ONLY.txt",), ("a//b",)):
            with self.subTest(entries=entries), tempfile.TemporaryFile() as stream:
                with zipfile.ZipFile(stream, "w") as archive:
                    for name in entries:
                        # ZipInfo(name) sanitizes backslashes on Windows. Set
                        # the raw stored name after construction so the hostile
                        # fixture, including its central directory, stays hostile.
                        info = zipfile.ZipInfo("fixture")
                        info.filename = info.orig_filename = name
                        archive.writestr(info, "test")
                stream.seek(0)
                with zipfile.ZipFile(stream) as archive:
                    self.assertEqual([info.orig_filename for info in archive.infolist()], list(entries))
                    # Do not repair the parsed ZipInfo: production must reject
                    # raw names that Windows would normalize while reading.
                    with self.assertRaises(ValueError): bundle.safe_zip(archive)
        with tempfile.TemporaryFile() as stream:
            with zipfile.ZipFile(stream, "w") as archive:
                info = zipfile.ZipInfo("link")
                info.external_attr = 0o120777 << 16
                archive.writestr(info, "target")
            stream.seek(0)
            with zipfile.ZipFile(stream) as archive, self.assertRaises(ValueError): bundle.safe_zip(archive)

    def test_duplicate_json_rejected(self):
        (self.output / "release-manifest.json").write_text('{"schema":1,"schema":1}')
        with self.assertRaisesRegex(ValueError, "duplicate"): self.validate()


if __name__ == "__main__":
    unittest.main()
