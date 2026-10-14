# Modified for GridCraft-Next: independent packaging identity and notices.
# Stage the same legal payload for the MSI and portable archive. Source texts are copied verbatim.
function Copy-PackageLegalFiles([string] $Root, [string] $Destination) {
  New-Item -ItemType Directory -Force -Path $Destination | Out-Null
  foreach ($name in 'LICENSE-MIT', 'LICENSE-APACHE', 'NOTICE', 'ATTRIBUTION.md') {
    $source = Join-Path $Root $name
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Required legal file is missing: $source" }
    Copy-Item -LiteralPath $source -Destination $Destination
  }
  Copy-Item -LiteralPath (Join-Path $Root 'assets\app-icon\LICENSE.txt') -Destination (Join-Path $Destination 'app-icon-LICENSE.txt')
  Copy-Item -LiteralPath (Join-Path $Root 'docs\brand\LICENSE-brand.txt') -Destination (Join-Path $Destination 'LICENSE-brand.txt')
  foreach ($name in 'README.md', 'LICENSE', 'COPYRIGHT') {
    $source = Join-Path $Root $name
    if (Test-Path -LiteralPath $source -PathType Leaf) { Copy-Item -LiteralPath $source -Destination $Destination }
  }
  # Builds made with craft-fonts embed its fonts, so retain each family's own licence.
  if ($env:CRAFT_FONTS_DIR) {
    foreach ($ofl in Get-ChildItem -Path (Join-Path $env:CRAFT_FONTS_DIR 'fonts\*\OFL.txt') -ErrorAction SilentlyContinue) {
      Copy-Item -LiteralPath $ofl.FullName -Destination (Join-Path $Destination "OFL-$($ofl.Directory.Name).txt")
    }
  }
}

# Explicit File entries install every staged notice, including optional OFL files. WiX derives
# stable component GUIDs from each file's independent installed path (one file keypath per component).
function Write-PackageLegalManifest([string] $Directory, [string] $OutputPath) {
  $settings = [System.Xml.XmlWriterSettings]::new()
  $settings.Indent = $true
  $writer = [System.Xml.XmlWriter]::Create($OutputPath, $settings)
  $hash = [System.Security.Cryptography.SHA256]::Create()
  try {
    $writer.WriteStartDocument()
    $writer.WriteStartElement('Wix', 'http://wixtoolset.org/schemas/v4/wxs')
    $writer.WriteStartElement('Fragment', 'http://wixtoolset.org/schemas/v4/wxs')
    $writer.WriteStartElement('ComponentGroup', 'http://wixtoolset.org/schemas/v4/wxs')
    $writer.WriteAttributeString('Id', 'GridcraftLegalFiles')
    $writer.WriteAttributeString('Directory', 'LICENSEFOLDER')
    foreach ($file in Get-ChildItem -LiteralPath $Directory -File | Sort-Object Name) {
      $digest = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($file.Name))).Replace('-', '')
      $id = 'Legal_' + $digest.Substring(0, 32)
      $writer.WriteStartElement('Component', 'http://wixtoolset.org/schemas/v4/wxs')
      $writer.WriteAttributeString('Id', $id)
      $writer.WriteAttributeString('Guid', '*')
      $writer.WriteStartElement('File', 'http://wixtoolset.org/schemas/v4/wxs')
      $writer.WriteAttributeString('Id', $id + '_File')
      $writer.WriteAttributeString('Source', $file.FullName)
      $writer.WriteAttributeString('Name', $file.Name)
      $writer.WriteAttributeString('KeyPath', 'yes')
      $writer.WriteEndElement()
      $writer.WriteEndElement()
    }
    $writer.WriteEndElement()
    $writer.WriteEndElement()
    $writer.WriteEndElement()
    $writer.WriteEndDocument()
  } finally {
    $hash.Dispose()
    $writer.Dispose()
  }
}
