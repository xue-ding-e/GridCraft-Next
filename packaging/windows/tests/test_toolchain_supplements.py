"""Original runtime notices: byte provenance and mandatory downstream inclusion."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1]
sys.path.insert(0, str(SCRIPTS))
import release_bundle as bundle
from test_release_bundle import create_fixture


class RuntimeSupplements(unittest.TestCase):
    def test_checked_in_fixed_rust_originals_and_map(self):
        root = SCRIPTS / 'toolchain-license-supplements'
        manifest = json.loads((root / 'runtime-supplements.json').read_text())
        commit = 'b940084d7eb6a299eb4bfeb8e34901bc051e7ac4'
        self.assertEqual(manifest['applicable_rust_commit'], commit)
        self.assertEqual(manifest['applicable_rust_release'], '1.99.0')
        self.assertEqual(manifest['target'], bundle.TARGET)
        self.assertEqual({row['file'] for row in manifest['files']}, {'compiler-builtins-LICENSE.txt', 'libm-LICENSE.txt'})
        expected_map = {}
        for row in manifest['files']:
            original = (root / row['file']).read_bytes()
            digest = hashlib.sha256(original).hexdigest()
            self.assertEqual(len(original), row['bytes'])
            self.assertEqual(digest, row['original_sha256'])
            self.assertEqual(row['source_commit'], commit)
            self.assertTrue(row['source_url'].startswith(f'https://raw.githubusercontent.com/rust-lang/rust/{commit}/library/compiler-builtins/'))
            self.assertEqual(row['collector_role'], 'rust-runtime-fixed-commit-license')
            expected_map[digest[:16] + '-' + row['file']] = digest
        self.assertEqual(manifest['required_public_legal'], expected_map)

    def test_matching_review_cannot_omit_collected_runtime_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, evidence, _ = create_fixture(root)
            chain = evidence['toolchain']
            digest = 'd' * 64
            chain['toolchain']['files'].append({'role': 'rust-runtime-fixed-commit-license',
                'name': 'compiler-builtins-LICENSE.txt', 'bytes': 123, 'sha256': digest,
                'file_version': None, 'legal_file': digest[:16] + '-compiler-builtins-LICENSE.txt'})
            path = root / 'packaging/windows/windows-release-review.json'
            review = bundle.read_json(path)
            review['toolchain_review_fingerprint'] = bundle.toolchain_fingerprint(chain)
            bundle.write_json(path, review)
            with self.assertRaisesRegex(ValueError, 'original notices'):
                bundle.review_toolchain(root, evidence['inventory_review_fingerprint'], chain)
            review['required_toolchain_notices']['toolchain-compiler-builtins-LICENSE.txt'] = digest
            bundle.write_json(path, review)
            self.assertEqual(bundle.review_toolchain(root, evidence['inventory_review_fingerprint'], chain), review)

    def test_non_x64_public_path_is_rejected_before_compilation(self):
        script = (SCRIPTS / 'package.ps1').read_text()
        self.assertLess(script.index("if ($Arch -ne 'x64' -and -not $ValidationOnly)"), script.index('cargo build --release'))
        self.assertIn('/packaging/windows/toolchain-license-supplements/** -text', (SCRIPTS.parents[1] / '.gitattributes').read_text())


if __name__ == '__main__':
    unittest.main()
