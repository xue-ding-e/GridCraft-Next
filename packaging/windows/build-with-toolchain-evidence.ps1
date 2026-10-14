<# Build the real Rust package once and collect separate, runner-local toolchain evidence.
Only the package outcome controls this build step. Evidence incompleteness is emitted as a
separate output and MUST fail the later evidence/aggregate gate; it never authorizes release.
This separation lets actual application/install checks run even when notice research remains.
#>
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$selection = Join-Path $env:RUNNER_TEMP 'gridcraft-toolchain-selection'
$capture = Join-Path $env:RUNNER_TEMP 'gridcraft-link-capture'
& (Join-Path $PSScriptRoot 'select-installed-msvc-sdk.ps1') -OutputDirectory $selection -ApplyToProcess
& (Join-Path $PSScriptRoot 'enable-msvc-link-capture.ps1') -OutputDirectory $capture
$packageFailure = $null
$captureComplete = $false
try {
  & (Join-Path $PSScriptRoot 'package.ps1') -Arch x64 -ReviewedOrValidation
} catch {
  $packageFailure = $_
} finally {
  try {
    $targetRoot = if ($env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR } else { Join-Path $PSScriptRoot '../../target' }
    $collected = & (Join-Path $PSScriptRoot 'collect-build-toolchain-evidence.ps1') `
      -BinaryDirectory (Join-Path $targetRoot 'x86_64-pc-windows-msvc/release') -SourceSha $env:GRIDCRAFT_BUILD_SHA
    # The helper preserves the existing collection checks and never calls the
    # new-directory-only collector twice. Its receipt is not legal approval.
    $captureComplete = ($collected.capture_complete -is [bool] -and $collected.capture_complete -eq $true)
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
