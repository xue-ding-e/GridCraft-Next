"""Only the one pinned official DOCX is eligible for the public legal surface."""
import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from test_public_toolchain_evidence import samples

SCRIPTS = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('public_terms', SCRIPTS / 'validate-public-toolchain-evidence.py')
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class MicrosoftOriginals(unittest.TestCase):
    def test_manifest_and_exact_original_files(self):
        root = SCRIPTS / 'microsoft-runtime-terms'
        manifest = json.loads((root / 'microsoft-runtime-terms.json').read_text())
        self.assertEqual(manifest['target'], 'x86_64-pc-windows-msvc')
        self.assertEqual({row['file'] for row in manifest['files']}, {'Visual-Studio-2022-Enterprise-Professional-License-EN.docx', 'sdk_license.rtf', 'WINDOWS-RUNTIME-LICENSE-NOTICE.txt'})
        for row in manifest['files']:
            data = (root / row['file']).read_bytes()
            self.assertEqual(len(data), row['bytes'])
            self.assertEqual(hashlib.sha256(data).hexdigest(), row['sha256'])
            self.assertEqual(manifest['required_package_files'][row['file']], row['sha256'])
        attributes = (SCRIPTS.parents[1] / '.gitattributes').read_text()
        self.assertIn('/packaging/windows/microsoft-runtime-terms/** -text', attributes)

    def test_pinned_document_passes_and_no_arbitrary_office_exception_exists(self):
        name = 'Visual-Studio-2022-Enterprise-Professional-License-EN.docx'
        data = (SCRIPTS / 'microsoft-runtime-terms' / name).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        original = {'role': 'microsoft-fixed-original-license', 'name': name, 'bytes': len(data),
                    'sha256': digest, 'file_version': None, 'legal_file': digest[:16] + '-' + name}
        cases = [(original, data, True),
                 ({**original, 'name': 'different-license.docx', 'legal_file': digest[:16] + '-different-license.docx'}, data, False),
                 (original, data[:-1] + bytes([data[-1] ^ 1]), False)]
        changed = data + b'changed'
        changed_hash = hashlib.sha256(changed).hexdigest()
        cases.append(({**original, 'bytes': len(changed), 'sha256': changed_hash, 'legal_file': changed_hash[:16] + '-' + name}, changed, False))
        for row, content, accepted in cases:
            with self.subTest(name=row['name'], accepted=accepted), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / 'legal').mkdir()
                (root / 'legal' / row['legal_file']).write_bytes(content)
                summary = copy.deepcopy(samples()['toolchain-summary.json'])
                summary['files'] = [row]
                (root / 'toolchain-summary.json').write_text(json.dumps(summary))
                if accepted:
                    self.assertEqual(validator.check(root)['legal_files'], 1)
                else:
                    with self.assertRaises(ValueError): validator.check(root)
        self.assertNotIn('.docx', validator.LEGAL_EXTENSIONS)


if __name__ == '__main__':
    unittest.main()
