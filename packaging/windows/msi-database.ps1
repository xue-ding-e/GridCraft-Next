# Shared read-only MSI row reader. Dot-sourcing defines helpers only.
# PowerShell functions return every unsuppressed success-stream value. COM lifecycle
# calls must never escape into a sequence that callers treat as database row objects.
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
        $fieldCount = [int] $record.FieldCount
        if ($fieldCount -ne $Columns.Length) {
          throw "MSI row shape mismatch: query returned $fieldCount fields; $($Columns.Length) column names were supplied. SQL: $Sql"
        }
        $row = [ordered]@{}
        for ($i = 0; $i -lt $Columns.Length; $i++) {
          if ([string]::IsNullOrWhiteSpace($Columns[$i]) -or $row.Contains($Columns[$i])) {
            throw 'MSI column names must be nonempty and unique.'
          }
          $row[$Columns[$i]] = [string] $record.StringData($i + 1)
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
