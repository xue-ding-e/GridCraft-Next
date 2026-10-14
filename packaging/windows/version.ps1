# GridCraft-Next's single authoritative SemVer-to-MSI version policy.
# Dot-source this file from packaging and tests; it has no external side effects.
#
# MSI compares only M.m.build, with M/m <= 255 and build <= 65535:
# https://learn.microsoft.com/windows/win32/msi/productversion
# Canonical M.m.p-next.N maps to M.m.(256*p+N); stable M.m.p reserves rank 255.
# M, m and p are 0..255; N is 0..254. This finite mapping preserves ordering
# through next.9 -> next.10, next.254 -> stable, and stable -> next patch/minor/major.
# At a boundary, choose a higher representable SemVer; never wrap, clamp or reuse a rank.
# 255.255.255 is terminal for this policy. Changing the policy after distribution
# needs an explicit migration design. There were no published Next MSIs when adopted.
# Full SemVer remains the display/artifact version; only MSI ProductVersion is encoded.

function ConvertTo-GridCraftMsiVersion {
  param([AllowNull()] [AllowEmptyString()] [string] $Version)

  # ASCII digits, canonical zeroes and absolute anchors are deliberate. Limit each
  # capture before parsing so hostile, huge integers can never overflow the conversion.
  $number = '(0|[1-9][0-9]{0,2})'
  $pattern = '\A' + $number + '\.' + $number + '\.' + $number + '(?:-next\.' + $number + ')?\z'
  if ($null -eq $Version -or $Version.Length -gt 20) {
    throw 'Version must be canonical M.m.p-next.N or M.m.p (M/m/p 0..255; N 0..254).'
  }
  $match = [regex]::Match($Version, $pattern, [System.Text.RegularExpressions.RegexOptions]::CultureInvariant)
  if (-not $match.Success) {
    throw 'Version must be canonical M.m.p-next.N or M.m.p (M/m/p 0..255; N 0..254).'
  }
  $major = [int] $match.Groups[1].Value
  $minor = [int] $match.Groups[2].Value
  $patch = [int] $match.Groups[3].Value
  if ($major -gt 255 -or $minor -gt 255 -or $patch -gt 255) {
    throw 'MSI version policy requires major, minor and patch in 0..255.'
  }
  $rank = 255
  if ($match.Groups[4].Success) {
    $rank = [int] $match.Groups[4].Value
    if ($rank -gt 254) { throw 'next.N requires N in 0..254; rank 255 is reserved for stable.' }
  }
  # The validated bounds guarantee 0 <= build <= 65535, with no arithmetic overflow.
  $build = 256 * $patch + $rank
  return [string]::Format([Globalization.CultureInfo]::InvariantCulture, '{0}.{1}.{2}', $major, $minor, $build)
}

function Get-GridCraftPackageVersion {
  param(
    [Parameter(Mandatory)] [string] $ManifestPath,
    # Keep this untyped: PowerShell's [string] binding turns $null into '', losing
    # the distinction between an absent override and an explicitly empty override.
    [AllowNull()] $VersionOverride = $null
  )

  $version = $null
  $inPackage = $false
  $packageSections = 0
  foreach ($line in Get-Content -LiteralPath $ManifestPath -ErrorAction Stop) {
    if ($line -match '^\s*\[') {
      $inPackage = $line -cmatch '^\s*\[workspace\.package\]\s*(?:#.*)?$'
      if ($inPackage) { $packageSections++ }
      continue
    }
    if ($inPackage -and $line -cmatch '^\s*version\s*=') {
      # Support the repository's single-line double-quoted TOML field. Fail closed
      # on other spellings instead of guessing at TOML or falling back to an override.
      if ($null -ne $version -or $line -cnotmatch '^\s*version\s*=\s*"([^"]*)"\s*(?:#.*)?$') {
        throw 'Cargo.toml must contain exactly one single-line double-quoted [workspace.package] version.'
      }
      $version = $Matches[1]
    }
  }
  if ($packageSections -ne 1 -or $null -eq $version) {
    throw 'Could not read exactly one [workspace.package] version from Cargo.toml.'
  }
  $msiVersion = ConvertTo-GridCraftMsiVersion $version
  if ($null -ne $VersionOverride -and
      ($VersionOverride -isnot [string] -or -not [string]::Equals($VersionOverride, $version, [StringComparison]::Ordinal))) {
    throw 'GRIDCRAFT_VERSION must exactly match [workspace.package] version in Cargo.toml; change the workspace version before building.'
  }
  return [pscustomobject]@{ FullVersion = $version; MsiVersion = $msiVersion }
}
