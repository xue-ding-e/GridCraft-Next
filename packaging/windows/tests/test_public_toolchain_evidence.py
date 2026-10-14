import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('validator', Path(__file__).parents[1] / 'validate-public-toolchain-evidence.py')
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)
DATA = b'Original legal text\r\n'
SHA = hashlib.sha256(DATA).hexdigest()
LEGAL = SHA[:16] + '-sdk_license.rtf'
TARGET = 'x86_64-pc-windows-msvc'


def common(schema=1):
    return {'schema': schema, 'evidence_only': True, 'distribution_approved': False, 'unresolved': ['LEGAL_TERMS_NOT_REVIEWED']}


def samples():
    runtime = {'name': 'libcmt.lib', 'bytes': 123, 'sha256': SHA}
    return {
        'toolchain-summary.json': dict(common(2), target=TARGET, rust_release='1.99.0', rust_commit='b940084d7eb6a299eb4bfeb8e34901bc051e7ac4', cargo_release='1.99.0', llvm_version='23.1.1', msvc_toolset_version='14.44.35207', windows_sdk_version='10.0.26100.0', ucrt_version='10.0.26100.0', files=[{'role': 'microsoft-selected-sdk-legal-candidate', 'name': 'sdk_license.rtf', 'bytes': len(DATA), 'sha256': SHA, 'file_version': None, 'legal_file': LEGAL}], missing_files=[{'role': 'rust-std-original-license', 'name': 'MIT.txt'}]),
        'selection-summary.json': dict(common(), target=TARGET, visual_studio_version='17.14.37710.0', visual_studio_product='Microsoft.VisualStudio.Product.Enterprise', msvc_toolset_version='14.44.35207', windows_sdk_version='10.0.26100.0', selected_files=[{'name': 'link.exe', 'sha256': SHA}], applied_to_process=True, reviewed_versions_requested=False),
        'link-summary.json': dict(common(), target=TARGET, executables=[{'name': 'gridcraft.exe', 'sha256': SHA, 'linker_name': 'link.exe', 'linker_sha256': SHA, 'runtime_libraries': [runtime], 'unresolved': ['STATIC_RUNTIME_INPUTS_UNPROVEN']}]),
        'link-probe-summary.json': dict(common(), executed_binary=False, passed=True, argv_passthrough=True, baseline_equals_captured=True, exit_code_passthrough=True, binary_sha256=SHA, runtime_libraries=[runtime]),
        'toolchain-failure.json': common(2),
        'selection-failure.json': common(),
    }


def nodes(value, path=()):
    yield path, value
    if type(value) is dict:
        for key, item in value.items():
            yield from nodes(item, path + (key,))
    elif type(value) is list:
        for index, item in enumerate(value):
            yield from nodes(item, path + (index,))


def replace(obj, path, value):
    obj = copy.deepcopy(obj)
    if not path:
        return value
    parent = obj
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    return obj


class PublicEvidenceTests(unittest.TestCase):
    def write(self, root, filename, obj):
        if filename == 'toolchain-summary.json':
            (root / 'legal').mkdir(exist_ok=True)
            (root / 'legal' / LEGAL).write_bytes(DATA)
        (root / filename).write_text(json.dumps(obj), encoding='utf-8')

    def rejected(self, filename, obj, code=None):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); self.write(root, filename, obj)
            with self.assertRaisesRegex(ValueError, code or '.'):
                v.check(root)

    def test_all_six_precise_summary_schemas_accepted(self):
        for name, obj in samples().items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                root = Path(d); self.write(root, name, obj)
                self.assertEqual(v.check(root)['summaries'], 1)
                if name == 'toolchain-summary.json':
                    self.assertEqual((root / 'legal' / LEGAL).read_bytes(), DATA)

    def test_every_object_requires_every_field(self):
        for name, original in samples().items():
            for path, value in nodes(original):
                if type(value) is dict:
                    for key in value:
                        missing = copy.deepcopy(value); del missing[key]
                        with self.subTest(name=name, path=path, key=key):
                            self.rejected(name, replace(original, path, missing))

    def test_every_object_rejects_unknown_fields_and_nonobjects(self):
        for name, original in samples().items():
            for path, value in nodes(original):
                if type(value) is dict:
                    for bad in ([], None, 'C:\\private\\object', dict(value, private_path='C:\\secret')):
                        with self.subTest(name=name, path=path, bad=bad):
                            self.rejected(name, replace(original, path, bad))

    def test_every_scalar_rejects_nested_path_objects_and_lists(self):
        for name, original in samples().items():
            for path, value in nodes(original):
                if type(value) not in (dict, list):
                    for bad in ({'private_path': 'C:\\Users\\runner\\secret'}, ['C:\\secret']):
                        with self.subTest(name=name, path=path):
                            self.rejected(name, replace(original, path, bad))

    def test_every_array_rejects_string_dict_null_and_scalar(self):
        for name, original in samples().items():
            for path, value in nodes(original):
                if type(value) is list:
                    for bad in ('C:\\private', {}, None, 1, True):
                        with self.subTest(name=name, path=path, bad=bad):
                            self.rejected(name, replace(original, path, bad))

    def test_each_schema_exact_integer_not_bool_or_float(self):
        for name, original in samples().items():
            for bad in (True, False, 1.0, 2.0, 0, 3, '1', 'C:\\private', None):
                with self.subTest(name=name, bad=bad):
                    self.rejected(name, dict(original, schema=bad), 'INVALID_SCHEMA')

    def test_every_boolean_is_a_json_boolean(self):
        for name, original in samples().items():
            for path, value in nodes(original):
                if type(value) is bool:
                    for bad in (0, 1, 'true', 'C:\\private', None):
                        with self.subTest(name=name, path=path, bad=bad):
                            self.rejected(name, replace(original, path, bad))

    def test_all_hash_and_commit_fields_are_typed_and_full_length(self):
        for name, original in samples().items():
            for path, value in nodes(original):
                if path and path[-1] in ('sha256', 'linker_sha256', 'binary_sha256', 'rust_commit'):
                    for bad in (0, False, 1.5, 'f' * 63, 'g' * 64, 'F' * 64, 'C:\\private'):
                        with self.subTest(name=name, path=path, bad=bad):
                            self.rejected(name, replace(original, path, bad))

    def test_versions_reject_non_numeric_strings_and_other_scalars(self):
        for name, original in samples().items():
            for path, value in nodes(original):
                if path and (str(path[-1]).endswith('_version') or path[-1] in ('rust_release', 'cargo_release')):
                    for bad in (0, False, 1.99, 'stable', 'C:\\private', '1.99.0\n'):
                        with self.subTest(name=name, path=path, bad=bad):
                            self.rejected(name, replace(original, path, bad))

    def test_byte_counts_reject_bool_float_negative_and_string(self):
        for name, original in samples().items():
            for path, value in nodes(original):
                if path and path[-1] == 'bytes':
                    for bad in (True, False, -1, 1.5, '123', None):
                        with self.subTest(name=name, path=path, bad=bad):
                            self.rejected(name, replace(original, path, bad))

    def test_null_only_allowed_for_documented_nullable_fields(self):
        for name, original in samples().items():
            for path, value in nodes(original):
                if not path or type(value) in (dict, list) or value is None:
                    continue
                nullable = (name == 'toolchain-summary.json' and len(path) == 1 and path[-1] in ('cargo_release', 'llvm_version', 'msvc_toolset_version', 'windows_sdk_version', 'ucrt_version')) or (name == 'link-summary.json' and path == ('executables', 0, 'sha256')) or (name == 'link-probe-summary.json' and path == ('binary_sha256',))
                if nullable:
                    with self.subTest(name=name, path=path), tempfile.TemporaryDirectory() as d:
                        root = Path(d); self.write(root, name, replace(original, path, None)); v.check(root)
                else:
                    with self.subTest(name=name, path=path):
                        self.rejected(name, replace(original, path, None))

    def test_issue_codes_are_strings_without_paths(self):
        for name, original in samples().items():
            for path, value in nodes(original):
                if path and path[-1] == 'unresolved':
                    for bad in ([False], [123], [None], ['C:\\private'], ['ISSUE\n'], [{'secret': 'value'}]):
                        with self.subTest(name=name, path=path):
                            self.rejected(name, replace(original, path, bad))

    def test_names_and_roles_reject_paths_and_control_characters(self):
        for name, original in samples().items():
            for path, value in nodes(original):
                if path and path[-1] in ('name', 'role', 'legal_file', 'linker_name'):
                    for bad in ('C:\\secret\\file', '../secret', 'bad\x00name', '.', 'name\n', 'name:stream'):
                        with self.subTest(name=name, path=path):
                            self.rejected(name, replace(original, path, bad))

    def test_legal_extension_allowlist_prevents_executable_library_or_archive(self):
        for extension in ('.exe', '.lib', '.dll', '.rlib', '.zip', '.cmd', '.ps1', '.EXE'):
            with self.subTest(extension=extension), tempfile.TemporaryDirectory() as d:
                root = Path(d); obj = samples()['toolchain-summary.json']
                item = obj['files'][0]; item['name'] = 'license' + extension; item['legal_file'] = SHA[:16] + '-' + item['name']
                (root / 'legal').mkdir(); (root / 'legal' / item['legal_file']).write_bytes(DATA)
                (root / 'toolchain-summary.json').write_text(json.dumps(obj))
                with self.assertRaisesRegex(ValueError, 'UNSAFE_LEGAL_FILE_TYPE'):
                    v.check(root)

    def test_original_executable_cannot_be_disguised_as_txt_copy(self):
        obj = samples()['toolchain-summary.json']; obj['files'][0]['name'] = 'license.exe'
        self.rejected('toolchain-summary.json', obj, 'UNSAFE_LEGAL_FILE_TYPE')

    def test_legal_copy_name_binds_hash_and_original_name(self):
        obj = samples()['toolchain-summary.json']; obj['files'][0]['legal_file'] = 'other-license.rtf'
        self.rejected('toolchain-summary.json', obj, 'INVALID_LEGAL_FILE_BINDING')

    def test_all_supported_legal_extensions_preserve_original_bytes(self):
        for extension in ('', '.txt', '.rtf', '.html', '.htm', '.md', '.TXT'):
            with self.subTest(extension=extension), tempfile.TemporaryDirectory() as d:
                root = Path(d); obj = samples()['toolchain-summary.json']; item = obj['files'][0]
                item['name'] = 'LICENSE' + extension; item['legal_file'] = SHA[:16] + '-' + item['name']
                (root / 'legal').mkdir(); (root / 'legal' / item['legal_file']).write_bytes(DATA)
                (root / 'toolchain-summary.json').write_text(json.dumps(obj))
                self.assertEqual(v.check(root)['legal_files'], 1)

    def test_nonlegal_file_metadata_has_explicit_null_copy_and_numeric_version(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); obj = samples()['toolchain-summary.json']
            obj['files'] = [{'role': 'msvc-build-tool-identity-only', 'name': 'link.exe', 'bytes': 123, 'sha256': SHA, 'file_version': '14.44.35207', 'legal_file': None}]
            (root / 'toolchain-summary.json').write_text(json.dumps(obj))
            self.assertEqual(v.check(root)['legal_files'], 0)

    def test_extra_raw_log_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); self.write(root, 'toolchain-summary.json', samples()['toolchain-summary.json'])
            (root / 'link.log').write_text('local paths')
            with self.assertRaisesRegex(ValueError, 'UNREFERENCED_PUBLIC_FILE'):
                v.check(root)

    def test_changed_legal_bytes_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); self.write(root, 'toolchain-summary.json', samples()['toolchain-summary.json'])
            (root / 'legal' / LEGAL).write_bytes(b'Changed')
            with self.assertRaisesRegex(ValueError, 'LEGAL_BYTES_MISMATCH'):
                v.check(root)

    def test_duplicate_json_key_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); obj = samples()['selection-failure.json']
            (root / 'selection-failure.json').write_text(json.dumps(obj)[:-1] + ', "schema": 1}')
            with self.assertRaisesRegex(ValueError, 'DUPLICATE_JSON_FIELD'):
                v.check(root)

    def test_nonfinite_numbers_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); obj = samples()['selection-failure.json']
            (root / 'selection-failure.json').write_text(json.dumps(obj).replace('"schema": 1', '"schema": NaN'))
            with self.assertRaisesRegex(ValueError, 'NONFINITE_JSON_NUMBER'):
                v.check(root)

    def test_cli_failure_does_not_print_input_or_local_path(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'selection-failure.json').write_text('{"private": "C:\\secret')
            result = subprocess.run([sys.executable, str(Path(v.__file__)), str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout.strip(), 'PUBLIC_TOOLCHAIN_EVIDENCE_VALIDATION_FAILED')
            self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
