#requires -Version 7.2
<#
.SYNOPSIS
  Fetch one unchanged official GridCraft MSI solely for disposable coexistence testing.
.DESCRIPTION
  This is a fixture, never part of a Next deliverable. No install is performed here.
  v0.6.1 primary sources reviewed before adding this fixture:
  https://github.com/storytold/gridcraft/blob/v0.6.1/LICENSE-MIT
  https://github.com/storytold/gridcraft/blob/v0.6.1/docs/brand/LICENSE-brand.txt
  https://github.com/storytold/gridcraft/blob/v0.6.1/packaging/windows/gridcraft.wxs
  MIT permits use; the brand license permits unchanged official GridCraft builds.
  The reviewed WiX UI has no EULA acceptance step. test-package.ps1 also checks
  the downloaded MSI's tables before installation and stops on unexpected legal UI.
  Do not upload this fixture, modify its marks, remove MOTW, or bypass a security warning.
#>
param([Parameter(Mandatory)] [string] $Destination)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'package-test-common.ps1')
Assert-HostedWindows
$Destination = Assert-RunnerTemporaryPath $Destination
Assert-PackageCheck (-not (Test-Path -LiteralPath $Destination)) 'Refusing to overwrite an existing fixture.'
$parent = Split-Path -Parent $Destination
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$url = 'https://github.com/storytold/gridcraft/releases/download/v0.6.1/gridcraft-0.6.1-windows-x64.msi'
# Exact GitHub release-asset digest and size verified against the official release API.
$expectedSha256 = '984e9a85942ce55953ce2b875c4e93ddd70d47fb4bf5f07c74d1d9f9b98e56c9'
Invoke-WebRequest -Uri $url -OutFile $Destination -TimeoutSec 120 -MaximumRetryCount 0
Assert-PackageCheck ((Get-Item -LiteralPath $Destination).Length -eq 13819904) 'Official fixture size differs from the verified release asset.'
$hash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
Assert-PackageCheck ($hash -ceq $expectedSha256) 'Official fixture SHA256 differs from the verified release asset; do not install it.'
Write-Host "Verified unchanged official v0.6.1 MSI for isolated testing: $hash"
Write-Output $Destination
