# Real Rust GUI smoke wrapper. Requires disposable GitHub windows-2022, 64-bit Python.
# See native_gui_smoke.py --help. No installs, graphics overrides, or security bypasses.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$GuiExe,
    [Parameter(Mandatory)][string]$CliExe,
    [Parameter(Mandatory)][string]$OutputDir,
    [Parameter(Mandatory)][string]$ExpectedVersion,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$SourceSha,
    [Parameter(Mandatory)][ValidateSet('installed', 'portable')][string]$Mode,
    [ValidateRange(10, 180)][int]$TimeoutSeconds = 90
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$python = (Get-Command python -ErrorAction Stop).Source
& $python (Join-Path $PSScriptRoot 'native_gui_smoke.py') `
    --gui-exe $GuiExe --cli-exe $CliExe --output-dir $OutputDir `
    --expected-version $ExpectedVersion --source-sha $SourceSha --mode $Mode --timeout $TimeoutSeconds
if ($LASTEXITCODE -ne 0) {
    throw "Real native GUI smoke failed ($LASTEXITCODE). Inspect $OutputDir/evidence.json and retained logs/PNGs."
}
