<#
Collect actual final-link and installed-toolchain evidence once per hosted build.
package.ps1 calls this before writing the MSI/ZIP; the outer build wrapper calls
it in finally for failed-build diagnostics. A source/binary-bound local receipt
prevents the second call from recreating the collector's new-only output directory.
Neither complete capture nor a receipt approves redistribution.
#>
param(
  [Parameter(Mandatory)][string] $BinaryDirectory,
  [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{40}$')][string] $SourceSha
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$result = [ordered]@{ schema = 1; source_sha = $SourceSha; capture_complete = $false; gui_sha256 = $null; cli_sha256 = $null }
if (-not $IsWindows -or $env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or -not $env:RUNNER_TEMP) {
  return [pscustomobject] $result
}
$capture = Join-Path $env:RUNNER_TEMP 'gridcraft-link-capture'
$collection = Join-Path $env:RUNNER_TEMP 'gridcraft-toolchain-collection'
$state = Join-Path $env:RUNNER_TEMP 'gridcraft-toolchain-collection-state'
$receipt = Join-Path $state 'collection-result.json'
$gui = Join-Path $BinaryDirectory 'gridcraft.exe'
$cli = Join-Path $BinaryDirectory 'gridcraft-cli.exe'
foreach ($pair in @(@('gui_sha256', $gui), @('cli_sha256', $cli))) {
  if (Test-Path -LiteralPath $pair[1] -PathType Leaf) {
    $result[$pair[0]] = (Get-FileHash -LiteralPath $pair[1] -Algorithm SHA256).Hash.ToLowerInvariant()
  }
}
if (Test-Path -LiteralPath $state) {
  # An interrupted first attempt is incomplete, not permission to reuse stale
  # evidence or retry into existing collector directories.
  try {
    $saved = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
    if ($saved.schema -eq 1 -and $saved.source_sha -ceq $SourceSha -and
        $saved.gui_sha256 -ceq $result.gui_sha256 -and $saved.cli_sha256 -ceq $result.cli_sha256 -and
        $saved.capture_complete -is [bool] -and $saved.capture_complete -eq $true) {
      $result.capture_complete = $true
    }
  } catch { Write-Warning 'Toolchain collection receipt is incomplete; the evidence gate will remain red.' }
  return [pscustomobject] $result
}
New-Item -ItemType Directory -Path $state | Out-Null
try {
  & python (Join-Path $PSScriptRoot 'summarize-msvc-link-inputs.py') $capture --gui $gui --cli $cli | Out-Host
  $captureStatus = $LASTEXITCODE
  $inputs = Get-Content -LiteralPath (Join-Path $capture 'private/collector-inputs.json') -Raw | ConvertFrom-Json
  & (Join-Path $PSScriptRoot 'collect-installed-toolchain.ps1') -OutputDirectory $collection `
    -RustToolchain '1.99.0-x86_64-pc-windows-msvc' `
    -LinkedRuntimeLibraries @($inputs.linked_runtime_libraries) `
    -LinkEvidenceFiles @($inputs.link_evidence_files) | Out-Host
  $summary = Get-Content -LiteralPath (Join-Path $collection 'public/toolchain-summary.json') -Raw | ConvertFrom-Json
  $technicalIssues = @($summary.unresolved | Where-Object { $_ -ne 'MICROSOFT_TERMS_APPLICABILITY_NOT_REVIEWED' })
  # A human review of the exact collected legal texts is still outstanding.
  # Every other missing original, identity mismatch or unresolved link stays red.
  $result.capture_complete = ($captureStatus -eq 0 -and @($summary.missing_files).Count -eq 0 -and $technicalIssues.Count -eq 0)
} catch {
  Write-Warning 'Toolchain evidence is incomplete. Its separate gate will fail; details stay runner-local.'
} finally {
  $result | ConvertTo-Json | Set-Content -LiteralPath $receipt -Encoding utf8NoBOM
}
return [pscustomobject] $result
