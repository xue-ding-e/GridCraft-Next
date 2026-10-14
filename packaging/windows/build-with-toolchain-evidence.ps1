<# Build the real Rust package once and collect separate, runner-local toolchain evidence.
Only the package outcome controls this build step. Evidence incompleteness is emitted as a
separate output and MUST fail the later evidence/aggregate gate; it never authorizes release.
This separation lets actual application/install checks run even when notice research remains.
#>
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$selection = Join-Path $env:RUNNER_TEMP 'gridcraft-toolchain-selection'
$capture = Join-Path $env:RUNNER_TEMP 'gridcraft-link-capture'
$collection = Join-Path $env:RUNNER_TEMP 'gridcraft-toolchain-collection'
& (Join-Path $PSScriptRoot 'select-installed-msvc-sdk.ps1') -OutputDirectory $selection -ApplyToProcess
& (Join-Path $PSScriptRoot 'enable-msvc-link-capture.ps1') -OutputDirectory $capture
$packageFailure = $null
$captureComplete = $false
try {
  & (Join-Path $PSScriptRoot 'package.ps1') -Arch x64 -ValidationOnly
} catch {
  $packageFailure = $_
} finally {
  try {
    & python (Join-Path $PSScriptRoot 'summarize-msvc-link-inputs.py') $capture `
      --gui 'target/x86_64-pc-windows-msvc/release/gridcraft.exe' `
      --cli 'target/x86_64-pc-windows-msvc/release/gridcraft-cli.exe'
    $captureStatus = $LASTEXITCODE
    $inputs = Get-Content -LiteralPath (Join-Path $capture 'private/collector-inputs.json') -Raw | ConvertFrom-Json
    & (Join-Path $PSScriptRoot 'collect-installed-toolchain.ps1') -OutputDirectory $collection `
      -RustToolchain '1.99.0-x86_64-pc-windows-msvc' `
      -LinkedRuntimeLibraries @($inputs.linked_runtime_libraries) `
      -LinkEvidenceFiles @($inputs.link_evidence_files)
    $summary = Get-Content -LiteralPath (Join-Path $collection 'public/toolchain-summary.json') -Raw | ConvertFrom-Json
    $technicalIssues = @($summary.unresolved | Where-Object { $_ -ne 'MICROSOFT_TERMS_APPLICABILITY_NOT_REVIEWED' })
    # A human review of the exact collected legal texts is still outstanding. Every other
    # collection problem, missing original, identity mismatch or unresolved link stays red.
    $captureComplete = ($captureStatus -eq 0 -and @($summary.missing_files).Count -eq 0 -and $technicalIssues.Count -eq 0)
  } catch {
    Write-Warning 'Toolchain evidence is incomplete. Its separate gate will fail; details stay runner-local.'
  }
  if ($env:GITHUB_OUTPUT) {
    ('toolchain_capture_complete=' + $captureComplete.ToString().ToLowerInvariant()) |
      Out-File -LiteralPath $env:GITHUB_OUTPUT -Encoding utf8 -Append
  }
}
if ($null -ne $packageFailure) { throw $packageFailure }
# This status belongs only to the real package build. The workflow independently requires
# toolchain_capture_complete and the public evidence validator; neither is skipped or greened.
$global:LASTEXITCODE = 0
