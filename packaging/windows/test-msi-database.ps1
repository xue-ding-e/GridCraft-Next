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
