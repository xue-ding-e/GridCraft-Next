<#
.SYNOPSIS
  Test the authoritative version helper and MSI authoring contract without building or installing.
.DESCRIPTION
  Run: pwsh -NoProfile -File packaging/windows/test-version.ps1
  No Pester, Python, Rust, WiX, administrator access, or package installation is needed.
  Exhausts all 65,536 build values for one major/minor pair, checks rollovers,
  rejects malformed/out-of-domain input, and tests the Cargo override contract.

  XML assertions below are authoring checks, NOT compiled-MSI or installation evidence.
  The Windows delivery gate must separately inspect the actual Property/Upgrade and
  InstallUISequence/InstallExecuteSequence tables (including SecureCustomProperties).
  It must install next.0 -> next.1, next.254 -> stable -> following patch next.0;
  reject reverse/downgrade transitions and equal versions with another ProductCode
  without changing installed registry/binary hashes; repair the identical MSI; and
  uninstall cleanly. Also prove original GridCraft files, registrations and extension
  defaults remain untouched. Installer fixtures alone do not validate the Rust app.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'version.ps1')
$script:Assertions = 0

function Assert-That([bool] $Condition, [string] $Message) {
  $script:Assertions++
  if (-not $Condition) { throw $Message }
}

function Assert-Equal($Actual, $Expected, [string] $Message) {
  Assert-That ($Actual -ceq $Expected) "$Message (expected '$Expected', got '$Actual')"
}

function Assert-Throws([scriptblock] $Action, [string] $Message) {
  $threw = $false
  try { & $Action | Out-Null } catch { $threw = $true }
  Assert-That $threw $Message
}

function Assert-Increasing([string] $Lower, [string] $Higher) {
  $a = [version] (ConvertTo-GridCraftMsiVersion $Lower)
  $b = [version] (ConvertTo-GridCraftMsiVersion $Higher)
  Assert-That ($a.CompareTo($b) -lt 0) "MSI order must preserve $Lower < $Higher"
}

$examples = [ordered]@{
  '0.0.0-next.0' = '0.0.0'
  '0.0.0' = '0.0.255'
  '0.6.1-next.0' = '0.6.256'
  '0.6.1-next.9' = '0.6.265'
  '0.6.1-next.10' = '0.6.266'
  '0.6.1-next.99' = '0.6.355'
  '0.6.1-next.100' = '0.6.356'
  '0.6.1-next.254' = '0.6.510'
  '0.6.1' = '0.6.511'
  '0.6.2-next.0' = '0.6.512'
  '0.6.255' = '0.6.65535'
  '0.7.0-next.0' = '0.7.0'
  '255.255.255-next.254' = '255.255.65534'
  '255.255.255' = '255.255.65535'
}
foreach ($example in $examples.GetEnumerator()) {
  Assert-Equal (ConvertTo-GridCraftMsiVersion $example.Key) $example.Value "Mapping $($example.Key)"
}
Write-Output 'ok exact examples and domain endpoints'

# Every patch contributes 255 prereleases followed by one stable release. Sequential
# expected values, uniqueness and Version.CompareTo verify the helper itself, rather
# than duplicating its radix formula as a second converter in another language.
$seen = [Collections.Generic.HashSet[int]]::new()
$expectedBuild = 0
$previous = $null
for ($patch = 0; $patch -le 255; $patch++) {
  for ($rank = 0; $rank -le 255; $rank++) {
    $semver = if ($rank -eq 255) { "7.9.$patch" } else { "7.9.$patch-next.$rank" }
    $encoded = ConvertTo-GridCraftMsiVersion $semver
    $numeric = [version] $encoded
    Assert-Equal $numeric.Major 7 'Major must remain unchanged'
    Assert-Equal $numeric.Minor 9 'Minor must remain unchanged'
    Assert-Equal $numeric.Revision -1 'MSI version must have exactly three fields'
    Assert-Equal $numeric.Build $expectedBuild "Build value for $semver"
    Assert-That ($seen.Add($numeric.Build)) "Collision at $semver"
    if ($null -ne $previous) {
      Assert-That ($previous.CompareTo($numeric) -lt 0) "Non-monotonic mapping at $semver"
    }
    $previous = $numeric
    $expectedBuild++
  }
}
Assert-Equal $seen.Count 65536 'All MSI build values must be reached exactly once'
Assert-Equal $expectedBuild 65536 'All patch/rank pairs must have been checked'
Assert-That ($seen.Contains(0) -and $seen.Contains(65535)) 'Both build endpoints must be reached'
Write-Output 'ok all 65,536 distinct MSI build values, strictly increasing'

Assert-Increasing '0.6.1-next.9' '0.6.1-next.10'
Assert-Increasing '0.6.1-next.99' '0.6.1-next.100'
Assert-Increasing '0.6.1-next.254' '0.6.1'
Assert-Increasing '0.6.1' '0.6.2-next.0'
for ($minor = 0; $minor -lt 255; $minor++) {
  Assert-Increasing "0.$minor.255" "0.$($minor + 1).0-next.0"
  Assert-Increasing "255.$minor.255" "255.$($minor + 1).0-next.0"
}
for ($major = 0; $major -lt 255; $major++) {
  Assert-Increasing "$major.255.255" "$($major + 1).0.0-next.0"
}
Write-Output 'ok numeric prerelease, stable, patch, minor and major rollover ordering'

$invalid = @(
  $null, '', ' ', '1', '1.2', '1.2.3.4', 'v1.2.3', '=1.2.3', '01.2.3', '1.02.3', '1.2.03',
  '1.2.3-next.00', '1.2.3-next.01', '1.2.3-next.001', '1.2.3-next', '1.2.3-next.',
  '1.2.3-next.-1', '1.2.3-next.+1', '1.2.3-next.1.0', '1.2.3-next.1-', '1.2.3-NEXT.1',
  '1.2.3-alpha.1', '1.2.3-rc.1', '1.2.3-beta', '1.2.3-', '1.2.3+build', '1.2.3-next.1+build',
  '256.0.0', '0.256.0', '0.0.256', '999.999.999', '1.2.3-next.255', '1.2.3-next.256',
  '-1.2.3', '1.-2.3', '1.2.-3', '+1.2.3', '1.2.3-next.1e2', '1.2.3-next.0x1',
  ' 1.2.3', '1.2.3 ', '1.2.3 next.1', "1.2.3`n", "1.2.3`r`n", "`t1.2.3", "1.2.3`0",
  '1.2.3-next.１', '１.2.3', '1.٢.3', '1.2.3-next.١', '1.2.3−next.1',
  '2147483648.0.0', '18446744073709551616.0.0', ('9' * 10000), ('1.2.3-next.' + ('9' * 10000))
)
foreach ($bad in $invalid) {
  Assert-Throws { ConvertTo-GridCraftMsiVersion $bad } 'Malformed or out-of-domain version was accepted'
}
Write-Output "ok $($invalid.Count) malformed/out-of-domain inputs rejected"

$culture = [Threading.Thread]::CurrentThread.CurrentCulture
try {
  foreach ($name in 'en-US', 'tr-TR', 'ar-SA') {
    [Threading.Thread]::CurrentThread.CurrentCulture = [Globalization.CultureInfo]::GetCultureInfo($name)
    Assert-Equal (ConvertTo-GridCraftMsiVersion '255.255.255-next.254') '255.255.65534' "Culture $name"
    Assert-Throws { ConvertTo-GridCraftMsiVersion '1.2.3-NEXT.1' } "Case-sensitive suffix in $name"
  }
} finally {
  [Threading.Thread]::CurrentThread.CurrentCulture = $culture
}
Write-Output 'ok culture-independent output and case-sensitive suffix'

$temp = Join-Path ([IO.Path]::GetTempPath()) ('gridcraft-next-version-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temp | Out-Null
try {
  $manifest = Join-Path $temp 'Cargo.toml'
  @(
    '[package]', 'version = "99.0.0"',
    ' [workspace.package] # authoritative', ' version = "0.6.1-next.0" # version comment',
    '[dependencies]', 'version = "88.0.0"'
  ) | Set-Content -LiteralPath $manifest -Encoding utf8
  foreach ($override in @($null, '0.6.1-next.0')) {
    $resolved = Get-GridCraftPackageVersion -ManifestPath $manifest -VersionOverride $override
    Assert-Equal $resolved.FullVersion '0.6.1-next.0' 'Full SemVer must remain unchanged'
    Assert-Equal $resolved.MsiVersion '0.6.256' 'Workspace version must be encoded'
  }
  foreach ($override in @('', ' ', '0.6.1', '0.6.1-next.1', '0.6.1-NEXT.0', '0.6.256', 123)) {
    Assert-Throws { Get-GridCraftPackageVersion -ManifestPath $manifest -VersionOverride $override } 'Mismatched override was accepted'
  }
  $invalidManifests = @(
    ('[package]' + "`n" + 'version = "0.6.1-next.0"'),
    '[workspace.package]',
    ('[workspace.package]' + "`n" + 'version = ""'),
    ('[workspace.package]' + "`n" + 'version = "0.6.1-next.255"'),
    ('[workspace.package]' + "`n" + 'version = "0.6.1-next.0" trailing'),
    ('[workspace.package]' + "`n" + 'version = "0.6.1-next.0"' + "`n" + 'version = "0.6.1-next.1"'),
    ('[workspace.package]' + "`n" + 'version = "0.6.1-next.0"' + "`n" + '[workspace.package]'),
    ('[workspace.package]' + "`n" + 'version = 123')
  )
  foreach ($content in $invalidManifests) {
    Set-Content -LiteralPath $manifest -Value $content -Encoding utf8
    Assert-Throws { Get-GridCraftPackageVersion -ManifestPath $manifest -VersionOverride '0.6.1-next.0' } 'Invalid manifest was hidden by an override'
  }
  Assert-Throws { Get-GridCraftPackageVersion -ManifestPath (Join-Path $temp 'missing.toml') } 'Missing Cargo.toml was accepted'
} finally {
  Remove-Item -LiteralPath $temp -Recurse -Force
}
Write-Output 'ok Cargo workspace extraction and exact-match override contract'

# Source-only installer identity/guard checks. The real MSI test must assert emitted
# tables and actual behavior, because well-formed XML alone cannot prove either.
[xml] $wix = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'gridcraft.wxs') -Raw
$ns = [Xml.XmlNamespaceManager]::new($wix.NameTable)
$ns.AddNamespace('w', 'http://wixtoolset.org/schemas/v4/wxs')
$package = $wix.SelectSingleNode('/w:Wix/w:Package', $ns)
$nextUpgradeCode = '73F9F3B2-548C-4F5B-8934-253B3C29E0B1'
$upstreamUpgradeCode = '5511327B-F305-41BE-92D0-EB84944E7C4D'
Assert-Equal $package.GetAttribute('UpgradeCode') $nextUpgradeCode 'Independent Next UpgradeCode is immutable'
Assert-That ([guid] $nextUpgradeCode -ne [guid] $upstreamUpgradeCode) 'Next and upstream must have separate upgrade families'
foreach ($upgrade in $wix.SelectNodes('//w:Upgrade', $ns)) {
  Assert-Equal $upgrade.GetAttribute('Id') $nextUpgradeCode 'No authored upgrade may target original GridCraft'
}
Assert-Equal $package.SelectSingleNode('w:MajorUpgrade', $ns).GetAttribute('AllowSameVersionUpgrades') '' 'Same-version upgrades must not be enabled'
$detection = $package.SelectSingleNode('w:Upgrade/w:UpgradeVersion[@Property="GRIDCRAFTNEXT_SAME_VERSION"]', $ns)
Assert-That ($null -ne $detection) 'Exact-version detection row is required'
foreach ($bound in 'Minimum', 'Maximum') {
  Assert-Equal $detection.GetAttribute($bound) '$(var.Version)' "Exact-version detection $bound"
}
foreach ($flag in 'OnlyDetect', 'IncludeMinimum', 'IncludeMaximum') {
  Assert-Equal $detection.GetAttribute($flag) 'yes' "Exact-version detection $flag"
}
$secure = $package.SelectSingleNode('w:Property[@Id="GRIDCRAFTNEXT_SAME_VERSION"]', $ns)
Assert-Equal $secure.GetAttribute('Secure') 'yes' 'Detection property must cross elevation boundary'
$launch = $package.SelectSingleNode('w:Launch', $ns)
Assert-Equal $launch.GetAttribute('Condition') 'Installed OR NOT GRIDCRAFTNEXT_SAME_VERSION' 'Guard must allow same-product maintenance and reject a detected equal-version product'
foreach ($table in 'InstallUISequence', 'InstallExecuteSequence') {
  $find = $package.SelectSingleNode("w:$table/w:FindRelatedProducts", $ns)
  $conditions = $package.SelectSingleNode("w:$table/w:LaunchConditions", $ns)
  Assert-That ($null -ne $find -and $null -ne $conditions) "$table must sequence detection and launch conditions"
  Assert-That ([int] $find.GetAttribute('Sequence') -lt [int] $conditions.GetAttribute('Sequence')) "$table must detect before evaluating the guard"
}
$fullVersion = $package.SelectSingleNode('w:Property[@Id="GRIDCRAFTNEXT_FULL_VERSION"]', $ns)
Assert-Equal $fullVersion.GetAttribute('Value') '$(var.FullVersion)' 'Full SemVer must survive in MSI metadata'
Write-Output 'ok source-only WiX independent identity, equal-version guard, maintenance exemption and sequencing'
Write-Output "PASS: $script:Assertions assertions; no MSI was built or installed by this test."
