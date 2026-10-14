# Modified for GridCraft-Next: independent packaging identity and notices.
<#
.SYNOPSIS
  Build, sign and package GridCraft-Next for Windows.

.DESCRIPTION
  Produces, in $env:DIST (default: dist/release):
    gridcraft-next-<version>-windows-<arch>.msi            per-machine installer (WiX v5)
    gridcraft-next-<version>-windows-<arch>-portable.zip   gridcraft-next.exe + gridcraft-next-cli.exe

  The binaries link the C runtime statically (+crt-static), so neither the MSI nor the portable
  zip needs the Visual C++ redistributable. Signing is delegated to sign.ps1 (skipped with a
  warning when no signing secrets are set).

  Needs: Rust (MSVC toolchain + the target), the Windows SDK (rc.exe, signtool.exe),
  and WiX v5: dotnet tool install --global wix --version 5.0.2

.EXAMPLE
  pwsh packaging/windows/package.ps1 -Arch x64
  # Non-x64 packaging is diagnostic-only and has no public release approval:
  pwsh packaging/windows/package.ps1 -Arch x86 -ValidationOnly
  pwsh packaging/windows/package.ps1 -Arch arm64 -ValidationOnly # requires ARM64 build tools
#>
param(
  [ValidateSet('x64', 'x86', 'arm64')] [string] $Arch = 'x64',
  [switch] $SkipBuild,
  # Disposable CI only: exercise the real application/installer before legal review.
  # These packages must stay inside the runner and are never downloadable artifacts.
  [switch] $ValidationOnly,
  # Hosted x64 CI: promote only exact reviewed inputs, otherwise retain an
  # explicitly internal package for native diagnostics and a red eligibility gate.
  [switch] $ReviewedOrValidation
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if ($SkipBuild) {
  throw '-SkipBuild is disabled: stale build records cannot prove the source/target identity of replacement binaries. Run a fresh locked build.'
}
if ($Arch -ne 'x64' -and -not $ValidationOnly) {
  throw 'Only Windows x64 has a reviewed release path. Other architectures require -ValidationOnly and stay internal.'
}
if ($ReviewedOrValidation -and ($ValidationOnly -or $Arch -ne 'x64')) {
  throw '-ReviewedOrValidation is an exclusive x64 CI mode; other architectures are not release-validated.'
}

function Invoke-Native([string] $What, [scriptblock] $Block) {
  Write-Output "==> $What"
  & $Block
  if ($LASTEXITCODE -ne 0) { throw "$What failed with exit code $LASTEXITCODE" }
}

# The version lives in one place: [workspace.package] version in the root Cargo.toml.
# Validate before creating output or building. An override cannot relabel Cargo binaries.
. (Join-Path $PSScriptRoot 'version.ps1')
$PackageVersion = Get-GridCraftPackageVersion -ManifestPath (Join-Path $Root 'Cargo.toml') -VersionOverride $env:GRIDCRAFT_VERSION
$Version = $PackageVersion.FullVersion
$MsiVersion = $PackageVersion.MsiVersion

$Target = switch ($Arch) { 'x64' { 'x86_64-pc-windows-msvc' } 'x86' { 'i686-pc-windows-msvc' } 'arm64' { 'aarch64-pc-windows-msvc' } }
$Dist = if ($env:DIST) { $env:DIST } else { Join-Path $Root 'dist\release' }
$TargetDir = if ($env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR } else { Join-Path $Root 'target' }
New-Item -ItemType Directory -Force -Path $Dist | Out-Null
$Evidence = Join-Path $TargetDir "windows-build-evidence\$Arch"
New-Item -ItemType Directory -Force -Path $Evidence | Out-Null
$BuildMessages = Join-Path $Evidence 'cargo-build.jsonl'
$Metadata = Join-Path $Evidence 'cargo-metadata.json'

if (-not $env:GRIDCRAFT_BUILD_SHA) { $env:GRIDCRAFT_BUILD_SHA = (git -C $Root rev-parse HEAD 2>$null) }
if (-not $env:GRIDCRAFT_BUILD_DATE) { $env:GRIDCRAFT_BUILD_DATE = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd') }

Write-Output "GridCraft-Next $Version for Windows $Arch ($Target); MSI ProductVersion $MsiVersion"

if (-not $SkipBuild) {
  # Static CRT: no VC++ redistributable needed. Scoped to the target so host build scripts and
  # proc-macros are unaffected.
  $flagVar = 'CARGO_TARGET_' + ($Target.ToUpper() -replace '-', '_') + '_RUSTFLAGS'
  [Environment]::SetEnvironmentVariable($flagVar, '-C target-feature=+crt-static')
  # Fail the build (rather than warn) if the icon/VERSIONINFO can't be embedded.
  $env:GRIDCRAFT_REQUIRE_WINRES = '1'
  Invoke-Native "cargo build ($Target)" {
    cargo build --release --locked -p gridcraft -p gridcraft-cli --target $Target --message-format=json |
      Tee-Object -FilePath $BuildMessages | Out-Null
  }
  Invoke-Native "cargo metadata ($Target)" {
    cargo metadata --locked --format-version 1 --filter-platform $Target |
      Out-File -FilePath $Metadata -Encoding utf8
  }
}

$Bin = Join-Path $TargetDir "$Target\release"
$ToolchainCapture = $null
if ($Arch -eq 'x64') {
  # Capture the exact just-built links/toolchain before any legal manifest or
  # package is written. The outer finally path reuses the source-bound receipt.
  $ToolchainCapture = & (Join-Path $PSScriptRoot 'collect-build-toolchain-evidence.ps1') `
    -BinaryDirectory $Bin -SourceSha $env:GRIDCRAFT_BUILD_SHA
}

# Check both binaries' PE headers before packaging. Machine (COFF header) must match -Arch, so an
# x64 build can never ship labelled arm64. Subsystem (optional header): 2 = Windows GUI, 3 = console.
# The app must be GUI (no console window opens with it); the CLI must stay console so its output
# reaches the terminal.
function Get-PeHeader([string] $Path) {
  $bytes = [System.IO.File]::ReadAllBytes($Path)
  $pe = [BitConverter]::ToInt32($bytes, 0x3C)
  return @{ Machine = [BitConverter]::ToUInt16($bytes, $pe + 4); Subsystem = [BitConverter]::ToUInt16($bytes, $pe + 0x5C) }
}
$Machine = switch ($Arch) { 'x64' { 0x8664 } 'x86' { 0x14C } 'arm64' { 0xAA64 } }
foreach ($check in @(@('gridcraft.exe', 2), @('gridcraft-cli.exe', 3))) {
  $h = Get-PeHeader (Join-Path $Bin $check[0])
  if ($h.Machine -ne $Machine) { throw "$($check[0]) is for machine 0x$('{0:X}' -f $h.Machine), expected 0x$('{0:X}' -f $Machine) ($Arch)" }
  if ($h.Subsystem -ne $check[1]) { throw "$($check[0]) has PE subsystem $($h.Subsystem), expected $($check[1])" }
  Write-Output "ok $($check[0]): $Arch, PE subsystem $($h.Subsystem)"
}
$Stage = Join-Path $TargetDir "windows-package\$Arch"
Remove-Item -Recurse -Force $Stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
Copy-Item (Join-Path $Bin 'gridcraft.exe') (Join-Path $Stage 'gridcraft-next.exe')
Copy-Item (Join-Path $Bin 'gridcraft-cli.exe') (Join-Path $Stage 'gridcraft-next-cli.exe')
. (Join-Path $PSScriptRoot 'legal-files.ps1')
$LegalDir = Join-Path $Stage 'licenses'
Copy-PackageLegalFiles $Root $LegalDir
# Collect the exact observed Cargo build inputs, not unrelated workspace/dev crates.
# The conservative set also preserves host build-input notices without claiming they
# all become linked code. All third-party legal files are flat, collision-proof names.
$ThirdParty = Join-Path $Evidence 'third-party'
if (Test-Path -LiteralPath $ThirdParty) { Remove-Item -LiteralPath $ThirdParty -Recurse -Force }
$ReviewPath = Join-Path $PSScriptRoot 'license-review.json'
$Review = $null
if (Test-Path -LiteralPath $ReviewPath) {
  try { $Review = Get-Content -LiteralPath $ReviewPath -Raw | ConvertFrom-Json }
  catch { Write-Warning 'Cargo review is unreadable; this build cannot be distribution-approved.' }
}
$CollectArgs = @(
  (Join-Path $PSScriptRoot 'collect-licenses.py'), '--metadata', $Metadata,
  '--build-messages', $BuildMessages, '--lockfile', (Join-Path $Root 'Cargo.lock'),
  '--target', $Target,
  '--output', $ThirdParty, '--supplements', (Join-Path $PSScriptRoot 'license-supplements\supplements.json')
)
if ($Review -and $Review.reviewed_fingerprint) { $CollectArgs += @('--reviewed-fingerprint', $Review.reviewed_fingerprint) }
& python @CollectArgs
$LegalExit = $LASTEXITCODE
if ($LegalExit -ne 0 -and $LegalExit -ne 2) { throw "Legal collection failed with exit code $LegalExit" }
Copy-Item -LiteralPath (Join-Path $ThirdParty 'THIRD_PARTY_INVENTORY.json') -Destination $LegalDir
Copy-Item -LiteralPath (Join-Path $ThirdParty 'THIRD_PARTY_NOTICES.txt') -Destination $LegalDir
Get-ChildItem -LiteralPath $ThirdParty -File -Filter 'third-party-*' | Copy-Item -Destination $LegalDir
$ToolchainReviewApproved = $false
if ($Arch -eq 'x64' -and $LegalExit -eq 0 -and $null -ne $ToolchainCapture -and $ToolchainCapture.capture_complete -eq $true) {
  # Both review files, exact inventories, final binary/link hashes and selected
  # original legal bytes are checked by one shared producer/consumer contract.
  & python (Join-Path $PSScriptRoot 'stage_reviewed_toolchain.py') --root $Root `
    --inventory (Join-Path $ThirdParty 'THIRD_PARTY_INVENTORY.json') --legal-directory $LegalDir `
    --selection (Join-Path $env:RUNNER_TEMP 'gridcraft-toolchain-selection/public') `
    --links (Join-Path $env:RUNNER_TEMP 'gridcraft-link-capture/public') `
    --toolchain (Join-Path $env:RUNNER_TEMP 'gridcraft-toolchain-collection/public') `
    --gui (Join-Path $Bin 'gridcraft.exe') --cli (Join-Path $Bin 'gridcraft-cli.exe')
  $ToolchainReviewExit = $LASTEXITCODE
  if ($ToolchainReviewExit -ne 0 -and $ToolchainReviewExit -ne 2) { throw 'Toolchain review helper failed unexpectedly.' }
  $ToolchainReviewApproved = ($ToolchainReviewExit -eq 0)
}
$DistributionApproved = ($LegalExit -eq 0 -and $ToolchainReviewApproved)
$InternalValidation = ([bool] $ValidationOnly -or ([bool] $ReviewedOrValidation -and -not $DistributionApproved))
if (-not $DistributionApproved -and -not $InternalValidation) {
  throw 'Exact Cargo/toolchain reviews and downstream originals are required. Distributable packaging is blocked.'
}
if ($InternalValidation) {
  'INTERNAL VALIDATION ONLY. Do not distribute this package. Review and rebuild without -ValidationOnly before publishing.' |
    Set-Content -LiteralPath (Join-Path $LegalDir 'INTERNAL-VALIDATION-ONLY.txt') -Encoding utf8
}
$LegalStatus = @{
  source_sha = $env:GRIDCRAFT_BUILD_SHA
  full_version = $Version
  msi_version = $MsiVersion
  distribution_approved = ($DistributionApproved -and -not $InternalValidation)
  legal_review_approved = $DistributionApproved
  validation_only = [bool] $InternalValidation
  collector_exit = $LegalExit
  inventory_sha256 = (Get-FileHash -LiteralPath (Join-Path $ThirdParty 'THIRD_PARTY_INVENTORY.json') -Algorithm SHA256).Hash.ToLowerInvariant()
}
$LegalStatus | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Evidence 'legal-status.json') -Encoding utf8
$LegalManifest = Join-Path $Stage 'legal.wxs'
Write-PackageLegalManifest $LegalDir $LegalManifest

& (Join-Path $PSScriptRoot 'sign.ps1') (Join-Path $Stage 'gridcraft-next.exe') (Join-Path $Stage 'gridcraft-next-cli.exe')

# ---- MSI ---------------------------------------------------------------------------------------
$Msi = Join-Path $Dist "gridcraft-next-$Version-windows-$Arch.msi"
Invoke-Native 'wix build' {
  wix build (Join-Path $PSScriptRoot 'gridcraft.wxs') $LegalManifest -arch $Arch `
    -d "Version=$MsiVersion" -d "FullVersion=$Version" -d "BinDir=$Stage" -d "IconPath=$(Join-Path $Root 'assets\app-icon\gridcraft.ico')" `
    -o $Msi
}
# wix writes its debug symbols (.wixpdb) next to the MSI; keep them out of the release assets.
Remove-Item -Force -ErrorAction SilentlyContinue ([IO.Path]::ChangeExtension($Msi, '.wixpdb'))
& (Join-Path $PSScriptRoot 'sign.ps1') $Msi

# ---- portable zip ------------------------------------------------------------------------------
$Portable = Join-Path $TargetDir "windows-package\gridcraft-next-$Version-windows-$Arch-portable"
Remove-Item -Recurse -Force $Portable -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Portable | Out-Null
Copy-Item (Join-Path $Stage '*.exe') $Portable
# Both formats include identical notices and optional font licences.
Copy-Item (Join-Path $LegalDir '*') $Portable
$Zip = Join-Path $Dist "gridcraft-next-$Version-windows-$Arch-portable.zip"
Remove-Item -Force $Zip -ErrorAction SilentlyContinue
Compress-Archive -Path $Portable -DestinationPath $Zip

# Smoke-test the CLI when this machine can run it. An ARM64 build made on an x64 runner can't run
# here; .github/workflows/windows-arm64.yml installs and runs it on ARM64 instead.
$HostArch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
if ($Arch -ne 'arm64' -or $HostArch -eq 'arm64') {
  Invoke-Native 'gridcraft-next-cli --version' { & (Join-Path $Stage 'gridcraft-next-cli.exe') --version }
} else {
  Write-Output "skipping gridcraft-next-cli --version: an $Arch build doesn't run on this $HostArch machine"
}
Get-Item $Msi, $Zip | Format-Table Name, Length
