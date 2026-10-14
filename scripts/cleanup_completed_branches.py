#!/usr/bin/env python3
"""Archive and remove only the four completed initial-delivery branches.

Manual, exact-SHA maintenance only. No arbitrary branch input, tag overwrite,
merge, force-update, credential file, or automatic mutation retry.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging/windows'))
from publish_release import Api

REPO = 'xue-ding-e/GridCraft-Next'
SHA = re.compile(r'[0-9a-f]{40}\Z')
TARGETS = (
    ('validation/initial-import', '5b93a711489a53232f4a5b4f50b5864fe2d9582d', None),
    ('validation/windows-native', '3fb1ac912f81966254b922c0087351c081b47b22', None),
    ('diagnostic/windows-msi-helpers', '8fa7602262d687bc71137041f83324acdc6265c9', 'archive/2026-10-14/windows-msi-helpers'),
    ('diagnostic/windows-toolchain', '8a384da48269b1f370ca886b4179e2bb3c67e7aa', 'archive/2026-10-14/windows-toolchain'),
)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def exact_ref(api, ref):
    prefix = ref.removeprefix('refs/')
    rows = api.request(f'/repos/{REPO}/git/matching-refs/' + urllib.parse.quote(prefix, safe='/'))
    require(type(rows) is list, 'invalid ref response')
    matches = [r for r in rows if r.get('ref') == ref]
    require(len(matches) <= 1, 'duplicate exact ref')
    if not matches:
        return None
    require(matches[0]['object']['type'] == 'commit', 'expected a direct commit ref')
    return matches[0]['object']['sha']


def delete_with_lease(branch, expected, root):
    # Keep the temporary Actions token and derived auth header out of command
    # arguments, files, command output and errors. This environment dies with git.
    require((branch, expected) in {(b, s) for b, s, _ in TARGETS}, 'deletion outside fixed scope')
    token = os.environ.get('GH_TOKEN')
    require(bool(token), 'temporary Actions token missing')
    env = os.environ.copy()
    env.pop('GH_TOKEN', None)
    env.pop('GITHUB_TOKEN', None)
    for key in list(env):
        if key.startswith(('GIT_CONFIG_', 'GIT_TRACE')) or key == 'GIT_CURL_VERBOSE':
            del env[key]
    auth = base64.b64encode(('x-access-token:' + token).encode()).decode()
    env.update({'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                'GIT_CONFIG_SYSTEM': os.devnull, 'GIT_TERMINAL_PROMPT': '0',
                'GIT_ASKPASS': '', 'GIT_CONFIG_COUNT': '3',
                'GIT_CONFIG_KEY_0': 'credential.helper', 'GIT_CONFIG_VALUE_0': '',
                'GIT_CONFIG_KEY_1': f'http.https://github.com/{REPO}.git.extraheader',
                'GIT_CONFIG_VALUE_1': 'AUTHORIZATION: basic ' + auth,
                'GIT_CONFIG_KEY_2': 'http.followRedirects', 'GIT_CONFIG_VALUE_2': 'false'})
    ref = 'refs/heads/' + branch
    result = subprocess.run(['git', '-C', str(root), 'push', '--porcelain',
                             '--force-with-lease=' + ref + ':' + expected,
                             'https://github.com/' + REPO + '.git', ':' + ref],
                            env=env, capture_output=True, text=True, timeout=120, check=False)
    # Provider output is not needed for the recovery mapping; do not print it.
    return result.returncode == 0


def cleanup(api, expected_main, root, delete=delete_with_lease):
    require(SHA.fullmatch(expected_main) is not None, 'invalid expected main SHA')
    results = []
    for branch, expected, archive in TARGETS:
        row = {'branch': branch, 'expected_sha': expected, 'archive_tag': archive,
               'recovery': archive or 'main ancestor ' + expected, 'status': 'blocked'}
        try:
            require(exact_ref(api, 'refs/heads/main') == expected_main, 'main changed; stop this deletion')
            head = exact_ref(api, 'refs/heads/' + branch)
            if head is None:
                # Absence alone cannot prove that the advertised recovery anchor
                # exists. Do not create new archives for a vanished branch.
                if archive:
                    require(exact_ref(api, 'refs/tags/' + archive) == expected,
                            'branch absent but archive recovery is unverified')
                else:
                    comparison = api.request(f'/repos/{REPO}/compare/{expected}...{expected_main}')
                    require(comparison['status'] in ('ahead', 'identical')
                            and comparison['merge_base_commit']['sha'] == expected,
                            'branch absent but main-ancestor recovery is unverified')
                row['status'] = 'already-absent'
                results.append(row)
                continue
            require(head == expected, 'branch changed; preserve new work')
            if archive:
                tag = 'refs/tags/' + archive
                saved = exact_ref(api, tag)
                require(saved in (None, expected), 'archive tag conflict; preserve branch')
                if saved is None:
                    api.request(f'/repos/{REPO}/git/refs', method='POST', data={'ref': tag, 'sha': expected})
                require(exact_ref(api, tag) == expected, 'archive readback mismatch; preserve branch')
            else:
                comparison = api.request(f'/repos/{REPO}/compare/{expected}...{expected_main}')
                require(comparison['status'] in ('ahead', 'identical')
                        and comparison['merge_base_commit']['sha'] == expected,
                        'tip is not a retained main ancestor; preserve branch')
            require(exact_ref(api, 'refs/heads/main') == expected_main, 'main changed before deletion')
            require(exact_ref(api, 'refs/heads/' + branch) == expected, 'branch changed before deletion')
            # Unlike REST DELETE, this is an atomic expected-old-SHA deletion.
            require(delete(branch, expected, root), 'atomic deletion failed or lease changed; no retry')
            require(exact_ref(api, 'refs/heads/' + branch) is None, 'deletion readback not absent')
            row['status'] = 'deleted'
        except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
            # No provider response or token-bearing process details enter logs.
            row['reason'] = str(error) if isinstance(error, ValueError) else type(error).__name__
        results.append(row)
    return {'repository': REPO, 'main_sha': expected_main, 'targets': results,
            'success': all(r['status'] in ('deleted', 'already-absent') for r in results)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-main', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
            and os.environ.get('GITHUB_REPOSITORY') == REPO
            and os.environ.get('GITHUB_SHA') == args.expected_main, 'exact manual repository dispatch required')
    root = Path(__file__).resolve().parents[1]
    head = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()
    require(head == args.expected_main, 'maintenance checkout SHA mismatch')
    report = cleanup(Api(REPO), args.expected_main, root)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
