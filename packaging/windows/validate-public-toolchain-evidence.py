"""Fail closed on the explicitly allowed public toolchain evidence surface.

This checks evidence structure and byte hashes, not license sufficiency or release approval.
No paths, environment dictionaries, command lines or raw linker output may enter JSON.
Original selected-installation legal texts are preserved rather than rewritten.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,4}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
COMMIT = re.compile(r"[a-f0-9]{40}\Z")
CODE = re.compile(r"[A-Z][A-Z0-9_]*\Z")
ROLE = re.compile(r"[a-z][a-z0-9-]*\Z")
LEGAL_EXTENSIONS = {'', '.txt', '.rtf', '.html', '.htm', '.md'}
LEGAL_NAME = re.compile(r'license|eula|redist|third.?party|notice|copyright|^MIT\.txt$|^Apache-2\.0\.txt$|^Unicode-3\.0\.txt$', re.I)
COMMON = {'schema', 'evidence_only', 'distribution_approved', 'unresolved'}
SCHEMAS = {
    'toolchain-summary.json': (2, COMMON | {'target', 'rust_release', 'rust_commit', 'cargo_release', 'llvm_version', 'msvc_toolset_version', 'windows_sdk_version', 'ucrt_version', 'files', 'missing_files'}),
    'selection-summary.json': (1, COMMON | {'target', 'visual_studio_version', 'visual_studio_product', 'msvc_toolset_version', 'windows_sdk_version', 'selected_files', 'applied_to_process', 'reviewed_versions_requested'}),
    'link-summary.json': (1, COMMON | {'target', 'executables'}),
    'link-probe-summary.json': (1, COMMON | {'executed_binary', 'passed', 'argv_passthrough', 'baseline_equals_captured', 'exit_code_passthrough', 'binary_sha256', 'runtime_libraries'}),
    'toolchain-failure.json': (2, COMMON),
    'selection-failure.json': (1, COMMON),
}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def matches(pattern, value):
    return type(value) is str and pattern.fullmatch(value) is not None


def basename(value):
    return (type(value) is str and 0 < len(value) <= 255 and value not in ('.', '..')
            and not value.endswith((' ', '.'))
            and not any(ord(c) < 32 or ord(c) == 127 or c in '<>:"/\\|?*' for c in value))


def fields(value, expected, code):
    require(type(value) is dict and set(value) == expected, code)


def array(value, code):
    require(type(value) is list, code)
    return value


def issue_codes(value, code):
    require(type(value) is list and all(matches(CODE, x) for x in value), code)


def digest(value, code, nullable=False):
    require((nullable and value is None) or matches(HASH, value), code)


def version(value, code, nullable=False):
    require((nullable and value is None) or matches(VERSION, value), code)


def runtime_libraries(value, code):
    for lib in array(value, code):
        fields(lib, {'name', 'bytes', 'sha256'}, code)
        require(basename(lib['name']) and lib['name'].lower().endswith('.lib'), code)
        require(type(lib['bytes']) is int and lib['bytes'] >= 0, code)
        digest(lib['sha256'], code)


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        require(key not in obj, 'DUPLICATE_JSON_FIELD')
        obj[key] = value
    return obj


def no_nonfinite_constant(_value):
    raise ValueError('NONFINITE_JSON_NUMBER')


def check(directory):
    root = Path(directory)
    require(root.is_dir() and not root.is_symlink(), 'PUBLIC_DIRECTORY_REQUIRED')
    # Reject symlinks before reading JSON or legal bytes, including a linked legal/ directory.
    actual = set()
    for p in root.rglob('*'):
        require(not p.is_symlink(), 'PUBLIC_SYMLINK_FORBIDDEN')
        if p.is_file():
            actual.add(p.relative_to(root).as_posix())
        else:
            require(p.is_dir() and p.relative_to(root).as_posix() == 'legal', 'UNEXPECTED_PUBLIC_DIRECTORY')
    summaries = list(root.glob('*.json'))
    require(bool(summaries), 'PUBLIC_SUMMARY_REQUIRED')
    referenced = set()
    for path in summaries:
        require(path.name in SCHEMAS, 'UNEXPECTED_PUBLIC_JSON')
        obj = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique_object,
                         parse_constant=no_nonfinite_constant)
        schema, expected = SCHEMAS[path.name]
        fields(obj, expected, 'UNEXPECTED_PUBLIC_FIELDS')
        require(type(obj['schema']) is int and obj['schema'] == schema, 'INVALID_SCHEMA')
        require(obj['distribution_approved'] is False and obj['evidence_only'] is True, 'EVIDENCE_MUST_NOT_APPROVE_RELEASE')
        issue_codes(obj['unresolved'], 'INVALID_ISSUE_CODE')
        if 'target' in obj:
            require(type(obj['target']) is str and obj['target'] == 'x86_64-pc-windows-msvc', 'UNEXPECTED_TARGET')
        if path.name == 'selection-summary.json':
            for key in ('visual_studio_version', 'msvc_toolset_version', 'windows_sdk_version'):
                version(obj[key], 'INVALID_VERSION')
            require(type(obj['visual_studio_product']) is str and obj['visual_studio_product'] in
                    {'Microsoft.VisualStudio.Product.' + x for x in ('Enterprise', 'Professional', 'Community', 'BuildTools')}, 'UNEXPECTED_VS_PRODUCT')
            for key in ('applied_to_process', 'reviewed_versions_requested'):
                require(type(obj[key]) is bool, 'INVALID_SELECTION_BOOLEAN')
            for item in array(obj['selected_files'], 'INVALID_SELECTED_FILES'):
                fields(item, {'name', 'sha256'}, 'UNSAFE_SELECTED_FILE_METADATA')
                require(basename(item['name']), 'UNSAFE_SELECTED_FILE_METADATA')
                digest(item['sha256'], 'UNSAFE_SELECTED_FILE_METADATA')
        elif path.name == 'toolchain-summary.json':
            version(obj['rust_release'], 'INVALID_VERSION')
            require(matches(COMMIT, obj['rust_commit']), 'INVALID_RUST_COMMIT')
            # Collector emits null for unavailable numeric version metadata; it is never
            # allowed to substitute arbitrary strings, collections, or missing fields.
            for key in ('cargo_release', 'llvm_version', 'msvc_toolset_version', 'windows_sdk_version', 'ucrt_version'):
                version(obj[key], 'INVALID_VERSION', nullable=True)
            for item in array(obj['files'], 'INVALID_FILES'):
                fields(item, {'role', 'name', 'bytes', 'sha256', 'file_version', 'legal_file'}, 'UNEXPECTED_FILE_FIELDS')
                require(basename(item['name']) and matches(ROLE, item['role']), 'UNSAFE_FILE_METADATA')
                digest(item['sha256'], 'UNSAFE_FILE_METADATA')
                require(type(item['bytes']) is int and item['bytes'] >= 0, 'INVALID_BYTE_COUNT')
                version(item['file_version'], 'INVALID_FILE_VERSION', nullable=True)
                if item['legal_file'] is not None:
                    require(basename(item['legal_file']), 'UNSAFE_LEGAL_FILENAME')
                    require(Path(item['name']).suffix.lower() in LEGAL_EXTENSIONS
                            and Path(item['legal_file']).suffix.lower() in LEGAL_EXTENSIONS
                            and LEGAL_NAME.search(item['name']) is not None, 'UNSAFE_LEGAL_FILE_TYPE')
                    require(item['legal_file'] == item['sha256'][:16] + '-' + item['name'], 'INVALID_LEGAL_FILE_BINDING')
                    require(item['bytes'] <= 8 * 1024 * 1024, 'LEGAL_FILE_TOO_LARGE')
                    rel = 'legal/' + item['legal_file']; f = root / rel
                    require(f.is_file() and not f.is_symlink(), 'LEGAL_FILE_REQUIRED')
                    data = f.read_bytes()
                    require(len(data) == item['bytes'] and hashlib.sha256(data).hexdigest() == item['sha256'], 'LEGAL_BYTES_MISMATCH')
                    referenced.add(rel)
            for item in array(obj['missing_files'], 'INVALID_MISSING_FILES'):
                fields(item, {'role', 'name'}, 'UNSAFE_MISSING_FILE_METADATA')
                require(matches(ROLE, item['role']) and basename(item['name']), 'UNSAFE_MISSING_FILE_METADATA')
        elif path.name == 'link-probe-summary.json':
            require(obj['executed_binary'] is False, 'PROBE_MUST_NOT_EXECUTE_BINARY')
            for key in ('passed', 'argv_passthrough', 'baseline_equals_captured', 'exit_code_passthrough'):
                require(type(obj[key]) is bool, 'INVALID_PROBE_BOOLEAN')
            digest(obj['binary_sha256'], 'INVALID_PROBE_HASH', nullable=True)
            runtime_libraries(obj['runtime_libraries'], 'UNSAFE_PROBE_RUNTIME_METADATA')
        elif path.name == 'link-summary.json':
            for exe in array(obj['executables'], 'INVALID_EXECUTABLES'):
                fields(exe, {'name', 'sha256', 'linker_name', 'linker_sha256', 'runtime_libraries', 'unresolved'}, 'UNEXPECTED_EXECUTABLE_FIELDS')
                require(type(exe['name']) is str and exe['name'] in ('gridcraft.exe', 'gridcraft-cli.exe')
                        and type(exe['linker_name']) is str and exe['linker_name'] == 'link.exe', 'UNEXPECTED_EXECUTABLE_IDENTITY')
                digest(exe['sha256'], 'INVALID_EXECUTABLE_HASH', nullable=True)
                digest(exe['linker_sha256'], 'INVALID_EXECUTABLE_HASH')
                issue_codes(exe['unresolved'], 'INVALID_EXECUTABLE_ISSUE')
                runtime_libraries(exe['runtime_libraries'], 'UNSAFE_RUNTIME_METADATA')
    require(actual == referenced | {p.name for p in summaries}, 'UNREFERENCED_PUBLIC_FILE')
    return {'summaries': len(summaries), 'legal_files': len(referenced), 'privacy_structure_checked': True, 'distribution_approved': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('directories', nargs='+')
    args = parser.parse_args()
    # Results intentionally omit local directory names and paths, even on read/parse errors.
    try:
        results = [check(directory) for directory in args.directories]
    except Exception:
        print('PUBLIC_TOOLCHAIN_EVIDENCE_VALIDATION_FAILED')
        raise SystemExit(1)
    for result in results:
        print(json.dumps(result, sort_keys=True))
