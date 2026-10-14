# Shared read-only inspection and bounded-process helpers for the disposable Windows gate.
# This file has no install, download, cleanup, or environment-changing side effects when loaded.
function Assert-PackageCheck([bool] $Condition, [string] $Message) {
  if (-not $Condition) { throw $Message }
}

function Assert-HostedWindows {
  Assert-PackageCheck ($env:GITHUB_ACTIONS -ceq 'true' -and $env:RUNNER_OS -ceq 'Windows' -and
    $env:RUNNER_ENVIRONMENT -ceq 'github-hosted') 'Refusing: only a disposable GitHub-hosted Windows runner is supported.'
  Assert-PackageCheck ($env:ImageOS -ceq 'win22') 'This gate is scoped to the reviewed windows-2022 image.'
  Assert-PackageCheck ([Environment]::Is64BitProcess -and [Environment]::Is64BitOperatingSystem) 'Use 64-bit PowerShell on an x64 Windows runner.'
  Assert-PackageCheck ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString() -ceq 'X64') 'Only native x64 is supported.'
  Assert-PackageCheck (-not [string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) 'RUNNER_TEMP is required.'
}

function Assert-RunnerTemporaryPath([string] $Path) {
  $root = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\') + '\'
  $full = [IO.Path]::GetFullPath($Path)
  Assert-PackageCheck ($full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) 'Test output must be a child of RUNNER_TEMP.'
  return $full
}

function Release-ComObject($Value) {
  if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
    [Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value) | Out-Null
  }
}

function Read-PackageRows($Database, [string] $Sql, [string[]] $Columns) {
  $view = $null
  try {
    $view = $Database.OpenView($Sql)
    $view.Execute()
    while ($null -ne ($record = $view.Fetch())) {
      try {
        $row = [ordered]@{}
        for ($index = 0; $index -lt $Columns.Length; $index++) {
          $row[$Columns[$index]] = $record.StringData($index + 1)
        }
        [pscustomobject] $row
      } finally { Release-ComObject $record }
    }
  } finally {
    if ($null -ne $view) { try { $view.Close() } finally { Release-ComObject $view } }
  }
}

function Read-PackageDatabase([string] $Path) {
  $installer = $null
  $database = $null
  try {
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $database = $installer.OpenDatabase((Resolve-Path -LiteralPath $Path).Path, 0) # Read-only.
    $tableNames = @(Read-PackageRows $database 'SELECT `Name` FROM `_Tables`' @('Name') | ForEach-Object { $_.Name })
    $data = [ordered]@{ Path = (Resolve-Path -LiteralPath $Path).Path; Properties = @{}; Tables = @{} }
    foreach ($row in Read-PackageRows $database 'SELECT `Property`, `Value` FROM `Property`' @('Property', 'Value')) {
      $data.Properties[$row.Property] = $row.Value
    }
    $columnsByTable = [ordered]@{
      File = @('File', 'Component_', 'FileName', 'FileSize')
      Component = @('Component', 'ComponentId', 'Directory_', 'Attributes', 'KeyPath')
      Registry = @('Registry', 'Root', 'Key', 'Name', 'Value', 'Component_')
      Shortcut = @('Shortcut', 'Directory_', 'Name', 'Component_', 'Target', 'Arguments', 'WkDir')
      Control = @('Dialog_', 'Control', 'Type', 'Text')
      CustomAction = @('Action', 'Type', 'Source', 'Target')
      Upgrade = @('UpgradeCode', 'VersionMin', 'VersionMax', 'Attributes', 'ActionProperty')
      LaunchCondition = @('Condition', 'Description')
      ServiceInstall = @('ServiceInstall', 'Name')
      ServiceControl = @('ServiceControl', 'Name')
      Environment = @('Environment', 'Name', 'Value')
      LockPermissions = @('LockObject', 'Table', 'Permission')
      MsiLockPermissionsEx = @('MsiLockPermissionsEx', 'LockObject', 'SDDLText')
    }
    foreach ($name in $columnsByTable.Keys) {
      $data.Tables[$name] = @()
      if ($tableNames -ccontains $name) {
        $columns = $columnsByTable[$name]
        $quoted = @($columns | ForEach-Object { '`' + $_ + '`' }) -join ', '
        $data.Tables[$name] = @(Read-PackageRows $database ('SELECT ' + $quoted + ' FROM `' + $name + '`') $columns)
      }
    }
    return [pscustomobject] $data
  } finally {
    Release-ComObject $database
    Release-ComObject $installer
  }
}

function Assert-NoExtraAgreementOrSystemActions($Package) {
  # The reviewed v0.6.1 official UI contains only shortcut/install/error dialogs.
  # Stop rather than silently bypass a newly introduced explicit legal acceptance.
  foreach ($row in $Package.Tables.Control) {
    Assert-PackageCheck (($row.Dialog_ + ' ' + $row.Control + ' ' + $row.Text) -notmatch
      '(?i)eula|license.?agreement|licence.?agreement|terms (and|&) conditions|I (accept|agree)|accept.*(license|licence|terms)') 'Unexpected agreement UI: pause for review and approval before installation.'
  }
  foreach ($row in $Package.Tables.CustomAction) {
    Assert-PackageCheck (([int] $row.Type -band 63) -eq 51) "Unreviewed custom action $($row.Action): only property setters are approved for this test."
  }
  foreach ($table in 'ServiceInstall', 'ServiceControl', 'Environment', 'LockPermissions', 'MsiLockPermissionsEx') {
    Assert-PackageCheck ($Package.Tables[$table].Count -eq 0) "Unexpected system/permission changes in MSI table $table; pause for review."
  }
}

function Get-RelatedProductCodes([string] $UpgradeCode) {
  $installer = $null
  $list = $null
  try {
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $list = $installer.RelatedProducts($UpgradeCode)
    foreach ($code in $list) { [string] $code }
  } finally { Release-ComObject $list; Release-ComObject $installer }
}

function Test-ExactProductPresent([string] $ProductCode) {
  $installer = $null
  try {
    $installer = New-Object -ComObject WindowsInstaller.Installer
    # ProductState reads installer registration; unlike Win32_Product it does not repair anything.
    return ([int] $installer.ProductState($ProductCode) -gt 0)
  } finally { Release-ComObject $installer }
}

function Invoke-CheckedProcess {
  param(
    [Parameter(Mandatory)] [string] $File,
    [Parameter(Mandatory)] [string[]] $Arguments,
    [Parameter(Mandatory)] [string] $WorkingDirectory,
    [Parameter(Mandatory)] [string] $LogPrefix,
    [int] $TimeoutSeconds = 120,
    [int[]] $AllowedExitCodes = @(0),
    [switch] $SystemPathOnly
  )
  $start = [Diagnostics.ProcessStartInfo]::new()
  $start.FileName = $File
  $start.UseShellExecute = $false
  $start.RedirectStandardOutput = $true
  $start.RedirectStandardError = $true
  $start.WorkingDirectory = $WorkingDirectory
  foreach ($argument in $Arguments) { $start.ArgumentList.Add($argument) }
  if ($SystemPathOnly) {
    $start.Environment['PATH'] = [Environment]::SystemDirectory + ';' + $env:WINDIR
    $start.Environment['GRIDCRAFT_NO_PREFS'] = '1'
  }
  $process = [Diagnostics.Process]::new()
  $process.StartInfo = $start
  $watch = [Diagnostics.Stopwatch]::StartNew()
  $stdoutTask = $null
  $stderrTask = $null
  try {
    Assert-PackageCheck ($process.Start()) "Could not start $File"
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
      # Kill only the exact process tree started here, never all msiexec/app processes.
      $process.Kill($true)
      $process.WaitForExit(10000) | Out-Null
      throw "Timed out after $TimeoutSeconds seconds: $File. Logs: $LogPrefix"
    }
    # A child retaining an inherited pipe must not defeat the overall operation timeout.
    $remaining = [Math]::Max(0, $TimeoutSeconds * 1000 - [int] $watch.ElapsedMilliseconds)
    Assert-PackageCheck ([Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]] @($stdoutTask, $stderrTask), $remaining)) "Timed out draining process output: $File. Logs: $LogPrefix"
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    [IO.File]::WriteAllText($LogPrefix + '.stdout.log', $stdout)
    [IO.File]::WriteAllText($LogPrefix + '.stderr.log', $stderr)
    Assert-PackageCheck ($AllowedExitCodes -contains $process.ExitCode) "Exit $($process.ExitCode): $File. Logs: $LogPrefix"
    return [pscustomobject]@{ ExitCode = $process.ExitCode; Stdout = $stdout; Stderr = $stderr }
  } finally {
    # Preserve partial diagnostics on timeout without waiting forever for a detached server process.
    if ($null -ne $stdoutTask -and $stdoutTask.IsCompletedSuccessfully) {
      [IO.File]::WriteAllText($LogPrefix + '.stdout.log', $stdoutTask.Result)
    }
    if ($null -ne $stderrTask -and $stderrTask.IsCompletedSuccessfully) {
      [IO.File]::WriteAllText($LogPrefix + '.stderr.log', $stderrTask.Result)
    }
    $watch.Stop()
    $process.Dispose()
  }
}

function Get-DirectoryHashes([string] $Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return }
  $root = [IO.Path]::GetFullPath($Path).TrimEnd('\') + '\'
  foreach ($file in Get-ChildItem -LiteralPath $Path -Recurse -File -Force | Sort-Object FullName) {
    [pscustomobject][ordered]@{ Name = $file.FullName.Substring($root.Length).Replace('\', '/'); Sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
  }
}

function Get-RegistryValueState([string] $Path, [string] $Name) {
  if (-not (Test-Path -LiteralPath $Path)) { return [pscustomobject]@{ Exists = $false; Kind = $null; Value = $null } }
  $key = Get-Item -LiteralPath $Path
  try {
    if ($key.GetValueNames() -notcontains $Name) { return [pscustomobject]@{ Exists = $false; Kind = $null; Value = $null } }
    $value = $key.GetValue($Name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
    if ($value -is [byte[]]) { $value = [Convert]::ToBase64String($value) }
    return [pscustomobject][ordered]@{ Exists = $true; Kind = $key.GetValueKind($Name).ToString(); Value = $value }
  } finally { $key.Close() }
}

function Get-RegistryTreeState([string] $Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return }
  $paths = @($Path) + @(Get-ChildItem -LiteralPath $Path -Recurse | ForEach-Object { $_.PSPath })
  foreach ($item in $paths | Sort-Object) {
    $key = Get-Item -LiteralPath $item
    try {
      $values = @()
      foreach ($name in $key.GetValueNames() | Sort-Object) {
        $values += [pscustomobject][ordered]@{ Name = $name; State = Get-RegistryValueState $item $name }
      }
      [pscustomobject][ordered]@{ Path = $key.Name; Values = $values }
    } finally { $key.Close() }
  }
}

function Get-AssociationDefaultsState {
  foreach ($extension in '.xlsx', '.xlsm', '.xlsb', '.ods', '.csv', '.tsv') {
    [pscustomobject][ordered]@{
      Extension = $extension
      MachineDefault = Get-RegistryValueState "HKLM:\Software\Classes\$extension" ''
      UserDefault = Get-RegistryValueState "HKCU:\Software\Classes\$extension" ''
      UserChoice = @(Get-RegistryTreeState "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\$extension\UserChoice")
    }
  }
}

function ConvertTo-StableJson($Value) { return ConvertTo-Json -InputObject $Value -Depth 30 -Compress }

function Assert-StateEqual($Expected, $Actual, [string] $Message) {
  Assert-PackageCheck ((ConvertTo-StableJson $Expected) -ceq (ConvertTo-StableJson $Actual)) $Message
}

function Get-ShortcutState([string] $Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return [pscustomobject]@{ Exists = $false; Sha256 = $null } }
  return [pscustomobject]@{ Exists = $true; Sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
}

function Assert-AdvertisedShortcut([string] $Path, [string] $ProductCode, [string] $Executable) {
  Assert-PackageCheck (Test-Path -LiteralPath $Path -PathType Leaf) "Start Menu shortcut is missing: $Path"
  $installer = $null
  $record = $null
  try {
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $record = $installer.ShortcutTarget($Path)
    Assert-PackageCheck ($record.StringData(1) -ieq $ProductCode) 'Start Menu shortcut advertises another product.'
    Assert-PackageCheck ($record.StringData(2) -ceq 'Main') 'Start Menu shortcut advertises another feature.'
    $target = $installer.ComponentPath($ProductCode, $record.StringData(3))
    Assert-PackageCheck ($target -ieq $Executable) 'Start Menu shortcut targets another executable.'
  } finally { Release-ComObject $record; Release-ComObject $installer }
}

function Assert-PlainShortcut([string] $Path, [string] $Executable, [string] $WorkingDirectory, [string] $ExpectedIcon = '') {
  $shell = $null
  $link = $null
  try {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path) # Inspect only; never Save or activate it.
    Assert-PackageCheck ($link.TargetPath -ieq $Executable) 'Desktop shortcut targets another executable.'
    Assert-PackageCheck ($link.WorkingDirectory.TrimEnd('\') -ieq $WorkingDirectory.TrimEnd('\')) 'Desktop shortcut has the wrong working directory.'
    if ($ExpectedIcon) {
      $iconMatch = [regex]::Match($link.IconLocation, '^(.*),\s*0$')
      Assert-PackageCheck ($iconMatch.Success) 'Desktop shortcut must use icon index zero.'
      $iconPath = [Environment]::ExpandEnvironmentVariables($iconMatch.Groups[1].Value.Trim('"'))
      Assert-PackageCheck (Test-Path -LiteralPath $iconPath -PathType Leaf) 'Desktop shortcut icon is missing.'
      Assert-PackageCheck ((Get-FileHash -LiteralPath $iconPath).Hash -ceq (Get-FileHash -LiteralPath $ExpectedIcon).Hash) 'Desktop shortcut icon differs from the independent Next source icon.'
    }
  } finally { Release-ComObject $link; Release-ComObject $shell }
}

function Get-PeFacts([string] $Path) {
  $bytes = [IO.File]::ReadAllBytes($Path)
  Assert-PackageCheck ($bytes.Length -ge 256 -and $bytes[0] -eq 77 -and $bytes[1] -eq 90) "Invalid MZ executable: $Path"
  $pe = [BitConverter]::ToInt32($bytes, 0x3c)
  Assert-PackageCheck ($pe -ge 64 -and $pe + 264 -lt $bytes.Length) 'PE header is outside the file.'
  Assert-PackageCheck ([BitConverter]::ToUInt32($bytes, $pe) -eq 0x4550) 'Invalid PE signature.'
  $optional = $pe + 24
  Assert-PackageCheck ([BitConverter]::ToUInt16($bytes, $optional) -eq 0x20b) 'Expected a PE32+ executable.'
  $sectionCount = [BitConverter]::ToUInt16($bytes, $pe + 6)
  $sectionTable = $optional + [BitConverter]::ToUInt16($bytes, $pe + 20)
  $sections = @()
  for ($index = 0; $index -lt $sectionCount; $index++) {
    $offset = $sectionTable + 40 * $index
    Assert-PackageCheck ($offset + 40 -le $bytes.Length) 'PE section header is outside the file.'
    $sections += [pscustomobject]@{
      Rva = [BitConverter]::ToUInt32($bytes, $offset + 12)
      Size = [Math]::Max([BitConverter]::ToUInt32($bytes, $offset + 8), [BitConverter]::ToUInt32($bytes, $offset + 16))
      Raw = [BitConverter]::ToUInt32($bytes, $offset + 20)
    }
  }
  $rvaOffset = {
    param([long] $Rva)
    foreach ($section in $sections) {
      if ($Rva -ge $section.Rva -and $Rva -lt [long] $section.Rva + $section.Size) {
        $offset = $Rva - $section.Rva + $section.Raw
        Assert-PackageCheck ($offset -ge 0 -and $offset -lt $bytes.Length) 'PE RVA resolves outside the file.'
        return [int] $offset
      }
    }
    throw "PE RVA $Rva does not map to a section."
  }
  $imports = @()
  $importRva = [BitConverter]::ToUInt32($bytes, $optional + 120)
  if ($importRva -ne 0) {
    $descriptor = & $rvaOffset $importRva
    for ($index = 0; $index -lt 512; $index++) {
      $offset = $descriptor + 20 * $index
      Assert-PackageCheck ($offset + 20 -le $bytes.Length) 'PE import descriptor is outside the file.'
      $nameRva = [BitConverter]::ToUInt32($bytes, $offset + 12)
      if ($nameRva -eq 0) { break }
      $nameOffset = & $rvaOffset $nameRva
      $end = $nameOffset
      while ($end -lt $bytes.Length -and $end - $nameOffset -lt 260 -and $bytes[$end] -ne 0) { $end++ }
      Assert-PackageCheck ($end -lt $bytes.Length -and $bytes[$end] -eq 0) 'PE import name is not terminated.'
      $imports += [Text.Encoding]::ASCII.GetString($bytes, $nameOffset, $end - $nameOffset)
      Assert-PackageCheck ($index -lt 511) 'Unexpected excessive PE import list.'
    }
  }
  return [pscustomobject]@{
    Machine = [BitConverter]::ToUInt16($bytes, $pe + 4)
    Subsystem = [BitConverter]::ToUInt16($bytes, $optional + 68)
    Imports = @($imports | Sort-Object -Unique)
    Version = [Diagnostics.FileVersionInfo]::GetVersionInfo($Path)
  }
}
