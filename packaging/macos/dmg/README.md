# Archived upstream macOS DMG source

The upstream `background.svg` and disabled `generate.py` are retained only as text engineering references, with their original attribution in `ATTRIBUTION.md`. They describe an upstream GridCraft window and do not establish a GridCraft-Next layout.

The unused binary `background.tiff` and `dmg-layout.DS_Store` are excluded from the independent source tree. GridCraft-Next does not copy a background or `.DS_Store` into release images. Its DMG uses Finder's default layout, a `GridCraft-Next.app` item, and the `Applications` link.

A future custom layout must be regenerated for the independent app and volume name with independently reviewed artwork. Do not rename or fabricate the old Finder metadata, and do not enable the archived generator as a release step without that review. No GridCraft-Next DMG installation or rendering validation is claimed.

The upstream text source originated in the GridCraft macOS DMG work described by `storytold/vectorcraft#493`. Original author and license information for the retained SVG remains in `ATTRIBUTION.md`.
