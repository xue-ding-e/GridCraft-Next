"""Windows response-file parsing only; these tests never run a compiler or executable."""
import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('capture_response', Path(__file__).parents[1] / 'capture-msvc-link.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


class ResponseFiles(unittest.TestCase):
    def test_newlines_normalized_only_for_metadata_not_file_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rsp = root / 'probe args.rsp'
            data = '/NOLOGO\r\n/OUT:"output with spaces.exe"\r\n'.encode('utf-16')
            rsp.write_bytes(data)
            receipts = []
            with patch.object(capture, 'windows_tokens', return_value=['/NOLOGO']) as parser:
                capture.expand_args(['@' + str(rsp)], root, receipts)
                self.assertNotIn('\r', parser.call_args.args[0])
                self.assertNotIn('\n', parser.call_args.args[0])
            self.assertEqual(rsp.read_bytes(), data)
            self.assertEqual((root / receipts[0]['copy']).read_bytes(), data)
            self.assertEqual(receipts[0]['sha256'], hashlib.sha256(data).hexdigest())

    @unittest.skipUnless(os.name == 'nt', 'Requires actual Windows CommandLineToArgvW; mandatory hosted Windows test')
    def test_multiline_unicode_and_utf8_response_files_keep_quoted_paths(self):
        for encoding, newline in [('utf-16', '\r\n'), ('utf-8-sig', '\n')]:
            with self.subTest(encoding=encoding), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                rsp = root / 'probe args.rsp'
                output = root / 'output with spaces.exe'
                obj = root / 'object with spaces.obj'
                lines = ['/NOLOGO', '/OUT:"' + str(output) + '"', '"' + str(obj) + '"', 'kernel32.lib']
                data = (newline.join(lines) + newline).encode(encoding)
                rsp.write_bytes(data)
                receipts = []
                flat = capture.expand_args(['@' + str(rsp)], root, receipts)
                self.assertEqual(flat, ['/NOLOGO', '/OUT:' + str(output), str(obj), 'kernel32.lib'])
                self.assertEqual(rsp.read_bytes(), data)
                self.assertEqual((root / receipts[0]['copy']).read_bytes(), data)
                self.assertEqual(receipts[0]['sha256'], hashlib.sha256(data).hexdigest())


if __name__ == '__main__':
    unittest.main()
