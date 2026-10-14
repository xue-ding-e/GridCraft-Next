<# Enable same-process, final-target-only MSVC link capture. No compilation is performed here.
The existing package.ps1 cargo build remains one build for GUI + CLI. The wrapper calls
selected link.exe exactly once per invocation and saves /VERBOSE:LIB output privately. #>
param([Parameter(Mandatory)][string] $OutputDirectory)
$ErrorActionPreference='Stop'
if(-not $IsWindows -or -not $env:RUNNER_TEMP -or -not $env:VCToolsInstallDir -or -not $env:WindowsSdkDir){throw 'LINK_CAPTURE_SELECTION_REQUIRED'}
$out=[IO.Path]::GetFullPath($OutputDirectory);$temp=[IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\')+'\'
if(-not $out.StartsWith($temp,[StringComparison]::OrdinalIgnoreCase)){throw 'LINK_CAPTURE_MUST_BE_RUNNER_LOCAL'}
if(Test-Path -LiteralPath $out){throw 'LINK_CAPTURE_OUTPUT_MUST_BE_NEW'}
$linker=Join-Path $env:VCToolsInstallDir 'bin\Hostx64\x64\link.exe'
$python=(Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$script=Join-Path $PSScriptRoot 'capture-msvc-link.py'
foreach($p in @($linker,$python,$script)){if(-not(Test-Path -LiteralPath $p -PathType Leaf)){throw 'LINK_CAPTURE_INPUT_MISSING'}}
# Reject batch metacharacters instead of interpolating unexpected executable paths.
foreach($p in @($out,$python,$script)){if($p -match '["%&!^<>\r\n]'){throw 'LINK_CAPTURE_PATH_CHARACTERS_UNSUPPORTED'}}
New-Item -ItemType Directory -Path (Join-Path $out 'private'),(Join-Path $out 'public') | Out-Null
$cmd=Join-Path $out 'private\capture-link.cmd'
$lines=@('@echo off', ('"{0}" "{1}" %*' -f $python,$script), 'exit /b %errorlevel%')
[IO.File]::WriteAllLines($cmd,$lines,[Text.Encoding]::Default)
[Environment]::SetEnvironmentVariable('GRIDCRAFT_REAL_LINK_EXE',$linker,'Process')
[Environment]::SetEnvironmentVariable('GRIDCRAFT_LINK_EVIDENCE_DIR',$out,'Process')
[Environment]::SetEnvironmentVariable('CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER',$cmd,'Process')
Write-Output 'Final-target MSVC link capture enabled; linker text stays runner-local.'
