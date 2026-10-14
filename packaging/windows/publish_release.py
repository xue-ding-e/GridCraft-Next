#!/usr/bin/env python3
"""Manual, fail-closed GitHub Release promotion of one exact successful CI artifact.

Uses only the runner's existing ephemeral Actions token in process memory.
GETs are separate from mutation. A failed upload/check leaves the new draft intact;
there is no overwrite, deletion, mutation retry, or resume of an existing release.
Official REST contracts: docs.github.com/en/rest/actions/{artifacts,workflow-jobs}
and docs.github.com/en/rest/releases/{releases,assets}.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from release_bundle import LIMIT, REPO, SHA, WORKFLOW, file_fact, package_names, require, safe_zip, validate_bundle, version

JOBS = {"cargo xtask ci", "Windows packaging script tests", "Windows native application and installer validation", "Complete source and Windows validation"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def anonymous_download_host(url):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    require(parsed.scheme == "https" and parsed.port in (None, 443) and not parsed.username and not parsed.password
            and (host in {"github.com", "api.github.com"} or host.endswith((".githubusercontent.com", ".blob.core.windows.net"))),
            "unexpected anonymous download destination")


class Api:
    def __init__(self, repository):
        self.repository = repository
        # Never allow urllib to copy Authorization to a redirect request.
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, path, method="GET", data=None, output=None, upload=None):
        prefix = f"/repos/{self.repository}/"
        if path.startswith(prefix):
            url = "https://api.github.com" + path
        else:
            require(path.startswith("https://uploads.github.com" + prefix), "unexpected API destination")
            url = path
        parsed = urllib.parse.urlsplit(url)
        require(parsed.scheme == "https" and parsed.netloc in {"api.github.com", "uploads.github.com"}
                and not parsed.fragment, "authenticated API host must be exact")
        # The workflow sets GH_TOKEN only for this step. No token enters an
        # argument, file, output, error message, or redirected request.
        token = os.environ.get("GH_TOKEN")
        require(bool(token), "ephemeral Actions token is missing")
        headers = {"Authorization": "Bearer " + token, "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "GridCraft-Next-reviewed-release", "Accept": "application/vnd.github+json"}
        # Actions artifact ZIP requests return a redirect using the GitHub API
        # media type. Only the Release asset endpoint negotiates binary bytes.
        if output is not None and re.fullmatch(re.escape(prefix) + r"releases/assets/[1-9][0-9]*", path):
            headers["Accept"] = "application/octet-stream"
        body = json.dumps(data).encode() if data is not None else None
        if body is not None:
            headers["Content-Type"] = "application/json"
        stream = None
        try:
            if upload is not None:
                headers.update({"Content-Type": "application/octet-stream", "Content-Length": str(file_fact(upload)["bytes"])})
                stream = Path(upload).open("rb")
                body = stream
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                response = self.opener.open(request, timeout=300)
            except urllib.error.HTTPError as error:
                if output is not None and method == "GET" and error.code in {301, 302, 303, 307, 308}:
                    location = error.headers.get("Location")
                    error.close()
                    require(location is not None, "download redirect location missing")
                    return self.download_anonymous(location, output)
                raise ValueError(f"GitHub API {method} returned HTTP {error.code}; no automatic mutation retry") from None
            with response:
                if output is not None:
                    return self.save_download(response, output)
                raw = response.read(16 * 1024 * 1024 + 1)
                require(len(raw) <= 16 * 1024 * 1024, "API JSON too large")
                return json.loads(raw)
        except (urllib.error.URLError, TimeoutError, OSError):
            # Provider exceptions may include signed URLs; suppress their text.
            raise ValueError(f"GitHub API {method} transport failed; outcome may be uncertain, no mutation retry") from None
        finally:
            if stream is not None:
                stream.close()

    @staticmethod
    def save_download(response, output):
        count = 0
        with Path(output).open("xb") as stream:
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                require(count <= LIMIT, "download exceeds size bound")
                stream.write(chunk)

    def download_anonymous(self, url, output):
        # Always construct a fresh anonymous request, even on same-host redirects.
        # No cookies, auth handler, token, or previous request headers are reused.
        for _ in range(6):
            anonymous_download_host(url)
            request = urllib.request.Request(url, headers={"User-Agent": "GridCraft-Next-reviewed-release"})
            try:
                response = self.opener.open(request, timeout=300)
            except urllib.error.HTTPError as error:
                if error.code in {301, 302, 303, 307, 308}:
                    location = error.headers.get("Location")
                    error.close()
                    require(location is not None, "anonymous redirect location missing")
                    url = urllib.parse.urljoin(url, location)
                    continue
                raise ValueError(f"Anonymous asset download returned HTTP {error.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                raise ValueError("Anonymous asset download failed") from None
            with response:
                return self.save_download(response, output)
        raise ValueError("Too many anonymous download redirects")

    def pages(self, path, key=None):
        values = []
        separator = "&" if "?" in path else "?"
        for page in range(1, 101):
            response = self.request(f"{path}{separator}per_page=100&page={page}")
            rows = response[key] if key else response
            require(type(rows) is list, "invalid paginated API response")
            values.extend(rows)
            if len(rows) < 100:
                return values
        raise ValueError("API pagination exceeded safe bound")


def check_run(run, jobs, repository, source_sha, run_id):
    require(type(run_id) is int and run_id > 0 and run["id"] == run_id, "CI run ID mismatch")
    require(run["repository"]["full_name"] == repository and run["head_repository"]["full_name"] == repository
            and run["repository"]["id"] == run["head_repository"]["id"], "cross-repository CI is forbidden")
    require(run["head_sha"] == source_sha and run["path"] == WORKFLOW and run["event"] in {"push", "workflow_dispatch"}, "CI source/workflow/event mismatch")
    require(run["status"] == "completed" and run["conclusion"] == "success"
            and type(run["run_attempt"]) is int and run["run_attempt"] > 0, "complete successful CI required")
    for name in JOBS:
        matches = [job for job in jobs if job["name"] == name]
        require(len(matches) == 1, "original independent CI gate missing or duplicated")
        job = matches[0]
        require(job["run_id"] == run_id and job["head_sha"] == source_sha
                # The attempt-specific jobs endpoint is authoritative. Some API
                # versions omit the optional per-job run_attempt field.
                and job.get("run_attempt", run["run_attempt"]) == run["run_attempt"] and job["status"] == "completed"
                and job["conclusion"] == "success", "a required original CI gate did not succeed")
        # A job-level green is insufficient if any named step was skipped, failed,
        # or marked continue-on-error. Post-checkout cleanup may be absent by design.
        require(bool(job["steps"]), "original CI gate step evidence missing")
        for step in job["steps"]:
            require(step["status"] == "completed" and step["conclusion"] == "success", "required CI contains a skipped/failed step")
    return run["run_attempt"]


def get_run(api, repository, source_sha, run_id):
    base = f"/repos/{repository}"
    run = api.request(f"{base}/actions/runs/{run_id}")
    attempt = run["run_attempt"]
    require(type(attempt) is int and attempt > 0, "invalid run attempt")
    jobs = api.pages(f"{base}/actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs")
    check_run(run, jobs, repository, source_sha, run_id)
    return run


def select_artifact(artifacts, run, repository, source_sha):
    name = f"windows-release-{source_sha}-attempt-{run['run_attempt']}"
    matches = [item for item in artifacts if item["name"] == name]
    require(len(matches) == 1, "one final artifact from the latest successful attempt is required")
    item = matches[0]
    provenance = item["workflow_run"]
    require(provenance["id"] == run["id"] and provenance["head_sha"] == source_sha
            and provenance["repository_id"] == run["repository"]["id"]
            and provenance["head_repository_id"] == run["repository"]["id"], "artifact provenance mismatch")
    require(item["expired"] is False and type(item["id"]) is int and item["id"] > 0
            and 0 < item["size_in_bytes"] <= LIMIT, "expired/invalid artifact")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", item["digest"]) is not None, "GitHub artifact digest required")
    return item


def fetch_verified(api, repository, source_sha, run_id, root, directory, expected=None):
    run = get_run(api, repository, source_sha, run_id)
    base = f"/repos/{repository}"
    artifacts = api.pages(f"{base}/actions/runs/{run_id}/artifacts", "artifacts")
    artifact = select_artifact(artifacts, run, repository, source_sha)
    if expected is not None:
        require((artifact["id"], artifact["digest"], run["run_attempt"]) == expected, "artifact changed after read-only verification")
    archive_path = directory / "artifact.zip"
    api.request(f"{base}/actions/artifacts/{artifact['id']}/zip", output=archive_path)
    fact = file_fact(archive_path)
    require("sha256:" + fact["sha256"] == artifact["digest"] and fact["bytes"] == artifact["size_in_bytes"], "downloaded artifact digest/size mismatch")
    bundle = directory / "bundle"
    bundle.mkdir()
    expected_names = {*package_names(version(root)[0]), "release-manifest.json", "RELEASE-EVIDENCE.json", "SHA256SUMS"}
    with zipfile.ZipFile(archive_path) as archive:
        infos = safe_zip(archive)
        require({info.filename for info in infos} == expected_names and len(infos) == 5 and all(not info.is_dir() for info in infos), "unexpected artifact layout")
        for info in infos:
            with archive.open(info) as source, (bundle / info.filename).open("xb") as dest:
                import shutil
                shutil.copyfileobj(source, dest, 1024 * 1024)
    manifest = validate_bundle(bundle, root, source_sha, repository, run_id, run["run_attempt"])
    # Recheck mutable run state after downloading; a new rerun invalidates this attempt.
    current = get_run(api, repository, source_sha, run_id)
    require(current["run_attempt"] == run["run_attempt"], "CI was rerun during verification")
    return manifest, artifact, bundle


def require_new_tag_and_release(api, base, tag):
    # Explicit lists avoid interpreting permission/connection failures as a 404.
    refs = api.request(f"{base}/git/matching-refs/tags/{urllib.parse.quote(tag, safe='')}")
    require(type(refs) is list and not any(ref["ref"] == "refs/tags/" + tag for ref in refs), "tag already exists; never reuse or overwrite")
    require(not any(release["tag_name"] == tag for release in api.pages(f"{base}/releases")), "Release/draft already exists; never reuse or overwrite")


def release_body(manifest, artifact, publisher_sha=None):
    ci = manifest["ci"]
    repository = manifest["repository"]
    return (f"GridCraft-Next {manifest['full_version']} for Windows x64.\n\n"
            f"Source commit: {manifest['source_sha']}\n"
            f"Source: https://github.com/{repository}/tree/{manifest['source_sha']}\n"
            + (f"Publication tooling: https://github.com/{repository}/commit/{publisher_sha} (does not rebuild the packages)\n" if publisher_sha else "")
            + f"MSI ProductVersion: {manifest['msi_version']}\n"
            f"Validated CI: https://github.com/{repository}/actions/runs/{ci['run_id']}/attempts/{ci['run_attempt']}\n"
            f"Producer artifact ID: {artifact['id']}\nArtifact digest: {artifact['digest']}\n\n"
            "Download the MSI for a machine-wide installation, or extract the portable ZIP and run gridcraft-next.exe. "
            "Keep the bundled license and notice files. SHA256SUMS covers the downloadable packages and release evidence.\n\n"
            "Unsigned build: MSI and executables do not have an Authenticode signature. Windows may show an unknown-publisher "
            "or SmartScreen warning; verify the source and SHA256 before deciding whether to run it. "
            "No signing certificate, SmartScreen reputation, non-admin UAC, or Windows 10/11 hardware certification is claimed.\n\n"
            "The original full source gate and hosted Windows x64 real Rust GUI/CLI, install, repair, upgrade/downgrade, "
            "coexistence and uninstall gates passed for these exact bytes. RELEASE-EVIDENCE.json records their bounded evidence. "
            "GridCraft-Next is an independent derivative, not an upstream GridCraft release.\n\n"
            "Microsoft runtime scope: these Windows binaries embed the release static C runtime components "
            "libcmt/libvcruntime and Universal C Runtime. Acquisition, installation, use and redistribution "
            "of those Microsoft components are subject to the applicable original Microsoft terms included "
            "in both packages: Visual-Studio-2022-Enterprise-Professional-License-EN.docx and sdk_license.rtf. "
            "Read WINDOWS-RUNTIME-LICENSE-NOTICE.txt for scope. The project's MIT/Apache-2.0 source licensing "
            "and other open-source component licenses are unchanged; the Microsoft components are not relicensed under them.\n"
            "Official terms: https://learn.microsoft.com/en-us/legal/windows-sdk/license and "
            "https://visualstudio.microsoft.com/wp-content/uploads/2021/11/Visual-Studio-2022-Enterprise-Professional-License-EN.docx\n")


def verify_uploaded_assets(api, base, release_id, bundle, tag, public=False):
    expected = {path.name: file_fact(path) for path in bundle.iterdir()}
    assets = api.pages(f"{base}/releases/{release_id}/assets")
    require(len(assets) == len(expected) and {x["name"] for x in assets} == set(expected), "draft asset set mismatch")
    with tempfile.TemporaryDirectory(prefix="release-download-check-") as temp:
        for asset in assets:
            fact = expected[asset["name"]]
            download_prefix = f"https://github.com{base.removeprefix('/repos')}/releases/download/"
            download_url = asset["browser_download_url"]
            expected_public_url = download_prefix + tag + "/" + asset["name"]
            # Unpublished drafts can expose an untagged-<id> placeholder URL.
            # Its bytes are read only through the exact release asset API ID;
            # after publication the public URL must identify the final tag.
            draft_placeholder = re.fullmatch(re.escape(download_prefix) + r"untagged-[A-Za-z0-9_-]+/" + re.escape(asset["name"]), download_url)
            require(type(asset["id"]) is int and asset["id"] > 0
                    and asset["url"] == f"https://api.github.com{base}/releases/assets/{asset['id']}"
                    and (download_url == expected_public_url or (not public and draft_placeholder is not None)),
                    "asset URL/repository identity mismatch")
            require(asset["state"] == "uploaded" and asset["size"] == fact["bytes"]
                    and asset["digest"] == "sha256:" + fact["sha256"], "draft asset metadata/hash mismatch")
            target = Path(temp) / asset["name"]
            if public:
                api.download_anonymous(asset["browser_download_url"], target)
            else:
                api.request(f"{base}/releases/assets/{asset['id']}", output=target)
            require(file_fact(target) == fact, "draft asset download differs from verified CI bytes")
    return assets


def publish(api, manifest, artifact, bundle, publisher_sha=None):
    repository = manifest["repository"]
    base = f"/repos/{repository}"
    tag = "v" + manifest["full_version"]
    require_new_tag_and_release(api, base, tag)
    ci = manifest["ci"]
    run = get_run(api, repository, manifest["source_sha"], ci["run_id"])
    require(run["run_attempt"] == ci["run_attempt"], "CI attempt changed before drafting")
    # Only this POST creates a release. Do not reuse IDs or overwrite assets.
    draft = api.request(f"{base}/releases", method="POST", data={
        "tag_name": tag, "target_commitish": manifest["source_sha"], "name": f"GridCraft-Next {manifest['full_version']}",
        "body": release_body(manifest, artifact, publisher_sha), "draft": True, "prerelease": "-" in manifest["full_version"], "make_latest": "false"})
    release_id = draft["id"]
    require(type(release_id) is int and release_id > 0
            and draft["url"] == f"https://api.github.com{base}/releases/{release_id}"
            and draft["assets_url"] == f"https://api.github.com{base}/releases/{release_id}/assets"
            and draft["upload_url"] == f"https://uploads.github.com{base}/releases/{release_id}/assets{{?name,label}}", "new draft API identity mismatch")
    print(f"Created new draft Release ID {release_id}; failures leave this draft unpublished.", flush=True)
    require(draft["draft"] is True and draft["tag_name"] == tag and draft["target_commitish"] == manifest["source_sha"], "draft identity mismatch")
    for path in sorted(bundle.iterdir()):
        upload = f"https://uploads.github.com/repos/{repository}/releases/{release_id}/assets?name={urllib.parse.quote(path.name, safe='')}"
        api.request(upload, method="POST", upload=path)
    verify_uploaded_assets(api, base, release_id, bundle, tag)
    # A tag can be created concurrently while a draft exists. Do not reuse it.
    refs = api.request(f"{base}/git/matching-refs/tags/{urllib.parse.quote(tag, safe='')}")
    require(not any(ref["ref"] == "refs/tags/" + tag for ref in refs), "tag appeared concurrently; leave draft unpublished")
    # Atomic create, never update, prevents GitHub from publishing against a
    # preexisting tag while silently ignoring target_commitish. Failure leaves
    # the draft intact. No tag force-update or delete is implemented.
    created_ref = api.request(f"{base}/git/refs", method="POST", data={"ref": "refs/tags/" + tag, "sha": manifest["source_sha"]})
    require(created_ref["ref"] == "refs/tags/" + tag and created_ref["object"]["type"] == "commit"
            and created_ref["object"]["sha"] == manifest["source_sha"], "new tag identity mismatch")
    run = get_run(api, repository, manifest["source_sha"], ci["run_id"])
    require(run["run_attempt"] == ci["run_attempt"], "CI attempt changed before publication")
    current = api.request(f"{base}/releases/{release_id}")
    require(current["draft"] is True and current["tag_name"] == tag and current["target_commitish"] == manifest["source_sha"], "draft changed during verification")
    result = api.request(f"{base}/releases/{release_id}", method="PATCH", data={"draft": False, "make_latest": "false"})
    require(result["draft"] is False and result["tag_name"] == tag, "publication response not confirmed; do not retry")
    ref = api.request(f"{base}/git/ref/tags/{urllib.parse.quote(tag, safe='')}")
    require(ref["object"]["type"] == "commit" and ref["object"]["sha"] == manifest["source_sha"], "published tag identity mismatch; manual attention required")
    verify_uploaded_assets(api, base, release_id, bundle, tag, public=True)
    print(f"Published and download-verified: https://github.com/{repository}/releases/tag/{tag}")


def verify_checkout_identity(root, source_sha, publisher_sha, repository):
    require(SHA.fullmatch(source_sha) is not None and SHA.fullmatch(publisher_sha) is not None
            and REPO.fullmatch(repository) is not None, "invalid exact source/publisher SHA or repository")
    require(os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and os.environ.get("GITHUB_SHA") == publisher_sha and os.environ.get("GITHUB_REPOSITORY") == repository,
            "manual dispatch must match the exact publication-tooling SHA")
    publisher_root = Path(__file__).resolve().parents[2]
    for checkout, expected, label in ((publisher_root, publisher_sha, "publisher"), (root, source_sha, "artifact source")):
        head = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        require(head == expected, label + " checkout differs from its exact SHA")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("verify", "publish"))
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--publisher-sha", required=True)
    parser.add_argument("--ci-run-id", required=True, type=int)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--root", type=Path, required=True, help="Separate checkout of the exact artifact source SHA")
    parser.add_argument("--artifact-id", type=int)
    parser.add_argument("--artifact-digest")
    parser.add_argument("--attempt", type=int)
    args = parser.parse_args()
    try:
        verify_checkout_identity(args.root, args.source_sha, args.publisher_sha, args.repository)
        expected = None
        if args.mode == "publish":
            require(args.artifact_id is not None and args.artifact_digest is not None and args.attempt is not None, "read-only verifier identity outputs required")
            expected = (args.artifact_id, args.artifact_digest, args.attempt)
        api = Api(args.repository)
        with tempfile.TemporaryDirectory(prefix="gridcraft-release-") as temp:
            manifest, artifact, bundle = fetch_verified(api, args.repository, args.source_sha, args.ci_run_id, args.root, Path(temp), expected)
            if args.mode == "publish":
                publish(api, manifest, artifact, bundle, args.publisher_sha)
            else:
                output = os.environ.get("GITHUB_OUTPUT")
                require(output is not None, "workflow output path missing")
                with open(output, "a", encoding="utf-8") as stream:
                    stream.write(f"artifact_id={artifact['id']}\nartifact_digest={artifact['digest']}\nattempt={manifest['ci']['run_attempt']}\n")
                print("Exact successful CI artifact verified; no external state changed.")
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print(f"RELEASE_BLOCKED: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
