<#
Discover an already-installed VS 2022 x64 MSVC + Windows SDK pair. No installs/builds.
With -ApplyToProcess, import only named developer-environment variables into this
PowerShell process. No `set` environment dump, machine/user changes, credentials,
activation queries, agreement acceptance, signing, or network access.
All actual paths and developer environment values stay under private/ on the runner.
Only public/selection-summary.json is eligible for artifact publication.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string] $OutputDirectory,
  [ValidatePattern('^$|^[0-9]+(?:\.[0-9]+){1,4}$')][string] $VisualStudioVersion = '',
  [ValidatePattern('^$|^[0-9]+(?:\.[0-9]+){1,4}$')][string] $VCToolsVersion = '',
  [ValidatePattern('^$|^[0-9]+(?:\.[0-9]+){1,4}$')][string] $WindowsSdkVersion = '',
  [switch] $ApplyToProcess
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'TOOLCHAIN_WINDOWS_REQUIRED' }
if (-not $env:RUNNER_TEMP) { throw 'TOOLCHAIN_RUNNER_TEMP_REQUIRED' }
$base = [IO.Path]::GetFullPath($OutputDirectory)
$temp = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\') + '\'
if (-not $base.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase)) { throw 'TOOLCHAIN_OUTPUT_MUST_BE_RUNNER_LOCAL' }
if ($base -match '["%&!^<>\r\n]') { throw 'UNEXPECTED_OUTPUT_PATH_CHARACTERS' }
if (Test-Path -LiteralPath $base) { throw 'TOOLCHAIN_OUTPUT_MUST_BE_NEW' }
$private = Join-Path $base 'private'; $public = Join-Path $base 'public'
New-Item -ItemType Directory -Path $private,$public | Out-Null
function Write-PrivateJson([string] $Name, $Value) {
  $Value | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $private $Name) -Encoding utf8NoBOM
}
try {
  $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
  if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) { throw 'VSWHERE_NOT_INSTALLED' }
  $raw = & $vswhere -all -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -version '[17.0,18.0)' -format json -utf8 2> (Join-Path $private 'vswhere-errors.txt')
  if ($LASTEXITCODE -ne 0) { throw 'VSWHERE_FAILED' }
  $installs = @($raw | ConvertFrom-Json)
  Write-PrivateJson 'vswhere.json' $installs
  $choices = @($installs | Where-Object { $_.isComplete -and -not $_.isPrerelease -and $_.installationVersion -match '^[0-9]+(?:\.[0-9]+){1,4}$' })
  if ($VisualStudioVersion) { $choices = @($choices | Where-Object { $_.installationVersion -eq $VisualStudioVersion }) }
  $vs = $choices | Sort-Object { [version]$_.installationVersion } -Descending | Select-Object -First 1
  if (-not $vs) { throw 'REQUESTED_VS2022_NOT_INSTALLED' }
  $vsPath = [IO.Path]::GetFullPath($vs.installationPath)
  $vcRoot = Join-Path $vsPath 'VC\Tools\MSVC'
  $versions = @(Get-ChildItem -LiteralPath $vcRoot -Directory | Where-Object {
    $_.Name -match '^[0-9]+(?:\.[0-9]+){1,4}$' -and
    (Test-Path -LiteralPath (Join-Path $_.FullName 'bin\Hostx64\x64\link.exe')) -and
    (Test-Path -LiteralPath (Join-Path $_.FullName 'lib\x64\libcmt.lib')) -and
    (Test-Path -LiteralPath (Join-Path $_.FullName 'lib\x64\libvcruntime.lib'))
  })
  if ($VCToolsVersion) { $versions = @($versions | Where-Object Name -eq $VCToolsVersion) }
  $vc = $versions | Sort-Object { [version]$_.Name } -Descending | Select-Object -First 1
  if (-not $vc) { throw 'REQUESTED_MSVC_X64_NOT_INSTALLED' }
  $kitsRoot = (Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows Kits\Installed Roots' -Name KitsRoot10).KitsRoot10
  if (-not $kitsRoot) { throw 'WINDOWS_KITS_ROOT_NOT_FOUND' }
  $sdkVersions = @(Get-ChildItem -LiteralPath (Join-Path $kitsRoot 'Lib') -Directory | Where-Object {
    $_.Name -match '^[0-9]+(?:\.[0-9]+){1,4}$' -and
    (Test-Path -LiteralPath (Join-Path $_.FullName 'ucrt\x64\libucrt.lib')) -and
    (Test-Path -LiteralPath (Join-Path $_.FullName 'um\x64\kernel32.lib')) -and
    (Test-Path -LiteralPath (Join-Path $kitsRoot "bin\$($_.Name)\x64\rc.exe"))
  })
  if ($WindowsSdkVersion) { $sdkVersions = @($sdkVersions | Where-Object Name -eq $WindowsSdkVersion) }
  $sdk = $sdkVersions | Sort-Object { [version]$_.Name } -Descending | Select-Object -First 1
  if (-not $sdk) { throw 'REQUESTED_WINDOWS_SDK_X64_NOT_INSTALLED' }
  $vcvars = Join-Path $vsPath 'VC\Auxiliary\Build\vcvarsall.bat'
  if (-not (Test-Path -LiteralPath $vcvars -PathType Leaf)) { throw 'VCVARSALL_NOT_INSTALLED' }
  # Reject cmd metacharacters rather than interpolating an unexpected install path.
  if ($vcvars -match '["%&!^<>\r\n]') { throw 'UNEXPECTED_INSTALLATION_PATH_CHARACTERS' }
  $allowed = @('PATH','LIB','LIBPATH','INCLUDE','VSINSTALLDIR','VCINSTALLDIR','VisualStudioVersion','VCToolsInstallDir','VCToolsVersion','VCToolsRedistDir','WindowsSdkDir','WindowsSDKVersion','WindowsLibPath','UniversalCRTSdkDir','UCRTVersion','VSCMD_ARG_HOST_ARCH','VSCMD_ARG_TGT_ARCH')
  $envFile = Join-Path $private 'developer-env.cmd'
  $cmdLines = @('@echo off', ('call "{0}" amd64 {1} -vcvars_ver={2} >nul 2>"{3}"' -f $vcvars,$sdk.Name,$vc.Name,(Join-Path $private 'vcvars-errors.txt')), 'if errorlevel 1 exit /b %errorlevel%', 'setlocal EnableDelayedExpansion')
  # Delayed expansion occurs after parsing, so PATH/LIB values cannot become cmd operators.
  # Only named values are emitted; no full environment dump.
  foreach ($name in $allowed) { $cmdLines += ('echo {0}=!{0}!' -f $name) }
  $cmdLines += 'exit /b 0'
  [IO.File]::WriteAllLines($envFile, $cmdLines, [Text.Encoding]::Default)
  $lines = @(& $env:ComSpec /d /c $envFile 2>> (Join-Path $private 'vcvars-errors.txt'))
  if ($LASTEXITCODE -ne 0) { throw 'VCVARS_SELECTION_FAILED' }
  $selected = [ordered]@{}
  foreach ($line in $lines) {
    if ($line -match '^([^=]+)=(.*)$' -and $Matches[1] -in $allowed) { $selected[$Matches[1]] = $Matches[2] }
  }
  foreach ($name in 'PATH','LIB','INCLUDE','VCToolsInstallDir','VCToolsVersion','WindowsSdkDir','WindowsSDKVersion') {
    if (-not $selected[$name]) { throw 'VCVARS_REQUIRED_VALUE_MISSING' }
  }
  if ($selected['VCToolsVersion'].TrimEnd('\') -ne $vc.Name -or $selected['WindowsSDKVersion'].TrimEnd('\') -ne $sdk.Name) { throw 'VCVARS_SELECTED_VERSION_MISMATCH' }
  if ([IO.Path]::GetFullPath($selected['VCToolsInstallDir']).TrimEnd('\') -ne $vc.FullName.TrimEnd('\')) { throw 'VCVARS_SELECTED_VC_PATH_MISMATCH' }
  $linker = Join-Path $vc.FullName 'bin\Hostx64\x64\link.exe'
  if ($ApplyToProcess) {
    foreach ($name in $allowed) { if ($selected.Contains($name)) { [Environment]::SetEnvironmentVariable($name, $selected[$name], 'Process') } }
    [Environment]::SetEnvironmentVariable('CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER',$linker,'Process')
  }
  Write-PrivateJson 'selection-details.json' ([ordered]@{ visual_studio=$vs; vc_path=$vc.FullName; sdk_root=$kitsRoot; sdk_version=$sdk.Name; linker=$linker; selected_process_environment=$selected; applied_to_process=[bool]$ApplyToProcess })
  $summary = [ordered]@{
    schema=1; evidence_only=$true; distribution_approved=$false; target='x86_64-pc-windows-msvc';
    visual_studio_version=$vs.installationVersion; visual_studio_product=$vs.productId;
    msvc_toolset_version=$vc.Name; windows_sdk_version=$sdk.Name;
    selected_files=@([ordered]@{name='link.exe';sha256=(Get-FileHash -LiteralPath $linker -Algorithm SHA256).Hash.ToLowerInvariant()});
    applied_to_process=[bool]$ApplyToProcess; reviewed_versions_requested=[bool]($VisualStudioVersion -and $VCToolsVersion -and $WindowsSdkVersion);
    unresolved=@('LEGAL_TERMS_NOT_REVIEWED','FINAL_LINK_INPUTS_NOT_YET_PROVEN')
  }
  $summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $public 'selection-summary.json') -Encoding utf8NoBOM
  # Do not emit paths, developer environment, or the raw receipt into workflow logs.
  Write-Output 'Installed MSVC/SDK selection recorded; public summary is separate from runner-local details.'
} catch {
  $_ | Out-String | Set-Content -LiteralPath (Join-Path $private 'error.txt') -Encoding utf8NoBOM
  $code=if($_.Exception.Message -match '^[A-Z][A-Z0-9_]+$'){$_.Exception.Message}else{'UNEXPECTED_TOOL_ERROR'}
  [ordered]@{schema=1;evidence_only=$true;distribution_approved=$false;unresolved=@('TOOLCHAIN_SELECTION_FAILED',$code)} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $public 'selection-failure.json') -Encoding utf8NoBOM
  throw "TOOLCHAIN_SELECTION_FAILED: $code. Details remain runner-local."
}
