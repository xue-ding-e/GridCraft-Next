# Asset attribution

Modified for GridCraft-Next on 2026-10-14: restricted artwork removed; original asset authors and licenses retained; Linux icon paths isolated; inherited marketing screenshots and unused DMG binary resources excluded.

Every non-code asset in this repository (images, icons, fonts, example files, presets) is listed here with its author, source and licence. `cargo xtask assets` (part of `cargo xtask ci`) fails if an asset file is missing from this table.

**Policy (mandatory):** GridCraft contains **no Microsoft, Adobe, Avid or Autodesk iconography, images, artwork, fonts, templates, themes, table styles or sample files**. Every asset is original work by GridCraft contributors or third-party material under an open licence (OSI open source, public domain / CC0, or Creative Commons that allows redistribution). Screenshots of Microsoft software are never committed. Font files are not added here: shared fonts live in [storytold/craft-fonts](https://github.com/storytold/craft-fonts), an optional build input (`CRAFT_FONTS_DIR`). The upstream ArtCraft trademark artwork has been removed from this independent distribution. Its historical attribution and `docs/brand/LICENSE-brand.txt` are retained as provenance, not as permission to use those marks.

Generated-in-code art is original and has no file to list: the UI icon set (`crates/ui-egui/src/icons.rs`), themes (`crates/engine/src/cmd/view.rs`), cell styles (`crates/engine/src/cmd/format.rs`), table styles (`crates/engine/src/tables.rs`), chart palettes and the sample workbooks (`crates/engine/src/sample.rs`).

| Asset | Author | Source | Licence | Notes |
|---|---|---|---|---|
| `docs/brand/artcraft-logo.svg` | ArtCraft Team | craftrules `assets/brand/` | ArtCraft brand terms (`docs/brand/LICENSE-brand.txt`) | Trademark, not open source; upstream asset removed from GridCraft-Next |
| `docs/brand/artcraft-logo.png` | ArtCraft Team | craftrules `assets/brand/` | ArtCraft brand terms | Trademark; upstream asset removed from GridCraft-Next |
| `docs/brand/artcraft-logo-white.svg` | ArtCraft Team | craftrules `assets/brand/` | ArtCraft brand terms | Trademark; upstream asset removed from GridCraft-Next |
| `docs/brand/artcraft-logo-white.png` | ArtCraft Team | craftrules `assets/brand/` | ArtCraft brand terms | Trademark; upstream asset removed from GridCraft-Next |
| `docs/brand/artcraft-mark.svg` | ArtCraft Team | craftrules `assets/brand/` | ArtCraft brand terms | Trademark; upstream asset removed from GridCraft-Next |
| `docs/brand/artcraft-mark.png` | ArtCraft Team | craftrules `assets/brand/` | ArtCraft brand terms | Trademark; upstream asset removed from GridCraft-Next |
| `docs/brand/artcraft-mark-black.svg` | ArtCraft Team | craftrules `assets/brand/` | ArtCraft brand terms | Trademark; upstream asset removed from GridCraft-Next |
| `docs/brand/artcraft-mark-black.png` | ArtCraft Team | craftrules `assets/brand/` | ArtCraft brand terms | Trademark; upstream asset removed from GridCraft-Next |
| `docs/brand/LICENSE-brand.txt` | (licence text) | craftrules | — | |
| `assets/app-icon/gridcraft.svg` | GridCraft contributors | original (hand-written SVG; `packaging/icons.sh`) | MIT OR Apache-2.0 (`assets/app-icon/LICENSE.txt`) | PLACEHOLDER app icon (ledger page); replaced by the owner's creature art later |
| `packaging/macos/dmg/background.svg` | @XusBadia | original (hand-written SVG, derived from the GridCraft app icon `assets/app-icon/gridcraft.svg`, referenced via `<image>`, not copied; text is outlined glyph paths; `packaging/macos/dmg/generate.py` renders it to `background.tiff`) | MIT OR Apache-2.0 (`LICENSE-MIT`, `LICENSE-APACHE`) | Upstream GridCraft macOS DMG background; retained for attribution, not used by GridCraft-Next packaging; added 2026-10-08 |
| `assets/app-icon/gridcraft-1024.png` | GridCraft contributors | original (hand-written SVG; `packaging/icons.sh`) | MIT OR Apache-2.0 (`assets/app-icon/LICENSE.txt`) | Placeholder icon, rendered from gridcraft.svg |
| `assets/app-icon/gridcraft-macos-512.png` | GridCraft contributors | original (hand-written SVG; `packaging/icons.sh`) | MIT OR Apache-2.0 (`assets/app-icon/LICENSE.txt`) | Placeholder icon, macOS grid render |
| `assets/app-icon/gridcraft.icns` | GridCraft contributors | original (hand-written SVG; `packaging/icons.sh`) | MIT OR Apache-2.0 (`assets/app-icon/LICENSE.txt`) | Placeholder icon, macOS bundle icon |
| `assets/app-icon/gridcraft.ico` | GridCraft contributors | original (hand-written SVG; `packaging/icons.sh`) | MIT OR Apache-2.0 (`assets/app-icon/LICENSE.txt`) | Placeholder icon, Windows exe icon |
| `assets/app-icon/hicolor/16x16/apps/io.github.xue_ding_e.gridcraft_next.png` | GridCraft contributors | original (hand-written SVG; `packaging/icons.sh`) | MIT OR Apache-2.0 (`assets/app-icon/LICENSE.txt`) | Placeholder icon, Linux hicolor render |
| `assets/app-icon/hicolor/24x24/apps/io.github.xue_ding_e.gridcraft_next.png` | GridCraft contributors | original (hand-written SVG; `packaging/icons.sh`) | MIT OR Apache-2.0 (`assets/app-icon/LICENSE.txt`) | Placeholder icon, Linux hicolor render |
| `assets/app-icon/hicolor/32x32/apps/io.github.xue_ding_e.gridcraft_next.png` | GridCraft contributors | original (hand-written SVG; `packaging/icons.sh`) | MIT OR Apache-2.0 (`assets/app-icon/LICENSE.txt`) | Placeholder icon, Linux hicolor render |
| `assets/app-icon/hicolor/48x48/apps/io.github.xue_ding_e.gridcraft_next.png` | GridCraft contributors | original (hand-written SVG; `packaging/icons.sh`) | MIT OR Apache-2.0 (`assets/app-icon/LICENSE.txt`) | Placeholder icon, Linux hicolor render |
| `assets/app-icon/hicolor/64x64/apps/io.github.xue_ding_e.gridcraft_next.png` | GridCraft contributors | original (hand-written SVG; `packaging/icons.sh`) | MIT OR Apache-2.0 (`assets/app-icon/LICENSE.txt`) | Placeholder icon, Linux hicolor render |
| `assets/app-icon/hicolor/128x128/apps/io.github.xue_ding_e.gridcraft_next.png` | GridCraft contributors | original (hand-written SVG; `packaging/icons.sh`) | MIT OR Apache-2.0 (`assets/app-icon/LICENSE.txt`) | Placeholder icon, Linux hicolor render |
| `assets/app-icon/hicolor/256x256/apps/io.github.xue_ding_e.gridcraft_next.png` | GridCraft contributors | original (hand-written SVG; `packaging/icons.sh`) | MIT OR Apache-2.0 (`assets/app-icon/LICENSE.txt`) | Placeholder icon, Linux hicolor render |
| `assets/app-icon/hicolor/512x512/apps/io.github.xue_ding_e.gridcraft_next.png` | GridCraft contributors | original (hand-written SVG; `packaging/icons.sh`) | MIT OR Apache-2.0 (`assets/app-icon/LICENSE.txt`) | Placeholder icon, Linux hicolor render |
| `assets/app-icon/hicolor/scalable/apps/io.github.xue_ding_e.gridcraft_next.svg` | GridCraft contributors | original (hand-written SVG; `packaging/icons.sh`) | MIT OR Apache-2.0 (`assets/app-icon/LICENSE.txt`) | Placeholder icon, copy of gridcraft.svg |
| `assets/app-icon/README.md` | GridCraft contributors | original | MIT OR Apache-2.0 | Icon documentation |
| `assets/app-icon/LICENSE.txt` | (licence text) | original | — | Licence of the app icon files |

## Source-snapshot exclusions

The inherited `docs/images/` marketing screenshots are not included pending independent provenance review. This is not a finding that their upstream license claims are invalid. Future screenshots should be rendered from GridCraft-Next with synthetic workbooks and documented here.

The unused upstream DMG binary background and Finder layout metadata are also excluded. Their text source/reference remains under `packaging/macos/dmg/`; GridCraft-Next packaging uses Finder's default layout.
