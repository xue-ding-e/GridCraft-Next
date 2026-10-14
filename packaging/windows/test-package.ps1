#requires -Version 7.2
<#
.SYNOPSIS
  Real x64 Rust package, MSI lifecycle, and upstream coexistence gate on disposable CI.
.DESCRIPTION
  Requires the version-policy helpers, built MSI/portable ZIP, the exact staging directory,
  and unchanged official v0.6.1 MSI from fetch-upstream-test-msi.ps1. All GUI/CLI tests use
  actual Rust executables. Installer-only version variants reuse those same binary bytes
  and are deliberately NOT publishable matching-SemVer builds. They stay under RUNNER_TEMP.
  A successful report establishes test coverage, not signing or dependency-license approval.
  No user computer, account, service, security setting, credentials, or preference files are
  changed. Exact products installed by this invocation are removed in finally. Never run
  this through a self-hosted runner, and never upload the official MSI or version variants.
#>
param(
  [Parameter(Mandatory)] [string] $Msi,
  [Parameter(Mandatory)] [string] $PortableZip,
  [Parameter(Mandatory)] [string] $StageDirectory,
  [Parameter(Mandatory)] [string] $FullVersion,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F]{40}$')] [string] $SourceSha,
  [Parameter(Mandatory)] [string] $UpstreamMsi,
  [Parameter(Mandatory)] [string] $EvidenceDirectory,
  [string] $GuiSmokeScript = (Join-Path $PSScriptRoot 'native_gui_smoke.py')
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'package-test-common.ps1')
. (Join-Path $PSScriptRoot 'version.ps1')
Assert-HostedWindows
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
Assert-PackageCheck ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) 'The hosted runner must already be elevated; this test never requests elevation.'
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$EvidenceDirectory = Assert-RunnerTemporaryPath $EvidenceDirectory
Assert-PackageCheck (-not (Test-Path -LiteralPath $EvidenceDirectory)) 'EvidenceDirectory must not already exist.'
foreach ($file in $Msi, $PortableZip, $UpstreamMsi, $GuiSmokeScript) {
  Assert-PackageCheck (Test-Path -LiteralPath $file -PathType Leaf) "Missing input: $file"
}
$Msi = (Resolve-Path -LiteralPath $Msi).Path
$PortableZip = (Resolve-Path -LiteralPath $PortableZip).Path
$UpstreamMsi = (Resolve-Path -LiteralPath $UpstreamMsi).Path
$GuiSmokeScript = (Resolve-Path -LiteralPath $GuiSmokeScript).Path
$StageDirectory = (Resolve-Path -LiteralPath $StageDirectory).Path
$expectedVersion = Get-GridCraftPackageVersion -ManifestPath (Join-Path $Root 'Cargo.toml')
Assert-PackageCheck ($expectedVersion.FullVersion -ceq $FullVersion) 'Expected full version differs from Cargo.toml.'
$actualSha = (& git -C $Root rev-parse HEAD).Trim()
Assert-PackageCheck ($LASTEXITCODE -eq 0 -and $actualSha -ieq $SourceSha) 'Source SHA differs from the checked-out source.'
Assert-PackageCheck ($env:GITHUB_SHA -ieq $SourceSha) 'Only the exact current workflow commit may pass this gate.'
# A current release must not carry an old build identity or a renderer-forcing override.
foreach ($item in Get-ChildItem Env:) {
  Assert-PackageCheck ($item.Name -notmatch '^(WGPU|DXVK|MESA)_') "Remove inherited graphics override $($item.Name) from the workflow; no fallback-only success is accepted."
}
$NextUpgrade = '{73F9F3B2-548C-4F5B-8934-253B3C29E0B1}'
$UpstreamUpgrade = '{5511327B-F305-41BE-92D0-EB84944E7C4D}'
$NextDirectory = Join-Path $env:ProgramFiles 'GridCraft-Next'
$OriginalDirectory = Join-Path $env:ProgramFiles 'GridCraft'
$UninstallRoot = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall'
$CommonPrograms = [Environment]::GetFolderPath('CommonPrograms')
$CommonDesktop = [Environment]::GetFolderPath('CommonDesktopDirectory')
$NextStart = Join-Path $CommonPrograms 'GridCraft-Next.lnk'
$NextDesktop = Join-Path $CommonDesktop 'GridCraft-Next.lnk'
$OriginalStart = Join-Path $CommonPrograms 'GridCraft.lnk'
$OriginalDesktop = Join-Path $CommonDesktop 'GridCraft.lnk'
# Never use a preexisting product as a fixture or erase user data to make a test pass.
foreach ($upgrade in $NextUpgrade, $UpstreamUpgrade) {
  Assert-PackageCheck (@(Get-RelatedProductCodes $upgrade).Count -eq 0) "Preexisting installation/advertisement for $upgrade; refusing to continue."
}
foreach ($path in @($NextDirectory, $OriginalDirectory, $NextStart, $NextDesktop, $OriginalStart, $OriginalDesktop,
  'HKLM:\Software\GridCraft-Next', 'HKCU:\Software\GridCraft-Next', 'HKLM:\Software\GridCraft', 'HKCU:\Software\GridCraft',
  'HKLM:\Software\Classes\GridCraftNext.Workbook', 'HKLM:\Software\Classes\GridCraftNext.TextData',
  'HKLM:\Software\Classes\GridCraft.Workbook', 'HKLM:\Software\Classes\GridCraft.TextData',
  'HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\gridcraft-next.exe',
  'HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\gridcraft.exe',
  (Join-Path $env:APPDATA 'GridCraft-Next'), (Join-Path $env:LOCALAPPDATA 'GridCraft-Next'),
  (Join-Path $env:APPDATA 'GridCraft'), (Join-Path $env:LOCALAPPDATA 'GridCraft'))) {
  Assert-PackageCheck (-not (Test-Path -LiteralPath $path)) "Preexisting installation/settings at $path; refusing to continue."
}
New-Item -ItemType Directory -Path $EvidenceDirectory | Out-Null
$UnrelatedDirectory = Join-Path $EvidenceDirectory 'unrelated working directory'
New-Item -ItemType Directory -Path $UnrelatedDirectory | Out-Null
$report = [ordered]@{
  schemaVersion = 1; sourceSha = $SourceSha; fullVersion = $FullVersion
  msiVersion = $expectedVersion.MsiVersion; status = 'running'; startedUtc = [DateTime]::UtcNow.ToString('o')
  scope = 'GitHub-hosted Windows x64; real Rust GUI/CLI; unsigned MSI/portable. No end-user SmartScreen, non-admin UAC, Windows 10/11 hardware, or legal-completeness claim.'
  publicationApproved = $false
  runner = [ordered]@{ imageOS = $env:ImageOS; imageVersion = $env:ImageVersion; osVersion = [Environment]::OSVersion.VersionString; powershellVersion = $PSVersionTable.PSVersion.ToString() }
  officialFixture = [ordered]@{ release = 'https://github.com/storytold/gridcraft/releases/tag/v0.6.1'; sha256 = '984e9a85942ce55953ce2b875c4e93ddd70d47fb4bf5f07c74d1d9f9b98e56c9'; redistributed = $false }
  inputs = @(); pe = @(); operations = @(); installerOnlyVariants = @(); checks = @(); cleanupErrors = @()
}
$attemptedProducts = [Collections.Generic.List[string]]::new()
$failure = $null
$upstreamSnapshot = $null
$defaultSnapshot = @(Get-AssociationDefaultsState)
$finalPackage = $null
$originalPackage = $null

function Save-Report {
  $report | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath (Join-Path $EvidenceDirectory 'package-evidence.json') -Encoding utf8
}
function Complete-Check([string] $Name) {
  $report.checks += $Name
  Write-Host "PASS: $Name"
  Save-Report
}
function Invoke-Installer([string] $Name, [string[]] $Arguments, [string] $AttemptedCode = '', [string] $ExpectedRejectionText = '') {
  if ($AttemptedCode -and -not $attemptedProducts.Contains($AttemptedCode)) { $attemptedProducts.Add($AttemptedCode) }
  $log = Join-Path $EvidenceDirectory ($Name + '.msi.log')
  $allowed = if ($ExpectedRejectionText) { @(1603) } else { @(0) }
  # 3010 and 1641 are deliberately not clean passes. Never reboot or bypass a warning.
  $result = Invoke-CheckedProcess -File (Join-Path ([Environment]::SystemDirectory) 'msiexec.exe') `
    -Arguments ($Arguments + @('/qn', '/norestart', '/l*v', $log)) -WorkingDirectory $UnrelatedDirectory `
    -LogPrefix (Join-Path $EvidenceDirectory $Name) -TimeoutSeconds 180 -AllowedExitCodes $allowed
  $report.operations += [ordered]@{ name = $Name; exitCode = $result.ExitCode; log = [IO.Path]::GetFileName($log); expectedRejection = [bool] $ExpectedRejectionText }
  if ($ExpectedRejectionText) {
    $logText = Get-Content -LiteralPath $log -Raw
    Assert-PackageCheck ($logText.Contains($ExpectedRejectionText)) "Failure was not the expected version-policy rejection: $Name"
    $launchFailure = '(?s)Action start [^\r\n]*LaunchConditions.*?' + [regex]::Escape($ExpectedRejectionText) + '.*?Action ended [^\r\n]*LaunchConditions\. Return value 3\.'
    Assert-PackageCheck ($logText -match $launchFailure) "Expected rejection did not come from LaunchConditions: $Name"
  }
  Save-Report
}
function Get-ProductSnapshot([string] $Name, [string] $Code) {
  $next = $Name -ceq 'GridCraft-Next'
  $directory = if ($next) { $NextDirectory } else { $OriginalDirectory }
  $exeName = if ($next) { 'gridcraft-next.exe' } else { 'gridcraft.exe' }
  $progPrefix = if ($next) { 'GridCraftNext' } else { 'GridCraft' }
  $roots = @("HKLM:\Software\$Name", "HKCU:\Software\$Name", "HKLM:\Software\Classes\$progPrefix.Workbook",
    "HKLM:\Software\Classes\$progPrefix.TextData", "HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\$exeName", "$UninstallRoot\$Code")
  $registry = @()
  foreach ($path in $roots) { $registry += @(Get-RegistryTreeState $path) }
  $openWith = @()
  foreach ($extension in '.xlsx', '.xlsm', '.xlsb', '.ods', '.csv', '.tsv') {
    $prog = if ($extension -in @('.csv', '.tsv')) { "$progPrefix.TextData" } else { "$progPrefix.Workbook" }
    $openWith += [ordered]@{ extension = $extension; value = Get-RegistryValueState "HKLM:\Software\Classes\$extension\OpenWithProgids" $prog }
  }
  return [pscustomobject][ordered]@{
    registered = Test-ExactProductPresent $Code
    related = @(Get-RelatedProductCodes $(if ($next) { $NextUpgrade } else { $UpstreamUpgrade }) | Sort-Object)
    files = @(Get-DirectoryHashes $directory); registry = $registry; openWith = $openWith
    registeredApplication = Get-RegistryValueState 'HKLM:\Software\RegisteredApplications' $Name
    start = Get-ShortcutState (Join-Path $CommonPrograms "$Name.lnk")
    desktop = Get-ShortcutState (Join-Path $CommonDesktop "$Name.lnk")
  }
}
function Assert-Isolation {
  # GUI smoke deliberately uses GRIDCRAFT_NO_PREFS. This proves no preference side effects,
  # not normal preference migration behavior; never delete a folder to satisfy this assertion.
  foreach ($base in $env:APPDATA, $env:LOCALAPPDATA) {
    foreach ($name in 'GridCraft', 'GridCraft-Next') {
      Assert-PackageCheck (-not (Test-Path -LiteralPath (Join-Path $base $name))) "Unexpected preference/log directory created for $name"
    }
  }
  Assert-StateEqual $defaultSnapshot @(Get-AssociationDefaultsState) 'A file-association default/UserChoice changed.'
  if ($null -ne $upstreamSnapshot) {
    Assert-StateEqual $upstreamSnapshot (Get-ProductSnapshot 'GridCraft' $originalPackage.Properties.ProductCode) 'Next altered the installed official GridCraft files/registration/shortcuts.'
  }
}
function Assert-LegalParity([string] $Directory) {
  $actual = @(Get-DirectoryHashes $Directory)
  Assert-StateEqual $expectedLegal $actual 'Installed legal filename/hash set differs from the exact staged payload.'
}
function Assert-NextPackage($Package, [bool] $Desktop) {
  $code = $Package.Properties.ProductCode
  Assert-PackageCheck (Test-ExactProductPresent $code) "Product $code is not installed."
  $related = @(Get-RelatedProductCodes $NextUpgrade)
  Assert-PackageCheck ($related.Count -eq 1 -and $related[0] -ieq $code) 'Next must have exactly one installed related product.'
  $arp = Get-ItemProperty -LiteralPath "$UninstallRoot\$code"
  Assert-PackageCheck ($arp.DisplayName -ceq 'GridCraft-Next') 'Wrong Add/Remove Programs name.'
  Assert-PackageCheck ($arp.DisplayVersion -ceq $Package.Properties.ProductVersion) 'Wrong installed MSI version.'
  Assert-PackageCheck ($arp.Publisher -ceq 'xue-ding-e') 'Wrong Add/Remove Programs publisher.'
  Assert-PackageCheck ($arp.Comments -ceq "GridCraft-Next $($Package.Properties.GRIDCRAFTNEXT_FULL_VERSION)") 'Installed ARP comments lost the full package SemVer.'
  foreach ($name in 'gridcraft-next.exe', 'gridcraft-next-cli.exe') {
    $installed = Join-Path $NextDirectory $name
    Assert-PackageCheck (Test-Path -LiteralPath $installed -PathType Leaf) "Installed executable is missing: $name"
    Assert-PackageCheck ((Get-FileHash -LiteralPath $installed).Hash -ceq (Get-FileHash -LiteralPath (Join-Path $StageDirectory $name)).Hash) "Installed $name differs from the real staged Rust executable."
  }
  Assert-LegalParity (Join-Path $NextDirectory 'licenses')
  Assert-PackageCheck ((Get-RegistryValueState 'HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\gridcraft-next.exe' '').Value -ieq (Join-Path $NextDirectory 'gridcraft-next.exe')) 'Wrong Next App Paths registration.'
  foreach ($prog in 'GridCraftNext.Workbook', 'GridCraftNext.TextData') {
    $expectedCommand = '"' + (Join-Path $NextDirectory 'gridcraft-next.exe') + '" "%1"'
    Assert-PackageCheck ((Get-RegistryValueState "HKLM:\Software\Classes\$prog\shell\open\command" '').Value -ieq $expectedCommand) "Wrong Open command for $prog"
  }
  foreach ($extension in '.xlsx', '.xlsm', '.xlsb', '.ods', '.csv', '.tsv') {
    $prog = if ($extension -in @('.csv', '.tsv')) { 'GridCraftNext.TextData' } else { 'GridCraftNext.Workbook' }
    Assert-PackageCheck ((Get-RegistryValueState "HKLM:\Software\Classes\$extension\OpenWithProgids" $prog).Value -ceq $prog) "Missing Next Open With registration for $extension"
  }
  Assert-PackageCheck ((Get-RegistryValueState 'HKLM:\Software\RegisteredApplications' 'GridCraft-Next').Value -ceq 'Software\GridCraft-Next\Capabilities') 'Wrong Next RegisteredApplications value.'
  Assert-PackageCheck ((Get-RegistryValueState 'HKLM:\Software\GridCraft-Next\Capabilities' 'ApplicationName').Value -ceq 'GridCraft-Next') 'Wrong Next capabilities identity.'
  foreach ($extension in '.xlsx', '.xlsm', '.csv', '.tsv') {
    $prog = if ($extension -in @('.csv', '.tsv')) { 'GridCraftNext.TextData' } else { 'GridCraftNext.Workbook' }
    Assert-PackageCheck ((Get-RegistryValueState 'HKLM:\Software\GridCraft-Next\Capabilities\FileAssociations' $extension).Value -ceq $prog) "Wrong Next capability for $extension"
  }
  Assert-AdvertisedShortcut $NextStart $code (Join-Path $NextDirectory 'gridcraft-next.exe')
  Assert-PackageCheck ((Test-Path -LiteralPath $NextDesktop) -eq $Desktop) 'Wrong desktop shortcut choice.'
  $choice = Get-RegistryValueState 'HKLM:\Software\GridCraft-Next\Installer' 'DesktopShortcut'
  Assert-PackageCheck ($choice.Value -ceq $(if ($Desktop) { '1' } else { '0' })) 'Desktop choice was not persisted.'
  Assert-PackageCheck ((Get-RegistryValueState 'HKLM:\Software\GridCraft-Next\Installer' 'DesktopShortcutComponent').Exists -eq $Desktop) 'Desktop component marker differs from its shortcut.'
  Assert-PackageCheck (-not (Get-RegistryValueState 'HKCU:\Software\GridCraft-Next\Installer' 'DesktopShortcutComponent').Exists) 'Unexpected per-user desktop component marker.'
  if ($Desktop) { Assert-PlainShortcut $NextDesktop (Join-Path $NextDirectory 'gridcraft-next.exe') $NextDirectory (Join-Path $Root 'assets\app-icon\gridcraft.ico') }
  Assert-Isolation
}
function Assert-NextRemoved {
  Assert-PackageCheck (@(Get-RelatedProductCodes $NextUpgrade).Count -eq 0) 'Next remains installed/advertised after uninstall.'
  Assert-PackageCheck (@(Get-DirectoryHashes $NextDirectory).Count -eq 0) 'Next installed files/notices remain after uninstall.'
  foreach ($path in $NextStart, $NextDesktop, 'HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\gridcraft-next.exe',
    'HKLM:\Software\Classes\GridCraftNext.Workbook', 'HKLM:\Software\Classes\GridCraftNext.TextData') {
    Assert-PackageCheck (-not (Test-Path -LiteralPath $path)) "Next install-owned path remains after uninstall: $path"
  }
  foreach ($path in 'HKLM:\Software\GridCraft-Next', 'HKCU:\Software\GridCraft-Next') {
    $values = @(Get-RegistryTreeState $path | ForEach-Object { $_.Values })
    Assert-PackageCheck ($values.Count -eq 0) "Next registry values remain after uninstall: $path"
  }
  Assert-PackageCheck (-not (Get-RegistryValueState 'HKLM:\Software\RegisteredApplications' 'GridCraft-Next').Exists) 'Next RegisteredApplications entry remains.'
  foreach ($extension in '.xlsx', '.xlsm', '.xlsb', '.ods', '.csv', '.tsv') {
    foreach ($prog in 'GridCraftNext.Workbook', 'GridCraftNext.TextData') {
      Assert-PackageCheck (-not (Get-RegistryValueState "HKLM:\Software\Classes\$extension\OpenWithProgids" $prog).Exists) 'Next Open With value remains.'
    }
  }
  foreach ($code in $attemptedProducts) {
    if ($code -ine $originalPackage.Properties.ProductCode) {
      Assert-PackageCheck (-not (Test-Path -LiteralPath "$UninstallRoot\$code")) 'Next Add/Remove Programs entry remains.'
    }
  }
  Assert-Isolation
}
function Invoke-ActualGui([string] $Directory, [string] $Mode) {
  $python = (Get-Command python -CommandType Application -ErrorAction Stop).Source
  $output = Join-Path $EvidenceDirectory "gui-$Mode"
  Invoke-CheckedProcess -File $python -Arguments @($GuiSmokeScript, '--gui-exe', (Join-Path $Directory 'gridcraft-next.exe'),
    '--cli-exe', (Join-Path $Directory 'gridcraft-next-cli.exe'), '--output-dir', $output,
    '--expected-version', $FullVersion, '--source-sha', $SourceSha, '--mode', $Mode, '--timeout', '90') `
    -WorkingDirectory $UnrelatedDirectory -LogPrefix (Join-Path $EvidenceDirectory "gui-$Mode-driver") `
    -TimeoutSeconds 900 | Out-Null
  $guiEvidencePath = Join-Path $output 'evidence.json'
  Assert-PackageCheck (Test-Path -LiteralPath $guiEvidencePath) 'GUI smoke exited without its required evidence.'
  $guiEvidence = Get-Content -LiteralPath $guiEvidencePath -Raw | ConvertFrom-Json
  Assert-PackageCheck ($guiEvidence.status -ceq 'passed') 'GUI evidence does not report complete success.'
  Complete-Check "real Rust $Mode native GUI, UI interaction, screenshot, save/reopen, and CLI smoke"
}
function New-InstallerOnlyVariant([string] $Version, [string] $Label) {
  $path = Join-Path $VariantDirectory ("INSTALLER-ONLY-$Label.msi")
  $encoded = ConvertTo-GridCraftMsiVersion $Version
  $wix = (Get-Command wix -CommandType Application -ErrorAction Stop).Source
  Invoke-CheckedProcess -File $wix -Arguments @('build', (Join-Path $PSScriptRoot 'gridcraft.wxs'), $VariantLegalManifest,
    '-arch', 'x64', '-d', "Version=$encoded", '-d', "FullVersion=$Version", '-d', "BinDir=$StageDirectory",
    '-d', "IconPath=$(Join-Path $Root 'assets\app-icon\gridcraft.ico')", '-o', $path) `
    -WorkingDirectory $UnrelatedDirectory -LogPrefix (Join-Path $EvidenceDirectory "build-variant-$Label") -TimeoutSeconds 180 | Out-Null
  & (Join-Path $PSScriptRoot 'test-msi-version.ps1') -Msi $path -FullVersion $Version | Write-Host
  $package = Read-PackageDatabase $path
  Assert-NoExtraAgreementOrSystemActions $package
  Assert-PackageCheck ($package.Properties.ProductCode -ine $finalPackage.Properties.ProductCode) 'A fixture must have a distinct ProductCode.'
  foreach ($variant in $report.installerOnlyVariants) {
    Assert-PackageCheck ($variant.productCode -ine $package.Properties.ProductCode) 'Two fixture builds reused a ProductCode.'
  }
  $report.installerOnlyVariants += [ordered]@{
    label = $Label; path = $path; fullVersion = $Version; msiVersion = $encoded
    productCode = $package.Properties.ProductCode; sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    binaryVersion = $FullVersion; sourceSha = $SourceSha; publishable = $false
    purpose = 'Installer transition logic only. Exact real staged Rust binaries reused; their version deliberately differs from the synthetic installer version when applicable.'
  }
  return $package
}

Save-Report
try {
  foreach ($name in 'GridCraft', 'GridCraft-Next') {
    Assert-PackageCheck (-not (Get-RegistryValueState 'HKLM:\Software\RegisteredApplications' $name).Exists) "Preexisting registered application: $name"
  }
  foreach ($prog in 'GridCraft.Workbook', 'GridCraft.TextData', 'GridCraftNext.Workbook', 'GridCraftNext.TextData') {
    Assert-PackageCheck (-not (Test-Path -LiteralPath "HKCU:\Software\Classes\$prog")) "Preexisting user ProgID: $prog"
    foreach ($extension in '.xlsx', '.xlsm', '.xlsb', '.ods', '.csv', '.tsv') {
      foreach ($hive in 'HKLM', 'HKCU') {
        Assert-PackageCheck (-not (Get-RegistryValueState "${hive}:\Software\Classes\$extension\OpenWithProgids" $prog).Exists) "Preexisting $prog Open With entry."
      }
    }
  }
  Assert-PackageCheck ((Get-Item -LiteralPath $UpstreamMsi).Length -eq 13819904) 'Official MSI size differs from the verified v0.6.1 asset.'
  Assert-PackageCheck ((Get-FileHash -LiteralPath $UpstreamMsi -Algorithm SHA256).Hash.ToLowerInvariant() -ceq $report.officialFixture.sha256) 'Official MSI SHA256 mismatch; do not install.'
  foreach ($path in $Msi, $PortableZip) {
    $report.inputs += [ordered]@{ path = $path; bytes = (Get-Item -LiteralPath $path).Length; sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
  }
  Assert-PackageCheck ((Get-AuthenticodeSignature -LiteralPath $Msi).Status.ToString() -ceq 'NotSigned') 'This unsigned validation gate must not introduce signing.'
  & (Join-Path $PSScriptRoot 'test-msi-version.ps1') -Msi $Msi -FullVersion $FullVersion
  $finalPackage = Read-PackageDatabase $Msi
  $originalPackage = Read-PackageDatabase $UpstreamMsi
  Assert-NoExtraAgreementOrSystemActions $finalPackage
  Assert-NoExtraAgreementOrSystemActions $originalPackage
  Assert-PackageCheck ([guid] $originalPackage.Properties.UpgradeCode -eq [guid] $UpstreamUpgrade) 'Wrong official upgrade family.'
  Assert-PackageCheck ($originalPackage.Properties.ProductName -ceq 'GridCraft' -and $originalPackage.Properties.ProductVersion -ceq '0.6.1') 'Wrong official fixture product/version.'
  Assert-PackageCheck ($originalPackage.Properties.Manufacturer -ceq 'Learning Machines LLC') 'Wrong official fixture manufacturer.'
  Assert-PackageCheck ($originalPackage.Properties.ProductCode -ine $finalPackage.Properties.ProductCode) 'Upstream and Next ProductCodes collide.'
  foreach ($package in $finalPackage, $originalPackage) {
    Assert-PackageCheck (-not (Test-ExactProductPresent $package.Properties.ProductCode) -and
      -not (Test-Path -LiteralPath "$UninstallRoot\$($package.Properties.ProductCode)")) 'An input product is already registered.'
  }
  $finalPackage | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $EvidenceDirectory 'next-msi-tables.json') -Encoding utf8
  $originalPackage | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $EvidenceDirectory 'official-msi-tables.json') -Encoding utf8
  $expectedLegal = @(Get-DirectoryHashes (Join-Path $StageDirectory 'licenses'))
  Assert-PackageCheck ($expectedLegal.Count -ge 6) 'Staged legal payload is missing.'
  foreach ($pair in @(@('LICENSE-MIT', 'LICENSE-MIT'), @('LICENSE-APACHE', 'LICENSE-APACHE'), @('NOTICE', 'NOTICE'),
    @('ATTRIBUTION.md', 'ATTRIBUTION.md'), @('app-icon-LICENSE.txt', 'assets\app-icon\LICENSE.txt'), @('LICENSE-brand.txt', 'docs\brand\LICENSE-brand.txt'))) {
    $staged = Join-Path $StageDirectory ('licenses\' + $pair[0])
    $source = Join-Path $Root $pair[1]
    Assert-PackageCheck (Test-Path -LiteralPath $staged -PathType Leaf) "Required legal notice missing: $($pair[0])"
    Assert-PackageCheck ((Get-FileHash -LiteralPath $source).Hash -ceq (Get-FileHash -LiteralPath $staged).Hash) "Legal notice changed from source: $($pair[0])"
  }
  $report.legalPayload = $expectedLegal
  $expectedNames = @('gridcraft-next.exe', 'gridcraft-next-cli.exe') + @($expectedLegal | ForEach-Object { [IO.Path]::GetFileName($_.Name) })
  $msiNames = @($finalPackage.Tables.File | ForEach-Object { ($_.FileName -split '\|')[-1] })
  Assert-StateEqual @($expectedNames | Sort-Object) @($msiNames | Sort-Object) 'MSI file table differs from expected real binaries and legal files.'
  foreach ($component in $finalPackage.Tables.Component) {
    Assert-PackageCheck (([int] $component.Attributes -band 256) -ne 0) "MSI component $($component.Component) is not 64-bit."
    Assert-PackageCheck ($component.ComponentId -ine '{0507D4B6-12FF-4882-84E1-59083E74A4EB}') 'Next reused the upstream application component GUID.'
  }
  foreach ($row in $finalPackage.Tables.Registry) {
    Assert-PackageCheck ([int] $row.Root -in @(2, -1)) 'Next MSI unexpectedly writes a non-machine registry hive.'
    Assert-PackageCheck ($row.Key -match '^Software\\(GridCraft-Next(\\|$)|Classes\\GridCraftNext\.|Microsoft\\Windows\\CurrentVersion\\App Paths\\gridcraft-next\.exe$|RegisteredApplications$|Classes\\\.(xlsx|xlsm|xlsb|ods|csv|tsv)\\OpenWithProgids$)') "Unexpected registry scope: $($row.Key)"
    if ($row.Key -match '^Software\\Classes\\\.') {
      Assert-PackageCheck ($row.Name -in @('GridCraftNext.Workbook', 'GridCraftNext.TextData')) 'Next must only add its own Open With value.'
    }
    if ($row.Key -ceq 'Software\RegisteredApplications') {
      Assert-PackageCheck ($row.Name -ceq 'GridCraft-Next') 'Next must not replace another registered application.'
    }
  }
  Assert-PackageCheck ($finalPackage.Tables.Shortcut.Count -eq 2) 'Unexpected MSI shortcut count.'
  Complete-Check 'read-only MSI metadata, independent identity, payload scope, and no additional agreement/system action'

  $expanded = Join-Path $EvidenceDirectory 'portable extraction with spaces'
  Expand-Archive -LiteralPath $PortableZip -DestinationPath $expanded
  $guiMatches = @(Get-ChildItem -LiteralPath $expanded -Recurse -File -Force -Filter 'gridcraft-next.exe')
  Assert-PackageCheck ($guiMatches.Count -eq 1) 'Portable archive must contain exactly one GUI executable.'
  $PortableDirectory = $guiMatches[0].DirectoryName
  $topLevelEntries = @(Get-ChildItem -LiteralPath $expanded -Force)
  Assert-PackageCheck ($topLevelEntries.Count -eq 1 -and $topLevelEntries[0].PSIsContainer -and
    $topLevelEntries[0].FullName -ieq $PortableDirectory) 'Portable ZIP has files/directories outside its sole approved payload folder.'
  Assert-PackageCheck (@(Get-ChildItem -LiteralPath $PortableDirectory -Directory -Force).Count -eq 0) 'Portable payload must have the expected flat file layout.'
  $portableFiles = @(Get-DirectoryHashes $PortableDirectory)
  Assert-StateEqual @($expectedNames | Sort-Object) @($portableFiles.Name | Sort-Object) 'Portable archive contains missing/extra files or nested payloads.'
  $portableLegal = @($portableFiles | Where-Object { $_.Name -notin @('gridcraft-next.exe', 'gridcraft-next-cli.exe') })
  Assert-StateEqual $expectedLegal $portableLegal 'Portable legal filename/hash set differs from staging.'
  foreach ($entry in @(@('gridcraft-next.exe', 2), @('gridcraft-next-cli.exe', 3))) {
    $name = $entry[0]
    $path = Join-Path $PortableDirectory $name
    Assert-PackageCheck ((Get-FileHash -LiteralPath $path).Hash -ceq (Get-FileHash -LiteralPath (Join-Path $StageDirectory $name)).Hash) "Portable $name differs from the staged Rust build."
    $facts = Get-PeFacts $path
    Assert-PackageCheck ($facts.Machine -eq 0x8664 -and $facts.Subsystem -eq $entry[1]) "Wrong x64/subsystem identity for $name"
    Assert-PackageCheck ((Get-AuthenticodeSignature -LiteralPath $path).Status.ToString() -ceq 'NotSigned') "Unexpected signature state: $name"
    foreach ($dll in $facts.Imports) {
      Assert-PackageCheck ($dll -notmatch '^(?i)(vcruntime|msvcp|msvcr|ucrtbase|api-ms-win-crt)') "Unexpected dynamically linked Visual C++ runtime: $dll"
      Assert-PackageCheck ($dll -match '^(?i)(api-ms-|ext-ms-)' -or (Test-Path -LiteralPath (Join-Path ([Environment]::SystemDirectory) $dll))) "Import $dll is not supplied by Windows."
    }
    if ($name -ceq 'gridcraft-next.exe') {
      Assert-PackageCheck ($facts.Version.ProductName -ceq 'GridCraft-Next') 'Wrong PE ProductName.'
      Assert-PackageCheck ($facts.Version.FileDescription -ceq 'GridCraft-Next spreadsheet') 'Wrong PE FileDescription.'
      Assert-PackageCheck ($facts.Version.CompanyName -ceq 'xue-ding-e') 'Wrong PE CompanyName.'
      Assert-PackageCheck ($facts.Version.OriginalFilename -ceq 'gridcraft-next.exe') 'Wrong PE OriginalFilename.'
      Assert-PackageCheck ($facts.Version.InternalName -ceq 'gridcraft-next') 'Wrong PE InternalName.'
      Assert-PackageCheck ($facts.Version.ProductVersion -ceq $FullVersion) 'PE ProductVersion must preserve the full Cargo SemVer.'
    }
    $report.pe += [ordered]@{ name = $name; machine = $facts.Machine; subsystem = $facts.Subsystem; imports = $facts.Imports;
      productVersion = $facts.Version.ProductVersion; fileVersion = $facts.Version.FileVersion; signature = 'NotSigned' }
  }
  $cli = Join-Path $PortableDirectory 'gridcraft-next-cli.exe'
  $versionResult = Invoke-CheckedProcess -File $cli -Arguments @('--version') -WorkingDirectory $UnrelatedDirectory `
    -LogPrefix (Join-Path $EvidenceDirectory 'portable-cli-version') -SystemPathOnly
  Assert-PackageCheck ($versionResult.Stdout.Trim() -ceq "gridcraft-next-cli $FullVersion") 'CLI version differs from Cargo/package version.'
  $evalResult = Invoke-CheckedProcess -File $cli -Arguments @('eval', '=SUM(1,2,3)') -WorkingDirectory $UnrelatedDirectory `
    -LogPrefix (Join-Path $EvidenceDirectory 'portable-cli-eval') -SystemPathOnly
  Assert-PackageCheck ($evalResult.Stdout.Trim() -ceq '6') 'Real portable CLI formula evaluation failed.'
  Complete-Check 'portable file/legal parity, real CLI, full version, PE identity, unsigned status, and static CRT imports'

  # Variants never go through package.ps1 or a misleading version override. Reuse the exact
  # real Rust staging bytes and author installer metadata only in a private test directory.
  $VariantDirectory = Join-Path $EvidenceDirectory 'DO-NOT-PUBLISH installer-only variants'
  New-Item -ItemType Directory -Path $VariantDirectory | Out-Null
  $VariantLegalManifest = Join-Path $VariantDirectory 'legal.wxs'
  . (Join-Path $PSScriptRoot 'legal-files.ps1')
  Write-PackageLegalManifest (Join-Path $StageDirectory 'licenses') $VariantLegalManifest
  $sameVersionOtherCode = New-InstallerOnlyVariant $FullVersion 'same-version-other-product-code'
  $transitions = @()
  foreach ($version in '0.6.1-next.0', '0.6.1-next.1', '0.6.1-next.254', '0.6.1', '0.6.2-next.0') {
    $transitions += New-InstallerOnlyVariant $version $version
  }
  Complete-Check 'installer-only variant metadata generated from unchanged real Rust payloads'

  Invoke-Installer 'official-install' @('/i', $UpstreamMsi, 'DESKTOPSHORTCUT=1') $originalPackage.Properties.ProductCode
  Assert-PackageCheck (Test-ExactProductPresent $originalPackage.Properties.ProductCode) 'Official fixture did not register.'
  foreach ($name in 'gridcraft.exe', 'gridcraft-cli.exe') {
    Assert-PackageCheck (Test-Path -LiteralPath (Join-Path $OriginalDirectory $name) -PathType Leaf) 'Official fixture executable missing.'
  }
  Assert-AdvertisedShortcut $OriginalStart $originalPackage.Properties.ProductCode (Join-Path $OriginalDirectory 'gridcraft.exe')
  Assert-PackageCheck (Test-Path -LiteralPath $OriginalDesktop) 'Official fixture desktop shortcut missing.'
  Assert-PlainShortcut $OriginalDesktop (Join-Path $OriginalDirectory 'gridcraft.exe') $OriginalDirectory
  Assert-StateEqual $defaultSnapshot @(Get-AssociationDefaultsState) 'Official installer changed an extension default.'
  $upstreamSnapshot = Get-ProductSnapshot 'GridCraft' $originalPackage.Properties.ProductCode
  $upstreamSnapshot | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath (Join-Path $EvidenceDirectory 'official-installed-snapshot.json') -Encoding utf8
  Complete-Check 'unchanged official MSI installed as an isolated coexistence fixture'

  Invoke-ActualGui $PortableDirectory 'portable'
  Assert-Isolation
  Invoke-Installer 'next-default-off' @('/i', $Msi) $finalPackage.Properties.ProductCode
  Assert-NextPackage $finalPackage $false
  Invoke-ActualGui $NextDirectory 'installed'
  Assert-NextPackage $finalPackage $false
  Complete-Check 'real Next MSI installed beside official GridCraft with default desktop shortcut off'
  Invoke-Installer 'next-identical-msi-repair' @('/i', $Msi, 'REINSTALL=ALL', 'REINSTALLMODE=amus')
  Assert-NextPackage $finalPackage $false
  $beforeEqual = Get-ProductSnapshot 'GridCraft-Next' $finalPackage.Properties.ProductCode
  Invoke-Installer 'next-equal-version-rejected' @('/i', $sameVersionOtherCode.Path) $sameVersionOtherCode.Properties.ProductCode `
    'This version of GridCraft-Next is already installed.'
  Assert-PackageCheck (-not (Test-ExactProductPresent $sameVersionOtherCode.Properties.ProductCode)) 'Rejected equal-version package was registered.'
  Assert-StateEqual $beforeEqual (Get-ProductSnapshot 'GridCraft-Next' $finalPackage.Properties.ProductCode) 'Equal-version rejection changed installed Next files/registry/shortcuts.'
  Assert-NextPackage $finalPackage $false
  Complete-Check 'identical-MSI repair allowed; different-ProductCode equal version rejected without mutation'
  Invoke-Installer 'next-uninstall-default-off' @('/x', $finalPackage.Properties.ProductCode)
  Assert-NextRemoved
  Invoke-Installer 'next-explicit-on' @('/i', $Msi, 'DESKTOPSHORTCUT=1') $finalPackage.Properties.ProductCode
  Assert-NextPackage $finalPackage $true
  Invoke-Installer 'next-identical-msi-repair-on' @('/i', $Msi, 'REINSTALL=ALL', 'REINSTALLMODE=amus')
  Assert-NextPackage $finalPackage $true
  Invoke-Installer 'next-uninstall-explicit-on' @('/x', $finalPackage.Properties.ProductCode)
  Assert-NextRemoved
  Complete-Check 'real MSI explicit desktop opt-in, repair, and uninstall preserve official GridCraft and defaults'

  $current = $transitions[0]
  Invoke-Installer 'transition-initial-next-0' @('/i', $current.Path, 'DESKTOPSHORTCUT=1') $current.Properties.ProductCode
  Assert-NextPackage $current $true
  for ($index = 1; $index -lt $transitions.Count; $index++) {
    $previous = $current
    $current = $transitions[$index]
    $arguments = @('/i', $current.Path)
    # Also verify retention of opt-in, explicit opt-out on upgrade, then retention of opt-out.
    if ($index -eq 2) { $arguments += 'DESKTOPSHORTCUT=0' }
    Invoke-Installer "transition-upgrade-$index" $arguments $current.Properties.ProductCode
    Assert-PackageCheck (-not (Test-ExactProductPresent $previous.Properties.ProductCode)) 'Major upgrade left the previous product registered.'
    Assert-NextPackage $current ($index -lt 2)
    $beforeDowngrade = Get-ProductSnapshot 'GridCraft-Next' $current.Properties.ProductCode
    Invoke-Installer "transition-downgrade-$index-rejected" @('/i', $previous.Path) $previous.Properties.ProductCode `
      'A newer version of GridCraft-Next is already installed.'
    Assert-PackageCheck (-not (Test-ExactProductPresent $previous.Properties.ProductCode)) 'Rejected downgrade product became registered.'
    Assert-StateEqual $beforeDowngrade (Get-ProductSnapshot 'GridCraft-Next' $current.Properties.ProductCode) 'Downgrade rejection changed the active product.'
    Assert-NextPackage $current ($index -lt 2)
  }
  Invoke-Installer 'transition-final-repair' @('/i', $current.Path, 'REINSTALL=ALL', 'REINSTALLMODE=amus')
  Assert-NextPackage $current $false
  Invoke-Installer 'transition-final-uninstall' @('/x', $current.Properties.ProductCode)
  Assert-NextRemoved
  Complete-Check 'real-byte installer variants: next.0 to next.1, next.254 to stable, stable to next patch; every reverse rejected unchanged'
  foreach ($artifact in $report.inputs) {
    Assert-PackageCheck ((Get-FileHash -LiteralPath $artifact.path -Algorithm SHA256).Hash.ToLowerInvariant() -ceq $artifact.sha256) 'Final candidate artifact changed during transition testing.'
  }
  Complete-Check 'final matching-version candidate artifacts stayed immutable throughout all installer-only tests'
} catch {
  $failure = $_
  $report.status = 'failed'
  $report.failure = $_.ToString()
} finally {
  # Keep logs and fixtures for diagnosis; never remove user documents/preferences or registry
  # trees to conceal failed MSI cleanup. Only exact ProductCodes attempted here are eligible.
  $codes = @($attemptedProducts.ToArray())
  [array]::Reverse($codes)
  foreach ($code in $codes) {
    if ($null -ne $originalPackage -and $code -ieq $originalPackage.Properties.ProductCode -and $null -ne $upstreamSnapshot) {
      # Check isolation once more after Next cleanup and before removing our official fixture.
      try { Assert-NextRemoved; Assert-Isolation }
      catch { $report.cleanupErrors += $_.ToString(); if ($null -eq $failure) { $failure = $_ } }
    }
    try {
      if (Test-ExactProductPresent $code) {
        Invoke-Installer ('cleanup-' + $code.Trim('{}')) @('/x', $code)
      }
      Assert-PackageCheck (-not (Test-ExactProductPresent $code)) "Exact fixture product $code remains after cleanup."
      Assert-PackageCheck (-not (Test-Path -LiteralPath "$UninstallRoot\$code")) "Exact fixture ARP entry $code remains after cleanup."
    } catch {
      $report.cleanupErrors += $_.ToString()
      if ($null -eq $failure) { $failure = $_ }
    }
  }
  $upstreamSnapshot = $null # The exact official fixture is now removed; defaults remain protected.
  if ($null -ne $originalPackage -and $attemptedProducts.Count -gt 0) {
    try { Assert-NextRemoved }
    catch { $report.cleanupErrors += $_.ToString(); if ($null -eq $failure) { $failure = $_ } }
  }
  if ($null -ne $originalPackage -and $attemptedProducts.Contains($originalPackage.Properties.ProductCode)) {
    try {
      Assert-PackageCheck (@(Get-DirectoryHashes $OriginalDirectory).Count -eq 0) 'Official fixture files remain after its exact-product uninstall.'
      Assert-PackageCheck (-not (Test-Path -LiteralPath $OriginalStart) -and -not (Test-Path -LiteralPath $OriginalDesktop)) 'Official fixture shortcuts remain after cleanup.'
      foreach ($row in $originalPackage.Tables.Registry) {
        Assert-PackageCheck ([int] $row.Root -in @(2, -1)) 'Official fixture used an unreviewed registry hive.'
        Assert-PackageCheck (-not (Get-RegistryValueState ('HKLM:\' + $row.Key) $row.Name).Exists) 'Official fixture registry value remains after cleanup.'
      }
    } catch { $report.cleanupErrors += $_.ToString(); if ($null -eq $failure) { $failure = $_ } }
  }
  try { Assert-StateEqual $defaultSnapshot @(Get-AssociationDefaultsState) 'Extension defaults/UserChoice changed by the test.' }
  catch { $report.cleanupErrors += $_.ToString(); if ($null -eq $failure) { $failure = $_ } }
  $report.finishedUtc = [DateTime]::UtcNow.ToString('o')
  $report.status = if ($null -eq $failure) { 'passed' } else { 'failed' }
  Save-Report
}
if ($null -ne $failure) { throw $failure }
Write-Host 'PASS: real Rust MSI/portable runtime, install/repair/upgrade/rejection/removal and official-upstream isolation. Evidence is not publication/license approval.'
