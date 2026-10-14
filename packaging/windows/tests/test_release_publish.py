import copy
import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).parents[1]))
import publish_release as release
from test_release_bundle import SOURCE, REPOSITORY, create_fixture, bundle


def ci_fixture():
    run = {"id": 123, "run_attempt": 2, "repository": {"id": 42, "full_name": REPOSITORY}, "head_repository": {"id": 42, "full_name": REPOSITORY},
           "head_sha": SOURCE, "path": release.WORKFLOW, "event": "push", "status": "completed", "conclusion": "success"}
    jobs = [{"name": name, "run_id": 123, "run_attempt": 2, "head_sha": SOURCE, "status": "completed", "conclusion": "success",
             "steps": [{"name": "real gate", "status": "completed", "conclusion": "success"}]} for name in release.JOBS]
    artifact = {"id": 99, "name": f"windows-release-{SOURCE}-attempt-2", "expired": False, "size_in_bytes": 100, "digest": "sha256:" + "c" * 64,
                "workflow_run": {"id": 123, "head_sha": SOURCE, "repository_id": 42, "head_repository_id": 42}}
    return run, jobs, artifact


class ProvenanceTests(unittest.TestCase):
    def test_all_original_jobs_required(self):
        run, jobs, _ = ci_fixture()
        self.assertEqual(release.check_run(run, jobs, REPOSITORY, SOURCE, 123), 2)
        for index in range(4):
            with self.assertRaises(ValueError): release.check_run(run, jobs[:index] + jobs[index + 1:], REPOSITORY, SOURCE, 123)
        for job in jobs:
            job.pop("run_attempt")
        self.assertEqual(release.check_run(run, jobs, REPOSITORY, SOURCE, 123), 2)

    def test_green_run_cannot_hide_failed_skipped_old_or_wrong_source_job(self):
        run, jobs, _ = ci_fixture()
        for update in ({"conclusion": "failure"}, {"conclusion": "skipped"}, {"status": "in_progress"}, {"run_attempt": 1}, {"head_sha": "b" * 40}):
            changed = copy.deepcopy(jobs)
            changed[0].update(update)
            with self.assertRaises(ValueError): release.check_run(run, changed, REPOSITORY, SOURCE, 123)
        changed = copy.deepcopy(jobs)
        changed[0]["steps"][0]["conclusion"] = "failure"
        with self.assertRaises(ValueError): release.check_run(run, changed, REPOSITORY, SOURCE, 123)

    def test_pr_cross_repository_wrong_workflow_and_incomplete_run_rejected(self):
        run, jobs, _ = ci_fixture()
        for update in ({"event": "pull_request"}, {"path": "fake.yml"}, {"head_sha": "b" * 40}, {"status": "in_progress"}, {"conclusion": "failure"},
                       {"head_repository": {"id": 999, "full_name": "attacker/fork"}}):
            changed = copy.deepcopy(run)
            changed.update(update)
            with self.assertRaises(ValueError): release.check_run(changed, jobs, REPOSITORY, SOURCE, 123)

    def test_artifact_wrong_attempt_digest_expiry_source_and_duplicate_rejected(self):
        run, _, artifact = ci_fixture()
        self.assertEqual(release.select_artifact([artifact], run, REPOSITORY, SOURCE)["id"], 99)
        for update in ({"expired": True}, {"digest": None}, {"digest": "bad"}, {"size_in_bytes": 0}, {"name": f"windows-release-{SOURCE}-attempt-1"},
                       {"workflow_run": {"id": 123, "head_sha": "b" * 40, "repository_id": 42, "head_repository_id": 42}}):
            changed = copy.deepcopy(artifact)
            changed.update(update)
            with self.assertRaises((ValueError, TypeError)): release.select_artifact([changed], run, REPOSITORY, SOURCE)
        with self.assertRaises(ValueError): release.select_artifact([artifact, artifact], run, REPOSITORY, SOURCE)

    def test_existing_tag_or_draft_is_never_reused(self):
        class Api:
            def __init__(self, refs, releases): self.refs, self.releases = refs, releases
            def request(self, _): return self.refs
            def pages(self, _): return self.releases
        for api in (Api([{"ref": "refs/tags/v1"}], []), Api([], [{"tag_name": "v1", "draft": True}]), Api([], [{"tag_name": "v1", "draft": False}])):
            with self.assertRaises(ValueError): release.require_new_tag_and_release(api, "/repos/o/r", "v1")
        release.require_new_tag_and_release(Api([], []), "/repos/o/r", "v1")


class PublicationOrderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bundle, _, self.manifest = create_fixture(self.root)
        self.run, _, self.artifact = ci_fixture()

    def test_failed_draft_download_verification_never_publishes(self):
        calls = []
        manifest = self.manifest
        class Api:
            def request(self, path, method="GET", data=None, upload=None):
                calls.append((method, path, data))
                if method == "POST" and path.endswith("/releases"):
                    base = f"/repos/{REPOSITORY}"
                    return {"id": 4, "draft": True, "tag_name": "v" + manifest["full_version"], "target_commitish": SOURCE,
                            "url": f"https://api.github.com{base}/releases/4", "assets_url": f"https://api.github.com{base}/releases/4/assets",
                            "upload_url": f"https://uploads.github.com{base}/releases/4/assets{{?name,label}}"}
                return {}
        with patch.object(release, "require_new_tag_and_release"), patch.object(release, "get_run", return_value=self.run), patch.object(release, "verify_uploaded_assets", side_effect=ValueError("digest mismatch")):
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                release.publish(Api(), self.manifest, self.artifact, self.bundle)
        self.assertEqual(len([x for x in calls if x[0] == "POST"]), 6)
        self.assertFalse(any(x[0] in {"PATCH", "DELETE"} for x in calls))
        self.assertIs(calls[0][2]["draft"], True)

    def test_legal_and_unsigned_limitations_in_release_body(self):
        body = release.release_body(self.manifest, self.artifact)
        for text in (SOURCE, "Unsigned", "SmartScreen", "non-admin UAC", "Windows 10/11", "independent derivative", "SHA256SUMS", "RELEASE-EVIDENCE.json", "attempts/2"):
            self.assertIn(text, body)

    def test_success_uploads_verifies_creates_exact_tag_then_publishes_and_checks_anonymously(self):
        calls = []
        manifest = self.manifest
        base = f"/repos/{REPOSITORY}"
        tag = "v" + manifest["full_version"]
        draft = {"id": 4, "draft": True, "tag_name": tag, "target_commitish": SOURCE,
                 "url": f"https://api.github.com{base}/releases/4", "assets_url": f"https://api.github.com{base}/releases/4/assets",
                 "upload_url": f"https://uploads.github.com{base}/releases/4/assets{{?name,label}}"}
        class Api:
            def request(self, path, method="GET", data=None, upload=None):
                calls.append((method, path, data))
                if method == "POST" and path.endswith("/releases"): return draft
                if "/matching-refs/" in path: return []
                if path.endswith("/git/refs") or "/git/ref/" in path: return {"ref": "refs/tags/" + tag, "object": {"type": "commit", "sha": SOURCE}}
                if method == "GET" and path.endswith("/releases/4"): return draft
                if method == "PATCH": return {**draft, "draft": False}
                return {}
        def verify(_api, _base, _id, _bundle, _tag, public=False):
            calls.append(("VERIFY_PUBLIC" if public else "VERIFY_DRAFT", "", None))
        with patch.object(release, "require_new_tag_and_release"), patch.object(release, "get_run", return_value=self.run), patch.object(release, "verify_uploaded_assets", side_effect=verify):
            release.publish(Api(), self.manifest, self.artifact, self.bundle)
        draft_check = next(i for i, call in enumerate(calls) if call[0] == "VERIFY_DRAFT")
        tag_create = next(i for i, call in enumerate(calls) if call[0] == "POST" and call[1].endswith("/git/refs"))
        publication = next(i for i, call in enumerate(calls) if call[0] == "PATCH")
        public_check = next(i for i, call in enumerate(calls) if call[0] == "VERIFY_PUBLIC")
        self.assertLess(draft_check, tag_create)
        self.assertLess(tag_create, publication)
        self.assertLess(publication, public_check)
        self.assertEqual(calls[tag_create][2]["sha"], SOURCE)
        self.assertFalse(any(call[0] == "DELETE" for call in calls))

    def test_actual_downloaded_artifact_digest_and_bundle_are_checked(self):
        archive_path = self.root / "source-artifact.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            for path in self.bundle.iterdir(): archive.write(path, path.name)
        fact = bundle.file_fact(archive_path)
        artifact = {**self.artifact, "digest": "sha256:" + fact["sha256"], "size_in_bytes": fact["bytes"]}
        class Api:
            def pages(self, path, key): return [artifact]
            def request(self, path, output=None): output.write_bytes(archive_path.read_bytes())
        target = self.root / "downloaded"
        target.mkdir()
        with patch.object(release, "get_run", return_value=self.run):
            manifest, checked_artifact, _ = release.fetch_verified(Api(), REPOSITORY, SOURCE, 123, self.root, target)
        self.assertEqual(manifest, self.manifest)
        self.assertEqual(checked_artifact["digest"], artifact["digest"])
        artifact["digest"] = "sha256:" + "0" * 64
        target2 = self.root / "tampered"
        target2.mkdir()
        with patch.object(release, "get_run", return_value=self.run), self.assertRaisesRegex(ValueError, "digest/size"):
            release.fetch_verified(Api(), REPOSITORY, SOURCE, 123, self.root, target2)

    def test_draft_assets_verify_api_bytes_and_public_assets_use_anonymous_downloads(self):
        base = f"/repos/{REPOSITORY}"
        tag = "v" + self.manifest["full_version"]
        paths = sorted(self.bundle.iterdir())
        assets = [{"id": index + 1, "name": path.name, "url": f"https://api.github.com{base}/releases/assets/{index + 1}",
                   "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/untagged-synthetic/{path.name}",
                   "state": "uploaded", "size": path.stat().st_size, "digest": "sha256:" + bundle.file_fact(path)["sha256"]}
                  for index, path in enumerate(paths)]
        requests = []
        class Api:
            def pages(self, path):
                self_path = f"{base}/releases/4/assets"
                if path != self_path: raise ValueError("wrong release")
                return assets
            def request(self, path, output):
                requests.append("authenticated-api")
                output.write_bytes(paths[int(path.rsplit("/", 1)[-1]) - 1].read_bytes())
            def download_anonymous(self, url, output):
                requests.append("anonymous-public")
                match = next(path for path in paths if url.endswith("/" + path.name))
                output.write_bytes(match.read_bytes())
        release.verify_uploaded_assets(Api(), base, 4, self.bundle, tag)
        self.assertEqual(requests, ["authenticated-api"] * 5)
        for asset in assets:
            asset["browser_download_url"] = asset["browser_download_url"].replace("untagged-synthetic", tag)
        requests.clear()
        release.verify_uploaded_assets(Api(), base, 4, self.bundle, tag, public=True)
        self.assertEqual(requests, ["anonymous-public"] * 5)
        assets[0]["url"] = "https://api.github.com/repos/other/repo/releases/assets/1"
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            release.verify_uploaded_assets(Api(), base, 4, self.bundle, tag)

    def test_workflow_manual_exact_source_and_write_only_in_publish(self):
        workflow = (Path(__file__).parents[3] / ".github/workflows/windows-release.yml").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("workflow_run:", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertNotIn("contents: write", workflow.split("  publish:")[0])
        self.assertEqual(workflow.count("contents: write"), 1)
        self.assertEqual(workflow.count("persist-credentials: false"), 4)
        self.assertIn('"$PUBLISHER_SHA" == "$GITHUB_SHA"', workflow)
        self.assertEqual(workflow.count("ref: ${{ inputs.source_sha }}"), 2)
        self.assertEqual(workflow.count("ref: ${{ inputs.publisher_sha }}"), 2)
        self.assertEqual(workflow.count("--root .release-source"), 2)
        self.assertIn("--publisher-sha", workflow)
        self.assertIn("--artifact-digest", workflow)


class DispatchIdentityTests(unittest.TestCase):
    def test_separate_source_and_publisher_are_both_exact(self):
        publisher = "d" * 40
        env = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
               "GITHUB_SHA": publisher, "GITHUB_REPOSITORY": REPOSITORY}
        def result(value): return release.subprocess.CompletedProcess([], 0, stdout=value + "\n")
        with patch.dict(os.environ, env), patch.object(release.subprocess, "run", side_effect=[result(publisher), result(SOURCE)]) as run:
            release.verify_checkout_identity(Path("source-checkout"), SOURCE, publisher, REPOSITORY)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[1].args[0][2], "source-checkout")
        for heads in ((SOURCE, SOURCE), (publisher, publisher)):
            with self.subTest(heads=heads), patch.dict(os.environ, env), patch.object(release.subprocess, "run", side_effect=[result(x) for x in heads]):
                with self.assertRaisesRegex(ValueError, "checkout differs"):
                    release.verify_checkout_identity(Path("source-checkout"), SOURCE, publisher, REPOSITORY)

    def test_wrong_dispatch_sha_or_event_is_rejected_before_git(self):
        env = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "push", "GITHUB_SHA": SOURCE, "GITHUB_REPOSITORY": REPOSITORY}
        for changed in ({}, {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_SHA": "b" * 40}):
            with patch.dict(os.environ, {**env, **changed}), patch.object(release.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "manual dispatch"):
                    release.verify_checkout_identity(Path("source"), SOURCE, "d" * 40, REPOSITORY)
                run.assert_not_called()

    def test_release_body_identifies_tooling_separately_from_binary_source(self):
        with tempfile.TemporaryDirectory() as temp:
            _, _, manifest = create_fixture(Path(temp))
            _, _, artifact = ci_fixture()
            body = release.release_body(manifest, artifact, "d" * 40)
            self.assertIn("Source commit: " + SOURCE, body)
            self.assertIn("/commit/" + "d" * 40 + " (does not rebuild the packages)", body)


class CredentialBoundaryTests(unittest.TestCase):
    def test_artifact_zip_and_release_asset_use_distinct_media_types(self):
        requests = []
        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                return io.BytesIO(b"binary fixture")
        api = release.Api(REPOSITORY)
        api.opener = Opener()
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"GH_TOKEN": "synthetic-test-token"}):
            for index, endpoint in enumerate(("actions/artifacts/99/zip", "releases/assets/88")):
                api.request(f"/repos/{REPOSITORY}/{endpoint}", output=Path(temp) / str(index))
        self.assertEqual(requests[0].get_header("Accept"), "application/vnd.github+json")
        self.assertEqual(requests[1].get_header("Accept"), "application/octet-stream")

    def test_authenticated_redirect_becomes_strictly_anonymous(self):
        requests = []
        class Response(io.BytesIO):
            pass
        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                if len(requests) == 1:
                    raise HTTPError(request.full_url, 302, "redirect", {"Location": "https://storage.blob.core.windows.net/object?sig=synthetic"}, None)
                if len(requests) == 2:
                    raise HTTPError(request.full_url, 302, "redirect", {"Location": "https://release-assets.githubusercontent.com/asset?signature=synthetic"}, None)
                return Response(b"synthetic bytes")
        api = release.Api(REPOSITORY)
        api.opener = Opener()
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"GH_TOKEN": "synthetic-test-token"}):
            output = Path(temp) / "asset"
            api.request(f"/repos/{REPOSITORY}/actions/artifacts/99/zip", output=output)
            self.assertEqual(output.read_bytes(), b"synthetic bytes")
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer synthetic-test-token")
        self.assertIsNone(requests[1].get_header("Authorization"))
        self.assertIsNone(requests[2].get_header("Authorization"))

    def test_mutation_redirect_not_followed_or_retried(self):
        requests = []
        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                raise HTTPError(request.full_url, 307, "redirect", {"Location": "https://attacker.invalid/upload"}, None)
        api = release.Api(REPOSITORY)
        api.opener = Opener()
        with patch.dict(os.environ, {"GH_TOKEN": "synthetic-test-token"}), self.assertRaisesRegex(ValueError, "HTTP 307"):
            api.request(f"/repos/{REPOSITORY}/releases", method="POST", data={"draft": True})
        self.assertEqual(len(requests), 1)

    def test_other_authenticated_host_or_repository_rejected_before_request(self):
        api = release.Api(REPOSITORY)
        for path in ("https://api.github.com.evil.invalid/repos/o/r/releases", "/repos/other/repository/releases", "https://uploads.github.com.evil.invalid/repos/o/r/releases"):
            with self.assertRaises(ValueError): api.request(path)

    def test_anonymous_download_never_accepts_http_userinfo_or_arbitrary_host(self):
        for url in ("http://github.com/file", "https://github.com:444/file", "https://user:password@github.com/file", "https://github.com.evil.invalid/file", "https://127.0.0.1/file"):
            with self.assertRaises(ValueError): release.anonymous_download_host(url)

    def test_no_default_redirect_handler_can_copy_auth(self):
        self.assertIsNone(release.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid"))


if __name__ == "__main__":
    unittest.main()
