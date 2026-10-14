<#
.SYNOPSIS
  Read-only version/upgrade-contract checks against a compiled GridCraft-Next MSI.
.DESCRIPTION
  Run on Windows after packaging:
    pwsh -NoProfile -File packaging/windows/test-msi-version.ps1 -Msi <path> -FullVersion 0.6.1-next.0
  Opens the MSI database read-only; does not install, repair, uninstall or modify it.
  Passing is emitted-table evidence only. Real transition/repair/isolation tests are
  still required; see test-version.ps1 for that separate Windows delivery gate scope.
#>
param(
  [Parameter(Mandatory)] [string] $Msi,
  [Parameter(Mandatory)] [string] $FullVersion
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
  throw 'Reading MSI tables through WindowsInstaller COM requires Windows.'
}
. (Join-Path $PSScriptRoot 'version.ps1')
. (Join-Path $PSScriptRoot 'msi-database.ps1')
$expectedVersion = ConvertTo-GridCraftMsiVersion $FullVersion
$nextUpgradeCode = [guid] '73F9F3B2-548C-4F5B-8934-253B3C29E0B1'
$upstreamUpgradeCode = [guid] '5511327B-F305-41BE-92D0-EB84944E7C4D'
$detectionProperty = 'GRIDCRAFTNEXT_SAME_VERSION'
$guard = 'Installed OR NOT GRIDCRAFTNEXT_SAME_VERSION'

function Assert-Msi([bool] $Condition, [string] $Message) {
  if (-not $Condition) { throw $Message }
}


$installer = $null
$database = $null
try {
  $path = (Resolve-Path -LiteralPath $Msi).Path
  $installer = New-Object -ComObject WindowsInstaller.Installer
  $database = $installer.OpenDatabase($path, 0) # msiOpenDatabaseModeReadOnly
  $properties = @{}
  foreach ($row in Read-GridCraftMsiRows $database 'SELECT `Property`, `Value` FROM `Property`' @('Property', 'Value')) {
    $properties[$row.Property] = $row.Value
  }
  Assert-Msi ($properties.ProductVersion -ceq $expectedVersion) 'MSI ProductVersion does not match the authoritative SemVer encoding'
  Assert-Msi ($properties.GRIDCRAFTNEXT_FULL_VERSION -ceq $FullVersion) 'MSI lost the full Cargo SemVer'
  Assert-Msi ($properties.ARPCOMMENTS -ceq "GridCraft-Next $FullVersion") 'MSI display metadata lost the full Cargo SemVer'
  Assert-Msi ($properties.ProductName -ceq 'GridCraft-Next') 'Wrong independent product name'
  Assert-Msi ([guid] $properties.UpgradeCode -eq $nextUpgradeCode) 'Next UpgradeCode changed'
  Assert-Msi ([guid] $properties.UpgradeCode -ne $upstreamUpgradeCode) 'MSI must not join the upstream upgrade family'
  Assert-Msi ([guid] $properties.ProductCode -ne [guid]::Empty) 'MSI must have a valid ProductCode'
  Assert-Msi (($properties.SecureCustomProperties -split ';') -ccontains $detectionProperty) 'Same-version detection property is not secure across elevation'

  $upgrades = @(Read-GridCraftMsiRows $database 'SELECT `UpgradeCode`, `VersionMin`, `VersionMax`, `Language`, `Attributes`, `ActionProperty` FROM `Upgrade`' @('UpgradeCode', 'VersionMin', 'VersionMax', 'Language', 'Attributes', 'ActionProperty'))
  foreach ($row in $upgrades) {
    Assert-Msi ([guid] $row.UpgradeCode -eq $nextUpgradeCode) 'Upgrade table targets a product outside the independent Next family'
  }
  $equal = @($upgrades | Where-Object { $_.ActionProperty -ceq $detectionProperty })
  Assert-Msi ($equal.Count -eq 1) 'Expected exactly one same-version detection row'
  Assert-Msi ($equal[0].VersionMin -ceq $expectedVersion -and $equal[0].VersionMax -ceq $expectedVersion) 'Same-version range is not exact'
  # OnlyDetect (2) + VersionMinInclusive (256) + VersionMaxInclusive (512).
  # https://learn.microsoft.com/windows/win32/msi/upgrade-table
  Assert-Msi ([int] $equal[0].Attributes -eq 770) 'Same-version row must be inclusive and detection-only'
  Assert-Msi ([string]::IsNullOrEmpty($equal[0].Language)) 'Same-version detection must cover all languages'

  $older = @($upgrades | Where-Object { $_.ActionProperty -ceq 'WIX_UPGRADE_DETECTED' })
  Assert-Msi ($older.Count -eq 1) 'Expected exactly one lower-version major-upgrade row'
  Assert-Msi ($older[0].VersionMax -ceq $expectedVersion) 'Major-upgrade upper bound differs from ProductVersion'
  Assert-Msi (([int] $older[0].Attributes -band 512) -eq 0) 'Major upgrade must not remove equal-version products'
  Assert-Msi (([int] $older[0].Attributes -band 2) -eq 0) 'Lower versions must be eligible for major upgrade'
  $newer = @($upgrades | Where-Object { $_.ActionProperty -ceq 'WIX_DOWNGRADE_DETECTED' })
  Assert-Msi ($newer.Count -eq 1) 'Expected exactly one downgrade-detection row'
  Assert-Msi ($newer[0].VersionMin -ceq $expectedVersion) 'Downgrade lower bound differs from ProductVersion'
  Assert-Msi (([int] $newer[0].Attributes -band 2) -ne 0) 'Newer versions must only be detected'
  Assert-Msi (([int] $newer[0].Attributes -band 256) -eq 0) 'Downgrade detection must exclude the equal version'

  $launches = @(Read-GridCraftMsiRows $database 'SELECT `Condition`, `Description` FROM `LaunchCondition`' @('Condition', 'Description'))
  $sameVersionLaunch = @($launches | Where-Object { $_.Condition -ceq $guard })
  Assert-Msi ($sameVersionLaunch.Count -eq 1) 'Missing equal-version guard or original-MSI maintenance exemption'
  Assert-Msi (-not [string]::IsNullOrWhiteSpace($sameVersionLaunch[0].Description)) 'Equal-version guard has no explanation'
  Assert-Msi (@($launches | Where-Object { $_.Condition -match 'WIX_DOWNGRADE_DETECTED' }).Count -eq 1) 'Missing downgrade launch condition'
  foreach ($table in 'InstallUISequence', 'InstallExecuteSequence') {
    $rows = @(Read-GridCraftMsiRows $database ('SELECT `Action`, `Sequence`, `Condition` FROM `' + $table + '`') @('Action', 'Sequence', 'Condition'))
    $find = @($rows | Where-Object { $_.Action -ceq 'FindRelatedProducts' })
    $launch = @($rows | Where-Object { $_.Action -ceq 'LaunchConditions' })
    Assert-Msi ($find.Count -eq 1 -and $launch.Count -eq 1) "$table is missing required standard actions"
    Assert-Msi ([int] $find[0].Sequence -gt 0 -and [int] $find[0].Sequence -lt [int] $launch[0].Sequence) "$table must detect related products before checking launch conditions"
    Assert-Msi ([string]::IsNullOrEmpty($find[0].Condition) -and [string]::IsNullOrEmpty($launch[0].Condition)) "$table must not conditionally bypass related-product detection or launch conditions"
  }
  Write-Output "PASS: compiled MSI tables for $FullVersion ($expectedVersion), ProductCode $($properties.ProductCode). No installation was performed."
} finally {
  foreach ($item in $database, $installer) {
    if ($null -ne $item) { [Runtime.InteropServices.Marshal]::FinalReleaseComObject($item) | Out-Null }
  }
}
