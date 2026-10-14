import importlib.util
import unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('capture',Path(__file__).parents[1] / 'capture-msvc-link.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
class LinkParserTests(unittest.TestCase):
    def test_searched_is_not_loaded(self):
        self.assertEqual(v.loaded_runtime_paths('Searching C:\\VS\\libcmt.lib:\n'),([],[]))
    def test_loaded_basename_binds_exact_search_path(self):
        text='Searching C:\\VS\\14.44\\libcmt.lib:\n  Found mainCRTStartup\n    Loaded LIBCMT.lib(exe_main.obj)\nSearching C:\\SDK\\libucrt.lib:\n  Loaded libucrt.lib(fopen.obj)\n'
        paths,issues=v.loaded_runtime_paths(text)
        self.assertEqual(paths,['C:\\SDK\\libucrt.lib','C:\\VS\\14.44\\libcmt.lib']);self.assertEqual(issues,[])
    def test_ambiguous_loaded_basename_stays_unresolved(self):
        self.assertEqual(v.loaded_runtime_paths(' Loaded libvcruntime.lib(a.obj)\n'),([],['LOADED_RUNTIME_PATH_UNRESOLVED']))
    def test_full_loaded_path_and_nonruntime_scope(self):
        text=' Loaded C:\\VS\\libvcruntime.lib(foo.obj)\n Loaded C:\\SDK\\kernel32.lib(foo.obj)\n'
        self.assertEqual(v.loaded_runtime_paths(text),(['C:\\VS\\libvcruntime.lib'],[]))
    def test_mismatched_search_cannot_lend_path(self):
        self.assertEqual(v.loaded_runtime_paths('Searching C:\\other\\oldnames.lib:\n Loaded libcmt.lib(foo.obj)\n'),([],['LOADED_RUNTIME_PATH_UNRESOLVED']))
if __name__=='__main__':unittest.main()
