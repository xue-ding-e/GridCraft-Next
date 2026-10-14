"""Produce a path-free final-link summary plus a runner-only collector input list."""
import argparse
import hashlib
import json
import re
from pathlib import Path

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def main(root, gui, cli):
    root=Path(root); output=[]; libraries=set(); evidence=[]; issues=[]
    final_paths={'gridcraft.exe':Path(gui),'gridcraft-cli.exe':Path(cli)}
    patterns={'gridcraft.exe':re.compile(r'gridcraft(?:-[0-9a-f]+)?\.exe',re.I),'gridcraft-cli.exe':re.compile(r'gridcraft[-_]cli(?:-[0-9a-f]+)?\.exe',re.I)}
    expected=set(final_paths); seen=set()
    for f in sorted((root/'private').glob('*/invocation.json')):
        item=json.loads(f.read_text()); p=Path(item['output_path']) if item.get('output_path') else None
        if not p:continue
        matches=[name for name,pattern in patterns.items() if pattern.fullmatch(p.name)]
        if len(matches)!=1:continue
        identity=matches[0]
        if identity in seen:issues.append('DUPLICATE_FINAL_LINK_RECORD')
        seen.add(identity); errors=list(item.get('unresolved',[])); evidence.extend([str(f),item['link_log']])
        final=final_paths[identity]
        if not final.is_file() or sha(final)!=item.get('output_sha256'):errors.append('FINAL_CARGO_BINARY_DIFFERS_FROM_LINK_OUTPUT')
        if item['exit_code']!=0:errors.append('FINAL_LINK_FAILED')
        if not p.is_file() or sha(p)!=item.get('output_sha256'):errors.append('FINAL_BINARY_CHANGED_AFTER_LINK')
        if not Path(item['link_log']).is_file() or sha(item['link_log'])!=item['link_log_sha256']:errors.append('LINK_LOG_HASH_MISMATCH')
        runtime=[]
        for lib in item['runtime_libraries']:
            path=Path(lib['path'])
            if not path.is_file() or sha(path)!=lib['sha256']:errors.append('RUNTIME_LIBRARY_CHANGED_AFTER_LINK');continue
            libraries.add(str(path)); runtime.append({k:lib[k] for k in ('name','bytes','sha256')})
            if re.fullmatch(r'(?:libcmtd|libvcruntimed|libucrtd|msvcrtd|vcruntimed|ucrtd|libcpmtd)\.lib',lib['name'],re.I):errors.append('DEBUG_RUNTIME_NOT_DISTRIBUTABLE')
        if not runtime:errors.append('STATIC_RUNTIME_INPUTS_UNPROVEN')
        output.append({'name':identity,'sha256':item.get('output_sha256'),'linker_name':'link.exe','linker_sha256':item['linker_sha256'],'runtime_libraries':runtime,'unresolved':sorted(set(errors))})
        issues.extend(errors)
    if seen!=expected:issues.append('BOTH_FINAL_EXECUTABLE_LINKS_REQUIRED')
    report={'schema':1,'evidence_only':True,'distribution_approved':False,'target':'x86_64-pc-windows-msvc','executables':output,'unresolved':sorted(set(issues))}
    (root/'public'/'link-summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (root/'private'/'collector-inputs.json').write_text(json.dumps({'linked_runtime_libraries':sorted(libraries),'link_evidence_files':sorted(set(evidence))},indent=2),encoding='utf-8')
    print(json.dumps({'final_executable_count':len(output),'runtime_library_count':len(libraries),'unresolved':report['unresolved'],'distribution_approved':False}))
    return 2 if issues else 0

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory');p.add_argument('--gui',required=True);p.add_argument('--cli',required=True);a=p.parse_args()
    try:raise SystemExit(main(a.directory,a.gui,a.cli))
    except Exception:print('MSVC_LINK_SUMMARY_FAILED: inspect runner-local evidence.');raise SystemExit(1)
