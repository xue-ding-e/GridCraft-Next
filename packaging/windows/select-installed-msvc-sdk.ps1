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
  # This x64 package needs a narrowly selected release compiler/SDK, not a general
  # developer shell. Bind only verified installation directories in this process.
  # This avoids relying on a batch file to export state across shell boundaries.
  # The cheap real /MT C + link probe verifies these actual include/library inputs.
  $vcBin = Join-Path $vc.FullName 'bin\Hostx64\x64'
  $sdkBin = Join-Path $kitsRoot "bin\$($sdk.Name)\x64"
  $libDirs = @(
    (Join-Path $vc.FullName 'lib\x64'),
    (Join-Path $kitsRoot "Lib\$($sdk.Name)\ucrt\x64"),
    (Join-Path $kitsRoot "Lib\$($sdk.Name)\um\x64")
  )
  $includeDirs = @(
    (Join-Path $vc.FullName 'include'),
    (Join-Path $kitsRoot "Include\$($sdk.Name)\ucrt"),
    (Join-Path $kitsRoot "Include\$($sdk.Name)\shared"),
    (Join-Path $kitsRoot "Include\$($sdk.Name)\um")
  )
  foreach ($directory in @($vcBin, $sdkBin) + $libDirs + $includeDirs) {
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { throw 'SELECTED_X64_BUILD_DIRECTORY_MISSING' }
  }
  foreach ($header in @((Join-Path $includeDirs[0] 'vcruntime.h'), (Join-Path $includeDirs[1] 'stdio.h'), (Join-Path $includeDirs[3] 'Windows.h'))) {
    if (-not (Test-Path -LiteralPath $header -PathType Leaf)) { throw 'SELECTED_X64_BUILD_HEADER_MISSING' }
  }
  $selected = [ordered]@{
    PATH = (@($vcBin, $sdkBin, $env:PATH) -join ';')
    LIB = ($libDirs -join ';')
    INCLUDE = ($includeDirs -join ';')
    VSINSTALLDIR = ($vsPath.TrimEnd('\') + '\')
    VCINSTALLDIR = ((Join-Path $vsPath 'VC').TrimEnd('\') + '\')
    VisualStudioVersion = '17.0'
    VCToolsInstallDir = ($vc.FullName.TrimEnd('\') + '\')
    VCToolsVersion = $vc.Name
    WindowsSdkDir = ($kitsRoot.TrimEnd('\') + '\')
    WindowsSDKVersion = ($sdk.Name + '\')
    UniversalCRTSdkDir = ($kitsRoot.TrimEnd('\') + '\')
    UCRTVersion = $sdk.Name
    VSCMD_ARG_HOST_ARCH = 'x64'
    VSCMD_ARG_TGT_ARCH = 'x64'
  }
  $allowed = @($selected.Keys)
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
