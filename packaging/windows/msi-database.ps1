# Shared read-only MSI row reader. Dot-sourcing defines helpers only.
# PowerShell functions return every unsuppressed success-stream value. COM lifecycle
# calls must never escape into a sequence that callers treat as database row objects.
function Get-GridCraftMsiRecordProperty {
  param(
    [Parameter(Mandatory)] $Record,
    [Parameter(Mandatory)] [ValidateSet('FieldCount', 'StringData')] [string] $Name,
    [AllowNull()] [object[]] $Arguments = $null
  )
  if (-not [Runtime.InteropServices.Marshal]::IsComObject($Record)) {
    throw "MSI Fetch returned a non-COM record: $($Record.GetType().FullName)."
  }
  return $Record.GetType().InvokeMember($Name, [Reflection.BindingFlags]::GetProperty, $null, $Record, $Arguments)
}

function Read-GridCraftMsiRows {
  param(
    [Parameter(Mandatory)] $Database,
    [Parameter(Mandatory)] [string] $Sql,
    [Parameter(Mandatory)] [ValidateNotNullOrEmpty()] [string[]] $Columns
  )
  $view = $null
  $record = $null
  $rows = [Collections.Generic.List[object]]::new()
  try {
    $view = $Database.OpenView($Sql)
    [void] $view.Execute()
    while ($null -ne ($record = $view.Fetch())) {
      try {
        # The PowerShell COM adapter does not consistently expose these IDispatch
        # properties as ordinary members. Invoke the documented property getters
        # explicitly, including the indexed StringData property; keep the count gate.
        # https://learn.microsoft.com/windows/win32/msi/record-fieldcount
        # https://learn.microsoft.com/windows/win32/msi/record-stringdata
        $fieldCount = [int] (Get-GridCraftMsiRecordProperty -Record $record -Name FieldCount)
        if ($fieldCount -ne $Columns.Length) {
          throw "MSI row shape mismatch: query returned $fieldCount fields; $($Columns.Length) column names were supplied. SQL: $Sql"
        }
        $row = [ordered]@{}
        for ($i = 0; $i -lt $Columns.Length; $i++) {
          if ([string]::IsNullOrWhiteSpace($Columns[$i]) -or $row.Contains($Columns[$i])) {
            throw 'MSI column names must be nonempty and unique.'
          }
          $row[$Columns[$i]] = [string] (Get-GridCraftMsiRecordProperty -Record $record -Name StringData -Arguments @($i + 1))
        }
        [void] $rows.Add([pscustomobject] $row)
      } finally {
        [void] [Runtime.InteropServices.Marshal]::FinalReleaseComObject($record)
        $record = $null
      }
    }
  } finally {
    if ($null -ne $view) {
      try { [void] $view.Close() }
      finally { [void] [Runtime.InteropServices.Marshal]::FinalReleaseComObject($view) }
    }
  }
  # Emit rows only after COM cleanup, with ordinary pipeline enumeration: callers use
  # @() for zero/one/many rows. Never return a lifecycle result or a nested row array.
  return $rows.ToArray()
}
