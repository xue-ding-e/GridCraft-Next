<#
.SYNOPSIS
  Cheap real WiX/WindowsInstaller COM regression before any Rust build.
.DESCRIPTION
  Requires Windows and official WiX 5.0.2 already on PATH. Builds the production WiX
  authoring with three tiny, original text fixtures (including non-executable .exe
  placeholders), then opens its MSI read-only. NEVER installs or launches the fixture.
  Validates both consumers of the shared row reader and the actual version/upgrade
  table assertions. This is helper/authoring evidence, never native-app evidence.
  Run: pwsh -NoProfile -File packaging/windows/test-msi-database.ps1
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'This COM regression requires Windows.' }
$wixVersion = & wix --version
if ($LASTEXITCODE -ne 0 -or $wixVersion -notmatch '^5\.0\.2(?:\+|\s|$)') { throw 'Official WiX 5.0.2 is required.' }
. (Join-Path $PSScriptRoot 'version.ps1')
. (Join-Path $PSScriptRoot 'package-test-common.ps1')
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$work = Join-Path ([IO.Path]::GetTempPath()) ('gridcraft-next-msi-reader-' + [guid]::NewGuid().ToString('N'))
$installer = $null
$database = $null
New-Item -ItemType Directory -Path $work | Out-Null
try {
  foreach ($name in 'gridcraft-next.exe', 'gridcraft-next-cli.exe', 'fixture-license') {
    [IO.File]::WriteAllText((Join-Path $work $name), 'Original synthetic MSI reader fixture. Not an application; never install or execute.')
  }
  $legal = Join-Path $work 'legal.wxs'
  @'
<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">
  <Fragment>
    <ComponentGroup Id="GridcraftLegalFiles" Directory="LICENSEFOLDER">
      <Component Id="ReaderFixtureLicense">
        <File Id="ReaderFixtureLicenseFile" Source="$(var.BinDir)\fixture-license" KeyPath="yes" />
      </Component>
    </ComponentGroup>
  </Fragment>
</Wix>
'@ | Set-Content -LiteralPath $legal -Encoding utf8
  $fullVersion = '0.6.1-next.0'
  $numeric = ConvertTo-GridCraftMsiVersion $fullVersion
  $msi = Join-Path $work 'reader-fixture-never-install.msi'
  & wix build (Join-Path $PSScriptRoot 'gridcraft.wxs') $legal -arch x64 `
    -d "Version=$numeric" -d "FullVersion=$fullVersion" -d "BinDir=$work" `
    -d "IconPath=$(Join-Path $root 'assets\app-icon\gridcraft.ico')" -o $msi
  if ($LASTEXITCODE -ne 0) { throw 'Could not build the tiny WiX reader regression fixture.' }
  $installer = New-Object -ComObject WindowsInstaller.Installer
  $database = $installer.OpenDatabase($msi, 0)

  # Record the actual COM adapter behavior on this PowerShell/Windows image rather
  # than guessing which lifecycle return value contaminated the original pipeline.
  $probe = $database.OpenView('SELECT `Property`, `Value` FROM `Property` WHERE `Property` = ''GRIDCRAFTNEXT_NONEXISTENT_READER_FIXTURE''')
  try {
    $legacyOutputs = @($probe.Execute(); $probe.Close())
    $legacyTypes = @($legacyOutputs | ForEach-Object { if ($null -eq $_) { '<null>' } else { $_.GetType().FullName } })
    Write-Output "Diagnostic: unsuppressed COM lifecycle output count=$($legacyOutputs.Count); types=$($legacyTypes -join ',')"
  } finally { Release-ComObject $probe }

  # Show the actual COM Record shape, then exercise documented property dispatch.
  # This diagnoses adapter differences before either consumer handles real rows.
  $recordProbeView = $null
  $recordProbe = $null
  try {
    $recordProbeView = $database.OpenView('SELECT `Property`, `Value` FROM `Property` WHERE `Property` = ''ProductName''')
    [void] $recordProbeView.Execute()
    $recordProbe = $recordProbeView.Fetch()
    Assert-PackageCheck ($null -ne $recordProbe) 'Record diagnostic query returned no row.'
    $recordType = $recordProbe.GetType()
    $recordIsCom = [Runtime.InteropServices.Marshal]::IsComObject($recordProbe)
    $memberNames = @($recordProbe.PSObject.Members | ForEach-Object { $_.Name })
    Write-Output "Diagnostic: Record type=$($recordType.FullName); IsComObject=$recordIsCom; members=$($memberNames -join ',')"
    Assert-PackageCheck $recordIsCom 'Fetch must produce a genuine COM Record.'
    $probeFieldCount = [int] (Get-GridCraftMsiRecordProperty -Record $recordProbe -Name FieldCount)
    Assert-PackageCheck ($probeFieldCount -eq 2) 'COM FieldCount getter returned the wrong field count.'
    $probeName = [string] (Get-GridCraftMsiRecordProperty -Record $recordProbe -Name StringData -Arguments @(1))
    Assert-PackageCheck ($probeName -ceq 'ProductName') 'COM indexed StringData getter returned the wrong field.'
  } finally {
    Release-ComObject $recordProbe
    if ($null -ne $recordProbeView) {
      try { [void] $recordProbeView.Close() } finally { Release-ComObject $recordProbeView }
    }
  }

  $none = @(Read-GridCraftMsiRows -Database $database -Sql 'SELECT `Property`, `Value` FROM `Property` WHERE `Property` = ''GRIDCRAFTNEXT_NONEXISTENT_READER_FIXTURE''' -Columns @('Property', 'Value'))
  Assert-PackageCheck ($none.Count -eq 0) 'Empty query leaked lifecycle values or a nested collection.'
  $one = @(Read-GridCraftMsiRows -Database $database -Sql 'SELECT `Property`, `Value` FROM `Property` WHERE `Property` = ''ProductName''' -Columns @('Property', 'Value'))
  Assert-PackageCheck ($one.Count -eq 1) 'Single-row query did not return exactly one row object.'
  Assert-PackageCheck ($one[0] -is [pscustomobject]) 'Single-row result must be a PSCustomObject.'
  Assert-PackageCheck (($one[0].PSObject.Properties.Name -join ',') -ceq 'Property,Value') 'Single-row columns changed shape.'
  Assert-PackageCheck ($one[0].Property -ceq 'ProductName' -and $one[0].Value -ceq 'GridCraft-Next') 'Single-row Property/Value read failed.'
  $many = @(Read-GridCraftMsiRows -Database $database -Sql 'SELECT `Property`, `Value` FROM `Property`' -Columns @('Property', 'Value'))
  Assert-PackageCheck ($many.Count -gt 5) 'Multi-row query must return separate row objects.'
  foreach ($row in $many) {
    Assert-PackageCheck ($row -is [pscustomobject]) 'Multi-row query leaked a non-row result.'
    Assert-PackageCheck (($row.PSObject.Properties.Name -join ',') -ceq 'Property,Value') 'Multi-row query lost Property/Value columns.'
  }
  $badShapeRejected = $false
  try {
    Read-GridCraftMsiRows -Database $database -Sql 'SELECT `Property`, `Value` FROM `Property`' -Columns @('OnlyOne') | Out-Null
  } catch {
    if ($_.Exception.Message -notlike 'MSI row shape mismatch:*') { throw }
    $badShapeRejected = $true
  }
  Assert-PackageCheck $badShapeRejected 'The reader accepted a mismatched column contract.'
  # A separate disposable COM-created database covers nullable/empty values without
  # mutating the real WiX MSI above. MSI normalizes empty strings and NULL to empty
  # StringData; both insertion forms must retain their row and exact field shape.
  $nullableDatabase = $null
  try {
    $nullableDatabase = $installer.OpenDatabase((Join-Path $work 'nullable-fixture.msi'), 3)
    foreach ($sql in @(
      'CREATE TABLE `ReaderFixture` (`Key` CHAR(72) NOT NULL, `Value` CHAR(255) PRIMARY KEY `Key`)',
      'INSERT INTO `ReaderFixture` (`Key`) VALUES (''null-value'')',
      'INSERT INTO `ReaderFixture` (`Key`, `Value`) VALUES (''empty-value'', '''')',
      'INSERT INTO `ReaderFixture` (`Key`, `Value`) VALUES (''text-value'', ''literal-value'')'
    )) {
      $writeView = $null
      try {
        $writeView = $nullableDatabase.OpenView($sql)
        [void] $writeView.Execute()
      } finally {
        if ($null -ne $writeView) {
          try { [void] $writeView.Close() } finally { Release-ComObject $writeView }
        }
      }
    }
    $nullableRows = @(Read-GridCraftMsiRows -Database $nullableDatabase -Sql 'SELECT `Key`, `Value` FROM `ReaderFixture`' -Columns @('Key', 'Value'))
    Assert-PackageCheck ($nullableRows.Count -eq 3) 'NULL/empty fixture must retain all three rows.'
    $nullableValues = @{}
    foreach ($row in $nullableRows) {
      Assert-PackageCheck (($row.PSObject.Properties.Name -join ',') -ceq 'Key,Value') 'NULL/empty fixture lost its column shape.'
      $nullableValues[$row.Key] = $row.Value
    }
    Assert-PackageCheck ($nullableValues['null-value'] -ceq '') 'NULL StringData must be returned as an empty string.'
    Assert-PackageCheck ($nullableValues['empty-value'] -ceq '') 'Empty StringData must be returned as an empty string.'
    Assert-PackageCheck ($nullableValues['text-value'] -ceq 'literal-value') 'Nonempty StringData must be preserved.'
  } finally { Release-ComObject $nullableDatabase }
  # Close this read session before exercising the two real consumers independently.
  Release-ComObject $database
  $database = $null
  Release-ComObject $installer
  $installer = $null
  $package = Read-PackageDatabase $msi
  Assert-PackageCheck ($package.Properties.ProductVersion -ceq $numeric) 'Shared package reader lost MSI ProductVersion.'
  Assert-PackageCheck ($package.Tables.File.Count -eq 3) 'Shared package reader lost synthetic File rows.'
  Assert-PackageCheck ($package.Tables.Upgrade.Count -eq 3) 'Shared package reader lost Upgrade rows.'
  & (Join-Path $PSScriptRoot 'test-msi-version.ps1') -Msi $msi -FullVersion $fullVersion
  $wrongVersionRejected = $false
  try {
    & (Join-Path $PSScriptRoot 'test-msi-version.ps1') -Msi $msi -FullVersion '0.6.1-next.1'
  } catch {
    if ($_.Exception.Message -cne 'MSI ProductVersion does not match the authoritative SemVer encoding') { throw }
    $wrongVersionRejected = $true
  }
  Assert-PackageCheck $wrongVersionRejected 'The version validator accepted the wrong expected SemVer.'
  Write-Output 'PASS: real WiX MSI + COM zero/one/many row shape, strict mismatch rejection, both consumers and full version/upgrade table validator. No installation or Rust build.'
} finally {
  Release-ComObject $database
  Release-ComObject $installer
  Remove-Item -LiteralPath $work -Recurse -Force
}
