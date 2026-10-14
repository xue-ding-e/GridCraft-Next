import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('summary',Path(__file__).parents[1] / 'summarize-msvc-link-inputs.py');v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
class LinkSummaryTests(unittest.TestCase):
    def make(self,root):
        (root/'public').mkdir();(root/'private').mkdir();(root/'bin').mkdir()
        finals=[]
        for i,(name,hashed) in enumerate((('gridcraft.exe','gridcraft-a1234.exe'),('gridcraft-cli.exe','gridcraft_cli-b5678.exe'))):
            d=root/'private'/str(i);d.mkdir();p=d/hashed;p.write_bytes(b'binary'+bytes([i]));final=root/'bin'/name;final.write_bytes(p.read_bytes());finals.append(final)
            log=d/'link.log';log.write_text('private path');lib=d/'libucrt.lib';lib.write_bytes(b'lib')
            data={'schema':1,'linker_sha256':'a'*64,'output_path':str(p),'output_sha256':v.sha(p),'exit_code':0,'link_log':str(log),'link_log_sha256':v.sha(log),'runtime_libraries':[{'path':str(lib),'name':lib.name,'bytes':3,'sha256':v.sha(lib)}],'unresolved':[]}
            (d/'invocation.json').write_text(json.dumps(data))
        return finals
    def test_hashed_cargo_outputs_match_final_binaries(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);gui,cli=self.make(root);self.assertEqual(v.main(root,gui,cli),0)
            public=(root/'public/link-summary.json').read_text();self.assertNotIn(str(root),public)
            self.assertEqual({x['name'] for x in json.loads(public)['executables']},{'gridcraft.exe','gridcraft-cli.exe'})
    def test_changed_final_binary_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);gui,cli=self.make(root);gui.write_bytes(b'changed');self.assertEqual(v.main(root,gui,cli),2)
            self.assertIn('FINAL_CARGO_BINARY_DIFFERS_FROM_LINK_OUTPUT',json.loads((root/'public/link-summary.json').read_text())['unresolved'])
    def test_missing_link_is_unproven(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);gui,cli=self.make(root);(root/'private/1/invocation.json').rename(root/'private/1/not-a-receipt.json');self.assertEqual(v.main(root,gui,cli),2)
if __name__=='__main__':unittest.main()
