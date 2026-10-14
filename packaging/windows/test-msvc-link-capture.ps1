<# Cheap hosted-Windows preflight for the existing MSVC linker capture.
Compiles our tiny synthetic C source and links it; NEVER executes any produced binary.
Uses only already-installed cl.exe/link.exe after select-installed-msvc-sdk.ps1.
All source/object/executable/arguments/logs stay runner-local. Emits only safe JSON.
This is a wrapper test, never evidence that the Rust GUI works or is legally distributable.
#>
[CmdletBinding()]
param([Parameter(Mandatory)][string] $OutputDirectory)
$ErrorActionPreference='Stop'
$PSNativeCommandUseErrorActionPreference=$false
if(-not $IsWindows -or -not $env:RUNNER_TEMP -or -not $env:VCToolsInstallDir){throw 'MSVC_PROBE_SELECTION_REQUIRED'}
$out=[IO.Path]::GetFullPath($OutputDirectory);$temp=[IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\')+'\'
if(-not $out.StartsWith($temp,[StringComparison]::OrdinalIgnoreCase)){throw 'MSVC_PROBE_MUST_BE_RUNNER_LOCAL'}
if(Test-Path -LiteralPath $out){throw 'MSVC_PROBE_OUTPUT_MUST_BE_NEW'}
$private=Join-Path $out 'private';$public=Join-Path $out 'public'
New-Item -ItemType Directory -Path $private,$public | Out-Null
$previous=[ordered]@{}
foreach($name in 'CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER','GRIDCRAFT_REAL_LINK_EXE','GRIDCRAFT_LINK_EVIDENCE_DIR'){$previous[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
$report=[ordered]@{schema=1;evidence_only=$true;distribution_approved=$false;executed_binary=$false;passed=$false;argv_passthrough=$false;baseline_equals_captured=$false;exit_code_passthrough=$false;binary_sha256=$null;runtime_libraries=@();unresolved=@()}
$failed=$null
try{
  $cl=Join-Path $env:VCToolsInstallDir 'bin\Hostx64\x64\cl.exe'
  $link=Join-Path $env:VCToolsInstallDir 'bin\Hostx64\x64\link.exe'
  $work=Join-Path $private 'path with spaces';New-Item -ItemType Directory -Path $work | Out-Null
  $src=Join-Path $work 'probe.c';$obj=Join-Path $work 'probe.obj';$exe=Join-Path $work 'probe.exe';$rsp=Join-Path $work 'probe args.rsp'
  # Our own tiny source. /MT ensures this probe actually exercises release static CRT inputs.
  $source = '#include <stdio.h>' + "`r`n" + 'int main(void) { return printf("GridCraft link capture probe\n") < 0; }' + "`r`n"
  [IO.File]::WriteAllText($src,$source,[Text.Encoding]::ASCII)
  & $cl /nologo /c /MT /Od "/Fo$obj" $src *> (Join-Path $private 'compile.log')
  if($LASTEXITCODE -ne 0){throw 'MSVC_PROBE_COMPILE_FAILED'}
  # Same exact response bytes and output path for baseline and capture. /Brepro removes
  # wall-clock linker timestamp variation so an extra diagnostics flag cannot hide drift.
  $linkArgs=@('/NOLOGO','/INCREMENTAL:NO','/BREPRO','/SUBSYSTEM:CONSOLE',('/OUT:"'+$exe+'"'),('"'+$obj+'"'),'kernel32.lib')
  [IO.File]::WriteAllLines($rsp,$linkArgs,[Text.Encoding]::Unicode)
  & $link "@$rsp" *> (Join-Path $private 'baseline-link.log')
  if($LASTEXITCODE -ne 0){throw 'MSVC_PROBE_BASELINE_LINK_FAILED'}
  $baseline=(Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
  $capture=Join-Path $private 'capture'
  & (Join-Path $PSScriptRoot 'enable-msvc-link-capture.ps1') -OutputDirectory $capture
  $wrapper=$env:CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER
  & $wrapper "@$rsp" *> (Join-Path $private 'wrapper-console.log')
  $successCode=$LASTEXITCODE
  if($successCode -ne 0){throw 'MSVC_PROBE_CAPTURE_LINK_FAILED'}
  $receipts=@(Get-ChildItem -LiteralPath (Join-Path $capture 'private') -Filter invocation.json -File -Recurse)
  if($receipts.Count -ne 1){throw 'MSVC_PROBE_SUCCESS_RECEIPT_MISSING'}
  $record=Get-Content -LiteralPath $receipts[0].FullName -Raw | ConvertFrom-Json
  $actual=(Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
  if($record.exit_code -ne $successCode -or $record.arguments.Count -ne 1 -or $record.arguments[0] -ne "@$rsp"){throw 'MSVC_PROBE_ARGUMENT_OR_EXIT_DRIFT'}
  if($record.response_files.Count -ne 1 -or $record.response_files[0].sha256 -ne (Get-FileHash -LiteralPath $rsp -Algorithm SHA256).Hash.ToLowerInvariant()){throw 'MSVC_PROBE_RESPONSE_BYTES_DRIFT'}
  if($record.output_path -ne $exe){throw 'MSVC_PROBE_OUTPUT_PATH_DRIFT'}
  if($record.output_sha256 -ne $actual){throw 'MSVC_PROBE_RECEIPT_HASH_DRIFT'}
  if($actual -ne $baseline){throw 'MSVC_PROBE_BASELINE_HASH_DRIFT'}
  if($record.unresolved.Count -gt 0){
    $codes = @($record.unresolved | Where-Object { $_ -cmatch '^[A-Z][A-Z0-9_]+$' })
    $logBytes = [IO.File]::ReadAllBytes($record.link_log)
    $logText = [Text.Encoding]::UTF8.GetString($logBytes)
    # Bounded shapes only: no raw linker lines, paths, arguments or environment values.
    [ordered]@{codes=$codes;log_bytes=$logBytes.Length;nul_bytes=@($logBytes | Where-Object {$_ -eq 0}).Count;
      searching_lines=[regex]::Matches($logText,'(?m)^\s*Searching ').Count;
      loaded_lines=[regex]::Matches($logText,'(?m)^\s*Loaded ').Count;
      runtime_mentions=[regex]::Matches($logText,'(?i)libcmt\.lib|libucrt\.lib|libvcruntime\.lib').Count} |
      ConvertTo-Json -Compress | Write-Output
    throw 'MSVC_PROBE_RUNTIME_PARSE_UNRESOLVED'
  }
  $names=@($record.runtime_libraries | ForEach-Object {$_.name.ToLowerInvariant()})
  if('libcmt.lib' -notin $names -or 'libucrt.lib' -notin $names){throw 'MSVC_PROBE_STATIC_CRT_NOT_PROVEN'}
  foreach($lib in $record.runtime_libraries){if((Get-FileHash -LiteralPath $lib.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $lib.sha256){throw 'MSVC_PROBE_RUNTIME_HASH_DRIFT'}}
  $report.argv_passthrough=$true;$report.baseline_equals_captured=$true;$report.binary_sha256=$actual
  $report.runtime_libraries=@($record.runtime_libraries | ForEach-Object {[ordered]@{name=$_.name;bytes=$_.bytes;sha256=$_.sha256}})
  # Intentional missing object: compare the direct linker's real failure code to wrapper exit.
  $missing=Join-Path $work 'deliberately missing.obj';$badOut=Join-Path $work 'failed.exe'
  $badArgs=@('/NOLOGO',('/OUT:'+ $badOut),$missing)
  & $link @badArgs *> (Join-Path $private 'baseline-failure.log');$expectedFailure=$LASTEXITCODE
  if($expectedFailure -eq 0){throw 'MSVC_PROBE_FAILURE_FIXTURE_DID_NOT_FAIL'}
  & $wrapper @badArgs *> (Join-Path $private 'wrapper-failure-console.log');$capturedFailure=$LASTEXITCODE
  if($capturedFailure -ne $expectedFailure){throw 'MSVC_PROBE_FAILURE_EXIT_DRIFT'}
  $all=@(Get-ChildItem -LiteralPath (Join-Path $capture 'private') -Filter invocation.json -File -Recurse | ForEach-Object {Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json})
  $bad=@($all | Where-Object {$_.output_path -eq $badOut})
  if($bad.Count -ne 1 -or $bad[0].exit_code -ne $expectedFailure){throw 'MSVC_PROBE_FAILURE_RECEIPT_MISSING'}
  $report.exit_code_passthrough=$true;$report.passed=$true
}catch{
  $_ | Out-String | Set-Content -LiteralPath (Join-Path $private 'error.txt') -Encoding utf8NoBOM
  $failed=if($_.Exception.Message -match '^[A-Z][A-Z0-9_]+$'){$_.Exception.Message}else{'MSVC_PROBE_UNEXPECTED_ERROR'}
  $report.unresolved=@($failed)
}finally{
  foreach($name in $previous.Keys){[Environment]::SetEnvironmentVariable($name,$previous[$name],'Process')}
  $report | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath (Join-Path $public 'link-probe-summary.json') -Encoding utf8NoBOM
  # Fixed schema only; no private detail is emitted to workflow output.
  $report | ConvertTo-Json -Depth 7 -Compress | Write-Output
}
if($failed){throw $failed}
# Both intentional-failure exit codes were already checked above. Clear only the test
# harness status after every assertion passed; GitHub's pwsh wrapper exits LASTEXITCODE.
$global:LASTEXITCODE = 0
