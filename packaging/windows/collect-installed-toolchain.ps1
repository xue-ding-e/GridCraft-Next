<#
Read-only inspection of an ALREADY INSTALLED, explicitly selected Windows toolchain.
Writes only beneath a new OutputDirectory in RUNNER_TEMP. Never installs, compiles,
signs, uploads, accepts agreements, reads credentials, or changes machine/user settings.
Run in the SAME process environment as the eventual build, after exact MSVC/SDK selection.
private/ contains all paths, whitelisted environment values, discovery and linker details.
ONLY public/ contains publishable version/hash summaries and original legal text.
This is evidence collection, not distribution approval. Not yet Windows-tested.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string] $OutputDirectory,
  [ValidatePattern('^(?:1\.99\.0|stable)-x86_64-pc-windows-msvc$')][string] $RustToolchain = '1.99.0-x86_64-pc-windows-msvc',
  [ValidatePattern('^[a-f0-9]{40}$')][string] $ExpectedRustCommit = 'b940084d7eb6a299eb4bfeb8e34901bc051e7ac4',
  [ValidateSet('x86_64-pc-windows-msvc')][string] $Target = 'x86_64-pc-windows-msvc',
  # Optional exact originals within the selected Microsoft installation; no whole-drive scan.
  [string[]] $MicrosoftLegalFiles = @(),
  # Exact .lib files identified by final GUI/CLI linker logs, not all installed libraries.
  [string[]] $LinkedRuntimeLibraries = @(),
  [string[]] $LinkEvidenceFiles = @()
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'TOOLCHAIN_WINDOWS_REQUIRED' }
if (-not $env:RUNNER_TEMP) { throw 'TOOLCHAIN_RUNNER_TEMP_REQUIRED' }
$Out = [IO.Path]::GetFullPath($OutputDirectory)
$TempRoot = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\') + '\'
if (-not $Out.StartsWith($TempRoot,[StringComparison]::OrdinalIgnoreCase)) { throw 'TOOLCHAIN_OUTPUT_MUST_BE_RUNNER_LOCAL' }
if (Test-Path -LiteralPath $Out) { throw 'TOOLCHAIN_OUTPUT_MUST_BE_NEW' }
$Private = Join-Path $Out 'private'; $Public = Join-Path $Out 'public'; $Legal = Join-Path $Public 'legal'
New-Item -ItemType Directory -Path $Private,$Legal | Out-Null
$Files = [System.Collections.Generic.List[object]]::new()
$Issues = [System.Collections.Generic.List[object]]::new()
$Seen = @{}
$Missing = [System.Collections.Generic.List[object]]::new()
function Add-Issue([string] $Code,[string] $Detail) { $Issues.Add([ordered]@{code=$Code;detail=$Detail}) }
function Get-NumericVersion([string] $Value) { if ($Value -match '^[0-9]+(?:\.[0-9]+){1,4}') { return $Matches[0] }; return $null }
function Test-InRoot([string] $Path,[string] $Root) {
  if (-not $Root) { return $false }
  $p=[IO.Path]::GetFullPath($Path); $r=[IO.Path]::GetFullPath($Root).TrimEnd('\')+'\'
  return $p.StartsWith($r,[StringComparison]::OrdinalIgnoreCase)
}
function Record-File([string] $Path,[string] $Role,[ValidateSet('none','private','public-legal')][string] $Copy='none') {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { Add-Issue 'REQUIRED_FILE_MISSING' "$Role : $Path"; $Missing.Add([ordered]@{role=$Role;name=[IO.Path]::GetFileName($Path)}); return }
  $f=Get-Item -LiteralPath $Path
  if ($f.Attributes -band [IO.FileAttributes]::ReparsePoint) { Add-Issue 'REPARSE_INPUT_REQUIRES_REVIEW' $f.FullName; return }
  $key=$Role+'|'+$f.FullName.ToLowerInvariant(); if ($Seen.ContainsKey($key)) { return }; $Seen[$key]=$true
  $hash=(Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
  $copyName=$null
  if ($Copy -ne 'none') {
    if ($Copy -eq 'public-legal' -and ($f.Extension -notin '.txt','.rtf','.html','.htm','.md','' -or $f.Length -gt 8MB -or $f.Name -notmatch '(?i)license|eula|redist|third.?party|notice|copyright|^MIT\.txt$|^Apache-2\.0\.txt$|^Unicode-3\.0\.txt$')) { Add-Issue 'LEGAL_FILE_TYPE_REQUIRES_REVIEW' $f.FullName; return }
    $copyName=$hash.Substring(0,16)+'-'+$f.Name
    # Preserve source bytes exactly; never newline-normalize originals.
    $dest=if($Copy -eq 'public-legal'){$Legal}else{$Private}
    Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $dest $copyName)
  }
  $Files.Add([ordered]@{role=$Role;original_path=$f.FullName;name=$f.Name;bytes=$f.Length;sha256=$hash;file_version=(Get-NumericVersion $f.VersionInfo.FileVersion);copy_scope=$Copy;copy_name=$copyName})
}
function Discover-Legal([string] $Root,[int] $Depth=0,[string] $Role='microsoft-original-legal-candidate',[switch] $GrantOnly) {
  if (-not $Root -or -not(Test-Path -LiteralPath $Root -PathType Container)) { return }
  $pattern=if($GrantOnly){'(?i)license|eula|redist\.txt'}else{'(?i)license|eula|redist\.txt|third.?party|notice|copyright'}
  Get-ChildItem -LiteralPath $Root -File -Depth $Depth -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match $pattern -and $_.Extension -in '.txt','.rtf','.html','.htm','.md','' } |
    ForEach-Object { Record-File $_.FullName $Role 'public-legal' }
}
try {
  $version=(& rustup run $RustToolchain rustc -Vv 2> (Join-Path $Private 'rustc-errors.txt') | Out-String).Trim()
  if ($LASTEXITCODE -ne 0) { throw 'PINNED_RUST_TOOLCHAIN_NOT_INSTALLED' }
  if ($version -notmatch [regex]::Escape("commit-hash: $ExpectedRustCommit") -or $version -notmatch '(?m)^release: 1\.99\.0\r?$') { throw 'UNEXPECTED_RUST_IDENTITY' }
  $sysroot=(& rustup run $RustToolchain rustc --print sysroot 2>> (Join-Path $Private 'rustc-errors.txt') | Out-String).Trim()
  if ($LASTEXITCODE -ne 0) { throw 'RUST_SYSROOT_UNAVAILABLE' }
  $targetLib=(& rustup run $RustToolchain rustc --print target-libdir --target $Target 2>> (Join-Path $Private 'rustc-errors.txt') | Out-String).Trim()
  if ($LASTEXITCODE -ne 0) { throw 'RUST_TARGET_LIBDIR_UNAVAILABLE' }
  $cargoVersion=(& rustup run $RustToolchain cargo -Vv 2> (Join-Path $Private 'cargo-errors.txt') | Out-String).Trim()
  if ($LASTEXITCODE -ne 0) { throw 'CARGO_VERSION_UNAVAILABLE' }
  $doc=Join-Path $sysroot 'share\doc\rust'
  Record-File (Join-Path $doc 'COPYRIGHT-library.html') 'rust-std-original-notices' 'public-legal'
  # Only standard-library-related originals, not the full compiler/host copyright file.
  foreach($name in 'MIT.txt','Apache-2.0.txt','Unicode-3.0.txt') { Record-File (Join-Path $doc "licenses\$name") 'rust-std-original-license' 'public-legal' }
  # Installed COPYRIGHT-library does not cover the fixed compiler-builtins subtree.
  # Retain its original notices, tied to the exact rustc source commit and bytes.
  $supplementRoot = Join-Path $PSScriptRoot 'toolchain-license-supplements'
  $manifestPath = Join-Path $supplementRoot 'runtime-supplements.json'
  $supplements = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
  if ($supplements.schema -ne 1 -or $supplements.applicable_rust_release -cne '1.99.0' -or
      $supplements.applicable_rust_commit -cne $ExpectedRustCommit -or $supplements.target -cne $Target -or
      @($supplements.files).Count -ne 2) { throw 'RUST_RUNTIME_SUPPLEMENT_IDENTITY_MISMATCH' }
  $expectedNames = @('compiler-builtins-LICENSE.txt', 'libm-LICENSE.txt')
  $seenSupplements = @{}
  foreach ($item in $supplements.files) {
    if ($item.file -cnotin $expectedNames -or $seenSupplements.ContainsKey($item.file) -or
        $item.source_commit -cne $ExpectedRustCommit -or
        $item.source_repository -cne 'https://github.com/rust-lang/rust' -or
        -not $item.source_url.StartsWith("https://raw.githubusercontent.com/rust-lang/rust/$ExpectedRustCommit/", [StringComparison]::Ordinal) -or
        $item.original_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $item.collector_role -cne 'rust-runtime-fixed-commit-license') { throw 'RUST_RUNTIME_SUPPLEMENT_SCOPE_MISMATCH' }
    $seenSupplements[$item.file] = $true
    $original = Join-Path $supplementRoot $item.file
    $publicName = $item.original_sha256.Substring(0,16) + '-' + $item.file
    if (-not (Test-Path -LiteralPath $original -PathType Leaf) -or
        (Get-Item -LiteralPath $original).Length -ne $item.bytes -or
        (Get-FileHash -LiteralPath $original -Algorithm SHA256).Hash.ToLowerInvariant() -cne $item.original_sha256 -or
        $item.required_public_legal_basename -cne $publicName -or
        $supplements.required_public_legal.$publicName -cne $item.original_sha256) { throw 'RUST_RUNTIME_SUPPLEMENT_BYTES_MISMATCH' }
    Record-File $original 'rust-runtime-fixed-commit-license' 'public-legal'
  }
  # Bind source URLs/commit and the required map into the same reviewed fingerprint.
  Record-File $manifestPath 'rust-runtime-supplement-manifest'
  foreach($name in 'LICENSE-MIT','LICENSE-APACHE','COPYRIGHT') {
    $p=Join-Path $doc $name; if(Test-Path -LiteralPath $p -PathType Leaf){Record-File $p 'rust-original-license-optional' 'public-legal'}
  }
  foreach($name in 'rustc.exe','cargo.exe'){Record-File (Join-Path $sysroot "bin\$name") 'build-tool-identity-only'}
  # Available target std inputs. This inventory does not assert every member was linked.
  foreach($f in Get-ChildItem -LiteralPath $targetLib -File){
    if($f.Name -match '^lib(?:std|core|alloc|compiler_builtins|panic_unwind|panic_abort|unwind|std_detect)-' -and $f.Extension -in '.rlib','.lib'){Record-File $f.FullName 'rust-std-target-input'}
  }
  # Whitelist only; never dump all environment values. No keys or activation metadata.
  $names=@('VSINSTALLDIR','VisualStudioVersion','VCToolsInstallDir','VCToolsVersion','VCToolsRedistDir','WindowsSdkDir','WindowsSDKVersion','UniversalCRTSdkDir','UCRTVersion','LIB','INCLUDE','CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER','CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_RUSTFLAGS','RUSTFLAGS','CARGO_ENCODED_RUSTFLAGS','RUSTUP_TOOLCHAIN')
  $selected=[ordered]@{}; foreach($name in $names){$selected[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
  foreach($name in 'VCToolsInstallDir','VCToolsVersion','WindowsSdkDir','WindowsSDKVersion'){if(-not $selected[$name]){Add-Issue 'MSVC_SDK_SELECTION_INCOMPLETE' $name}}
  if($env:VCToolsInstallDir){
    foreach($name in 'link.exe','cl.exe','lib.exe','dumpbin.exe'){Record-File (Join-Path $env:VCToolsInstallDir "bin\Hostx64\x64\$name") 'msvc-build-tool-identity-only'}
    foreach($name in 'libcmt.lib','libvcruntime.lib','oldnames.lib'){Record-File (Join-Path $env:VCToolsInstallDir "lib\x64\$name") 'selected-msvc-runtime-candidate'}
    Record-File (Join-Path $env:VCToolsInstallDir 'include\vcruntime.h') 'msvc-original-copyright-source' 'private'
    Discover-Legal $env:VCToolsInstallDir 0 'microsoft-vc-runtime-legal-candidate'
    Discover-Legal (Join-Path $env:VCToolsInstallDir 'crt') 3 'microsoft-vc-runtime-legal-candidate'
  }
  $sdkver=if($env:WindowsSDKVersion){$env:WindowsSDKVersion.TrimEnd('\')}else{''}
  if($env:WindowsSdkDir -and $sdkver){
    Record-File (Join-Path $env:WindowsSdkDir "Lib\$sdkver\ucrt\x64\libucrt.lib") 'selected-ucrt-runtime-candidate'
    Record-File (Join-Path $env:WindowsSdkDir "bin\$sdkver\x64\rc.exe") 'sdk-build-tool-identity-only'
    Record-File (Join-Path $env:WindowsSdkDir "Include\$sdkver\ucrt\corecrt.h") 'ucrt-original-copyright-source' 'private'
    Discover-Legal (Join-Path $env:WindowsSdkDir "Licenses\$sdkver") 2 'microsoft-selected-sdk-legal-candidate'
    Discover-Legal (Join-Path $env:WindowsSdkDir 'Licenses') 0 'microsoft-sdk-root-legal-candidate'
    Discover-Legal $env:WindowsSdkDir 0 'microsoft-sdk-root-legal-candidate' -GrantOnly
    Discover-Legal (Join-Path $env:WindowsSdkDir "Redist\$sdkver") 1 'microsoft-selected-sdk-redist-candidate'
  }
  if($env:VSINSTALLDIR){
    Discover-Legal $env:VSINSTALLDIR 0 'microsoft-vs-license-candidate' -GrantOnly
    Discover-Legal (Join-Path $env:VSINSTALLDIR 'Licenses') 2 'microsoft-vs-license-candidate' -GrantOnly
    Discover-Legal (Join-Path $env:VSINSTALLDIR 'Common7\IDE') 0 'microsoft-vs-license-candidate' -GrantOnly
    Discover-Legal (Join-Path $env:VSINSTALLDIR 'Common7\IDE\Licenses') 2 'microsoft-vs-license-candidate' -GrantOnly
  }
  if($env:VCToolsRedistDir){Discover-Legal $env:VCToolsRedistDir 2 'microsoft-vc-redist-legal-candidate'}
  foreach($p in $MicrosoftLegalFiles){
    if((Test-InRoot $p $env:VSINSTALLDIR) -or (Test-InRoot $p $env:WindowsSdkDir)){Record-File $p 'microsoft-original-legal-explicit' 'public-legal'}else{Add-Issue 'MICROSOFT_LEGAL_PATH_OUTSIDE_SELECTED_INSTALLATION' $p}
  }
  $msLegal=@($Files | Where-Object {$_.role -like 'microsoft-*' -and $_.copy_scope -eq 'public-legal'})
  if($msLegal.Count -eq 0){Add-Issue 'MICROSOFT_ORIGINAL_LEGAL_FILES_NOT_FOUND' 'No original legal text found in selected installation scope'}
  Add-Issue 'MICROSOFT_TERMS_APPLICABILITY_NOT_REVIEWED' 'Candidate documents alone do not establish static-runtime redistribution rights or required downstream terms'
  foreach($p in $LinkedRuntimeLibraries){
    if([IO.Path]::GetExtension($p) -ne '.lib' -or -not((Test-InRoot $p $env:VCToolsInstallDir) -or (Test-InRoot $p $env:WindowsSdkDir))){Add-Issue 'RUNTIME_PATH_OUTSIDE_SELECTED_INSTALLATION' $p;continue}
    if([IO.Path]::GetFileName($p) -match '^(?i:libcmtd|libvcruntimed|libucrtd|msvcrtd|vcruntimed|ucrtd)\.lib$'){Add-Issue 'DEBUG_RUNTIME_NOT_DISTRIBUTABLE' $p}
    Record-File $p 'observed-linked-runtime-input'
  }
  if($LinkedRuntimeLibraries.Count -eq 0){Add-Issue 'FINAL_LINK_RUNTIME_INPUTS_UNPROVEN' 'No resolved runtime libraries supplied from final GUI/CLI links'}
  foreach($p in $LinkEvidenceFiles){Record-File $p 'final-link-evidence' 'private'}
  if($LinkEvidenceFiles.Count -eq 0){Add-Issue 'FINAL_LINK_EVIDENCE_ABSENT' 'No final GUI/CLI link records supplied'}
  if($RustToolchain.StartsWith('stable-')){Add-Issue 'FLOATING_ALIAS_DIAGNOSTIC_ONLY' 'This invocation only inspects preinstalled bytes; final build must use the explicit release toolchain'}
  $vswhere=Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
  $vs=@(); if(Test-Path -LiteralPath $vswhere){$vs=@(& $vswhere -all -products '*' -format json -utf8 2> (Join-Path $Private 'vswhere-errors.txt') | ConvertFrom-Json)}
  $privateReport=[ordered]@{schema=2;evidence_only=$true;distribution_approved=$false;rustc_verbose=$version;cargo_verbose=$cargoVersion;sysroot=$sysroot;target_libdir=$targetLib;selected_process_environment=$selected;visual_studio_installations=$vs;files=@($Files.ToArray());unresolved=@($Issues.ToArray())}
  $privateReport | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $Private 'toolchain-details.json') -Encoding utf8NoBOM
  # Explicit allowlist projection: no paths, environment dictionary, arguments, or raw linker text.
  $publicFiles=@($Files | ForEach-Object {[ordered]@{role=$_.role;name=$_.name;bytes=$_.bytes;sha256=$_.sha256;file_version=$_.file_version;legal_file=if($_.copy_scope -eq 'public-legal'){$_.copy_name}else{$null}}})
  $cargoRelease=if($cargoVersion -match '^cargo ([0-9.]+)'){$Matches[1]}else{$null}
  $llvmVersion=if($version -match '(?m)^LLVM version: ([0-9.]+)\r?$'){$Matches[1]}else{$null}
  $summary=[ordered]@{schema=2;evidence_only=$true;distribution_approved=$false;target=$Target;rust_release='1.99.0';rust_commit=$ExpectedRustCommit;cargo_release=$cargoRelease;llvm_version=$llvmVersion;msvc_toolset_version=(Get-NumericVersion $env:VCToolsVersion);windows_sdk_version=(Get-NumericVersion $sdkver);ucrt_version=(Get-NumericVersion $env:UCRTVersion);files=$publicFiles;missing_files=@($Missing.ToArray());unresolved=@($Issues | ForEach-Object {$_.code} | Sort-Object -Unique)}
  $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $Public 'toolchain-summary.json') -Encoding utf8NoBOM
  Write-Output 'Toolchain evidence collected. Publish only public/; runner-local details remain private. Distribution approval is false.'
}catch{
  $_ | Out-String | Set-Content -LiteralPath (Join-Path $Private 'error.txt') -Encoding utf8NoBOM
  $code=if($_.Exception.Message -match '^[A-Z][A-Z0-9_]+$'){$_.Exception.Message}else{'UNEXPECTED_TOOL_ERROR'}
  [ordered]@{schema=2;evidence_only=$true;distribution_approved=$false;unresolved=@('TOOLCHAIN_COLLECTION_FAILED',$code)} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Public 'toolchain-failure.json') -Encoding utf8NoBOM
  throw "TOOLCHAIN_COLLECTION_FAILED: $code. Details remain runner-local."
}
