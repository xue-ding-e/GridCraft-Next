"""Transparent final-target link capture. Invoked by rustc through an installed-Python .cmd.

Runs the explicitly selected existing Microsoft linker once, forwarding argv and exit code.
All arguments, response files, full paths and linker text remain under RUNNER_TEMP/private.
Never prints linker text, downloads tools, accepts terms, or copies runtime libraries.
"""
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

RUNTIMES = {'libcmt.lib', 'libvcruntime.lib', 'libucrt.lib', 'oldnames.lib',
            'legacy_stdio_definitions.lib', 'legacy_stdio_wide_specifiers.lib',
            'libcpmt.lib', 'libconcrt.lib', 'msvcrt.lib', 'vcruntime.lib', 'ucrt.lib',
            'msvcprt.lib', 'libcmtd.lib', 'libvcruntimed.lib', 'libucrtd.lib',
            'msvcrtd.lib', 'vcruntimed.lib', 'ucrtd.lib', 'libcpmtd.lib'}

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()

def child_of(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False

def windows_tokens(text):
    count = ctypes.c_int()
    api = ctypes.windll.shell32.CommandLineToArgvW
    api.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    api.restype = ctypes.POINTER(ctypes.c_wchar_p)
    ptr = api('capture.exe ' + text, ctypes.byref(count))
    if not ptr:
        raise ValueError('RESPONSE_ARGUMENT_PARSE_FAILED')
    try:
        return [ptr[i] for i in range(1, count.value)]
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]; free.restype = ctypes.c_void_p
        free(ctypes.cast(ptr, ctypes.c_void_p))

def expand_args(args, directory, receipts, depth=0):
    if depth > 4:
        raise ValueError('RESPONSE_NESTING_LIMIT')
    result = []
    for arg in args:
        if not arg.startswith('@'):
            result.append(arg); continue
        path = Path(arg[1:].strip('"')).resolve()
        data = path.read_bytes()
        if len(data) > 16 * 1024 * 1024:
            raise ValueError('RESPONSE_SIZE_LIMIT')
        name = 'response-' + str(len(receipts)) + '.rsp'
        (directory / name).write_bytes(data)
        receipts.append({'path': str(path), 'copy': name, 'sha256': hashlib.sha256(data).hexdigest()})
        text = data.decode('utf-16') if data.startswith((b'\xff\xfe', b'\xfe\xff')) else data.decode('utf-8-sig')
        result.extend(expand_args(windows_tokens(text), directory, receipts, depth + 1))
    return result

def loaded_runtime_paths(text):
    """Conservative MS LINK English /VERBOSE:LIB parser; never equate 'searched' with loaded.

A basename-only Loaded record is accepted only under a matching full-path Searching
record. A full-path Loaded record is accepted directly. Ambiguity remains unresolved.
"""
    current = None
    found = set(); unresolved = []
    for line in text.splitlines():
        match = re.match(r'^\s*Searching\s+(.+?\.lib):\s*$', line, re.I)
        if match:
            current = match.group(1).strip('"'); continue
        match = re.match(r'^\s*Loaded\s+(.+?\.lib)\(.+\)\s*$', line, re.I)
        if not match:
            continue
        loaded = match.group(1).strip('"')
        # ntpath is needed for fixture tests on non-Windows hosts.
        import ntpath
        name = ntpath.basename(loaded).lower()
        if name not in RUNTIMES:
            continue
        if ntpath.isabs(loaded):
            found.add(loaded)
        elif current and ntpath.isabs(current) and ntpath.basename(current).lower() == name:
            found.add(current)
        else:
            unresolved.append('LOADED_RUNTIME_PATH_UNRESOLVED')
    return sorted(found, key=str.lower), sorted(set(unresolved))

def main():
    if os.name != 'nt':
        raise ValueError('WINDOWS_LINKER_REQUIRED')
    linker = Path(os.environ['GRIDCRAFT_REAL_LINK_EXE']).resolve()
    vcroot = Path(os.environ['VCToolsInstallDir']).resolve()
    sdkroot = Path(os.environ['WindowsSdkDir']).resolve()
    root = Path(os.environ['GRIDCRAFT_LINK_EVIDENCE_DIR']).resolve()
    temp = Path(os.environ['RUNNER_TEMP']).resolve()
    if linker.name.lower() != 'link.exe' or not child_of(linker, vcroot):
        raise ValueError('LINKER_OUTSIDE_SELECTED_MSVC')
    if not child_of(root, temp):
        raise ValueError('LINK_CAPTURE_MUST_BE_RUNNER_LOCAL')
    directory = root / 'private' / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    args = sys.argv[1:]; response_files = []
    record = {'schema': 1, 'linker_path': str(linker), 'linker_sha256': digest(linker),
              'arguments': args, 'expanded_arguments': [], 'response_files': response_files,
              'output_path': None, 'runtime_libraries': [], 'unresolved': []}
    output = None
    try:
        flat = expand_args(args, directory, response_files)
        record['expanded_arguments'] = flat
        outputs = [a[5:].strip('"') for a in flat if a.lower().startswith('/out:')]
        output = Path(outputs[-1]).resolve() if len(outputs) == 1 else None
        record['output_path'] = str(output) if output else None
    except Exception:
        # Evidence parsing must not prevent the unchanged real link from running.
        record['unresolved'].append('RESPONSE_ARGUMENT_CAPTURE_FAILED')
    # This is a child-process-only message-language selection, never an environment dump.
    os.environ['VSLANG'] = '1033'
    log = directory / 'link.log'
    with log.open('wb') as stream:
        result = subprocess.run([str(linker), *args, '/VERBOSE:LIB'], stdout=stream, stderr=subprocess.STDOUT, check=False)
    record['exit_code'] = result.returncode
    record['link_log'] = str(log)
    try:
        record['link_log_sha256'] = digest(log)
        if not output:
            record['unresolved'].append('FINAL_OUTPUT_IDENTITY_UNPROVEN')
        elif result.returncode == 0 and output.is_file():
            record['output_sha256'] = digest(output)
        if result.returncode == 0:
            # Paths from MS LINK can use the active Windows encoding. Reject unverifiable paths.
            text = log.read_bytes().decode('mbcs', errors='replace')
            paths, issues = loaded_runtime_paths(text); record['unresolved'].extend(issues)
            for p in paths:
                path = Path(p).resolve()
                if not path.is_file() or not (child_of(path, vcroot) or child_of(path, sdkroot)):
                    record['unresolved'].append('RESOLVED_RUNTIME_OUTSIDE_SELECTED_INSTALLATION'); continue
                record['runtime_libraries'].append({'path': str(path), 'name': path.name, 'bytes': path.stat().st_size, 'sha256': digest(path)})
            if not record['runtime_libraries']:
                record['unresolved'].append('NO_LOADED_RUNTIME_RECORDS')
    except Exception:
        record['unresolved'].append('POST_LINK_EVIDENCE_INCOMPLETE')
    try:
        (directory / 'invocation.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    except Exception:
        print('MSVC_LINK_RECEIPT_WRITE_FAILED: diagnostics remain runner-local.', file=sys.stderr)
    # Return the real linker's code even when independent evidence extraction was incomplete.
    if result.returncode != 0:
        print('MSVC linker failed; full diagnostics remain runner-local.', file=sys.stderr)
    return result.returncode

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        # Do not expose raw arguments, environment values or local paths through Cargo logs.
        print('MSVC_LINK_CAPTURE_FAILED: inspect runner-local evidence.', file=sys.stderr)
        sys.exit(1)
