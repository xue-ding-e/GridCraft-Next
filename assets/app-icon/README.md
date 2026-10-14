# GridCraft-Next app icon (upstream placeholder)

GridCraft-Next currently reuses the original, permissively licensed GridCraft placeholder icon.
It is a simple ledger page with a header row, a row-number column, a selected cell with its fill
handle and a double rule under a total, on a full-bleed green tile. The original GridCraft
contributors wrote its geometry by hand; it is not modelled on a vendor's spreadsheet logo.
The original authorship and MIT OR Apache-2.0 terms remain in `LICENSE.txt` and the root
`ATTRIBUTION.md`.
Reuse of this artwork does not imply upstream sponsorship or endorsement.

The source and platform binary filenames remain `gridcraft.*` for build compatibility; Linux
hicolor names use the independent application ID below. To replace the artwork, edit
`gridcraft.svg`, run `packaging/icons.sh`, and update the provenance and attribution entries.

## Palette

Exactly three colours:

| Colour | Hex | Used for |
|---|---|---|
| Ink | `#0b0b0c` | Line work and contour |
| Paper | `#efe9dc` | The page |
| GridCraft green (app colour, **proposed**) | `#1f9d55` | The full-bleed field and the header cells |

The inherited placeholder palette uses GridCraft green `#1f9d55`, with ink
(darker) variant `#147a40` for text and accents on light backgrounds. It differs from
DesignCraft's `#7bb51c`.

## Geometry

A 512 × 512 viewBox, clipped to a rounded square with `rx=112`. Windows and Linux use the full
tile. The macOS icons (`.icns`, `gridcraft-macos-512.png`) sit on Apple's grid: an 824/1024
body with a transparent margin.

## Files

| File | Use |
|---|---|
| `gridcraft.svg` | Canonical artwork; also `hicolor/scalable` |
| `gridcraft-1024.png` | 1024 px render of the full tile |
| `gridcraft-macos-512.png` | Runtime Dock icon on macOS (for `apps/gridcraft` to embed) |
| `gridcraft.icns` | macOS bundle icon (`CFBundleIconFile`) |
| `gridcraft.ico` | Windows exe icon, 16–256 px (embedded by `apps/gridcraft/build.rs`) |
| `hicolor/<size>/apps/io.github.xue_ding_e.gridcraft_next.png` | Linux icon theme, 16–512 px; the 256 px one is also the runtime window icon on Windows and Linux |
| `hicolor/scalable/apps/io.github.xue_ding_e.gridcraft_next.svg` | Linux scalable icon |
| `LICENSE.txt` | MIT OR Apache-2.0 |

The app ID is `io.github.xue_ding_e.gridcraft_next`. It's used for the hicolor icon names, the Wayland app
ID and `packaging/linux/io.github.xue_ding_e.gridcraft_next.desktop`.

## Regenerate

Edit `gridcraft.svg`, then run `packaging/icons.sh`. It needs `resvg`, and `iconutil` for the
`.icns` (macOS only). It packs the `.ico` with `cargo xtask ico`.
