# GridCraft-Next

An independently maintained derivative of GridCraft, focused on trustworthy spreadsheet data, large-sheet performance, and safer editing workflows.

Based on GridCraft by the ArtCraft team and the GridCraft contributors. This project is maintained independently by [xue-ding-e](https://github.com/xue-ding-e) and does not imply upstream sponsorship, endorsement, or support.

## Origin and current status

- Upstream: [storytold/gridcraft](https://github.com/storytold/gridcraft)
- Imported source: [84acf6293b1dc1fc09837524e035d674eb4d6741](https://github.com/storytold/gridcraft/tree/84acf6293b1dc1fc09837524e035d674eb4d6741)
- Import date: 2026-10-14
- Independent development version: `0.6.1-next.0`
- This repository starts from a source snapshot, without republishing upstream Git history or its old release tags. Original authors, contributor records, licenses and asset attribution are retained.
- Restricted ArtCraft logo/mark artwork is excluded. The original open-licensed placeholder application icon is retained with its attribution.

The independent source baseline now includes four targeted fixes: lossless CSV identifier inference and Text-format editing, AutoFilter's empty-text blank category, one-pass scattered row deletion for plain workbooks, and complete bounded Go To Special selection. Synthetic regression tests and all eight `cargo xtask ci` gates passed for [source commit 5b93a711](https://github.com/xue-ding-e/GridCraft-Next/commit/5b93a711489a53232f4a5b4f50b5864fe2d9582d) in [this Linux/WASM CI run](https://github.com/xue-ding-e/GridCraft-Next/actions/runs/38573291767).

These fixes do not replace floating-point arithmetic with an exact decimal financial engine. Native Windows installation, upgrade/uninstall, default-renderer GUI interaction, dependency notices, and distributable packaging are being validated separately; no successful Windows run or downloadable package is claimed yet.

Automatic release publication, signing and Pages deployment are disabled. CI has read-only repository permissions. Initial Windows packages are marked internal-validation-only and stay inside a disposable runner; only synthetic diagnostic evidence may be uploaded. There are no GridCraft-Next binary releases to download yet.

## Verified changes and remaining work

1. CSV import preserves long numeric identifiers and leading zeros as text. [Import tests](crates/xlsx/src/tests/csv_tests.rs), [editing/XLSX tests](crates/engine/tests/csv_identifier_editing.rs), and [GUI event tests](crates/ui-egui/tests/identifier_text_input.rs) cover the contract; see [CLI data types](docs/cli.md).
2. AutoFilter groups missing cells, stored empty text and `=""` results together without treating whitespace or zero as blank. [Tests](crates/engine/tests/autofilter_blank_values.rs) cover XLSX round trips, dropdown deduplication, reapply, undo and redo.
3. Bulk row deletion remaps plain workbook data once instead of repeatedly moving it for each scattered span. Complex workbooks retain the general path. [Regression tests](crates/engine/src/cmd/sheet_bulk_tests.rs) cover retained values and undo/redo; a large plain synthetic benchmark does not establish performance for formula-heavy workbooks.
4. Go To Special no longer silently truncates selections at 3,000 matching cells. [Bounds and behavior](docs/large-selections.md) and [selection tests](crates/engine/tests/go_to_special_selection.rs) document complete results or explicit rejection.
5. Remaining work includes native Windows delivery validation, broader cross-platform packaging, formula-heavy large-sheet performance, and an explicit financial precision contract. Ordinary numeric cells still use the inherited floating-point representation.

Only synthetic tests and public upstream assets belong in this repository. Do not commit private customer spreadsheets or production datasets.

## Development

The internal Rust package names remain `gridcraft-*` to avoid unnecessary code churn. Source builds still produce `gridcraft` and `gridcraft-cli`; distribution packaging uses the independent `gridcraft-next` and `gridcraft-next-cli` names. Application titles, configuration paths and installation identities are separated from upstream.

Use the existing source commands below and the project's `cargo xtask ci` gate. A source build is not a claim that an installer has been tested. See [release status](docs/releasing.md), [roadmap](ROADMAP.md), and [architecture](docs/architecture.md).

## Screenshots

Inherited marketing screenshots are omitted pending independent provenance review. Only newly captured GridCraft-Next screenshots using synthetic workbooks will be added after review; no screenshots are included in this repository yet.

## Inherited capabilities

- **Familiar.** Excel's ribbon, formula bar, Name Box, sheet tabs, keyboard shortcuts, fill
  handle, point-mode formula entry with coloured references, Format Cells, Paste Special,
  conditional formatting, tables, charts and more. If you know Excel, you already know GridCraft.
- **Compatible.** XLSX is the native format: styles, themes, formulas (including dynamic arrays),
  tables, conditional formats, validation, comments, hyperlinks, charts, pictures, sparklines,
  PivotTables and print settings round-trip. CSV and TSV too.
  Excel 97-2003 `.xls` workbooks open with their values, formulas and number formats (formatting and objects not yet).
  [XLSB worksheet data can be imported](docs/xlsb-import.md), using saved values instead of formulas.
  [ODS worksheet data can be imported](docs/ods-import.md), using saved values instead of formulas.
  [Static pictures can live inside cells](docs/cell-pictures.md), fitting their size and moving with the data.
- **Capable.** A dependency-graph calculation engine with dynamic arrays and spilling,
  500+ worksheet functions plus LET, LAMBDA, MAP, REDUCE, SCAN, BYROW, BYCOL and MAKEARRAY,
  structured table references, Excel's full number-format language, sort and AutoFilter,
  Flash Fill, data validation, outlining and subtotals.
- **Fast.** Copy-on-write workbooks (undo snapshots are nearly free), incremental recalculation,
  and a virtualised grid that only lays out what's on screen.
- **Agent-native.** Every ribbon button, menu item, dialog, keystroke and mouse gesture is a
  command that agents can run over a JSON control channel, an **MCP server**, or the CLI.
- **Everywhere.** One Rust codebase for desktop and the web. No subscription, no licence server,
  no telemetry.

## Quick start

```sh
cargo run --release -p gridcraft                              # desktop app
cargo run --release -p gridcraft -- --sample sales            # open a sample (sales, budget, grades)
cargo run --release -p gridcraft -- book.xlsx                 # open a workbook
cargo run --release -p gridcraft -- --sample sales --control 7979   # + JSON control channel
cargo xtask ci                                                 # fmt, clippy, tests, assets, layering, wasm
```

Choose Español under View → Interface language for [Spanish formula names, decimal commas, and semicolon arguments](docs/formula-input.md).
Workbooks, clipboard exchange, and programmatic APIs retain canonical English syntax.

### Web

```sh
cd apps/gridcraft-web && trunk build --release     # → dist/web (serve it with any static server)
cd apps/gridcraft-web && trunk serve --release     # http://127.0.0.1:8771  (?sample=sales opens a sample)
```

You need [trunk](https://trunkrs.dev) and the `wasm32-unknown-unknown` target. Open uses the
browser's file picker (dropping files works too) and Save downloads the workbook.

### Ribbon access keys

Press and release **left Alt** on Windows/Linux/BSD, or press **F10** on any platform,
then type a sequence below one key at a time. Keytips appear on the supported Home
controls and menu items. On macOS, use F10 (Fn+F10 if required by your keyboard); Option
remains available for entering accented characters.

| Sequence after Alt/F10 | Action |
|---|---|
| `H A L` / `H A C` / `H A R` | Align left / center / right |
| `H V S` or legacy `E S` | Open Paste Special |
| `H O I` or legacy `O C A` | AutoFit selected columns |

In Paste Special, **T/V/F/C** select formats/values/formulas/comments, and **E** toggles
Transpose. **Enter** applies the selected options; **Escape** cancels the dialog.
These letters also work with Alt held on Windows/Linux/BSD.

Escape backs out one keytip level; Alt/F10 again exits keytips. Clicking, scrolling,
or switching windows cancels navigation. Access keys do not start while editing text
or using another dialog. This is initial support for these Home and legacy paths;
other ribbon commands and full mouse-free navigation remain to be implemented.
Browsers or desktop environments may reserve Alt/F10 for their own menus.

### Logs

The desktop app writes its log to standard error and to `gridcraft.log` in the `logs` folder
next to its settings: `~/.config/gridcraft-next/logs` on Linux and BSD (`$XDG_CONFIG_HOME/gridcraft-next/logs`
when set), `~/Library/Application Support/GridCraft-Next/logs` on macOS and
`%APPDATA%\GridCraft-Next\logs` on Windows. Each start moves the previous log to `gridcraft.1.log` and
that one to `gridcraft.2.log`, so the log of a run that crashed survives the next start; attach
them to a bug report. A log file stops growing at 16 MiB. By default GridCraft's own crates log
at `info` and everything else at `warn`; `RUST_LOG` replaces that with env_logger-style
directives, e.g. `RUST_LOG=debug` or `RUST_LOG=warn,gridcraft_engine=debug` (a trailing `*`
matches a prefix: `gridcraft*=debug`). Runs with `GRIDCRAFT_NO_PREFS` set (no saved settings)
log to standard error only.

## For agents: MCP, CLI and the control channel

```sh
claude mcp add gridcraft-next -- gridcraft-cli mcp                 # headless MCP server
gridcraft-cli mcp --connect 7979                               # MCP bridged to a running window
gridcraft-cli eval '=XLOOKUP("b",{"a","b"},{1,2})'             # → 2
gridcraft-cli run --sample budget --cmd 'home.bold={"range":"B4:F4"}' --out budget.xlsx
gridcraft-cli cat budget.xlsx --range B4:F15                   # aligned text table
gridcraft-cli commands --search chart                          # every command and its params
```

The MCP server offers tools such as `read_range`, `write_range`, `set_cell`, `evaluate_formula`,
`format_range`, `insert_chart`, `create_table`, `sort_range`, `filter` and `execute_command`
(which reaches every one of GridCraft's commands). The desktop app's control channel adds real
pointer and keyboard input, dialogs and screenshots. See [`docs/mcp.md`](docs/mcp.md),
[`docs/cli.md`](docs/cli.md) and [`docs/control-protocol.md`](docs/control-protocol.md).

## Architecture

GridCraft is an engine-first Cargo workspace with enforced layering (`cargo xtask layers`). The
egui frontend is a separate crate, so the UI can be swapped without touching the engine.

| Layer | Crates |
|---|---|
| L0 | `core` (addresses, values, errors, dates, input parsing) · `numfmt` (number format codes) |
| L1 | `formula` (lexer, parser, printer, reference adjustment) · `functions` (worksheet function library) |
| L2 | `model` (workbook, sheets, copy-on-write cells, styles, tables, charts…) · `calc` (dependency graph, evaluator, dynamic arrays) · `xlsx` (XLSX, CSV, plus XLSB and ODS data import) |
| L3 | `chart` (toolkit-free chart layout and rendering) |
| L5 | `engine` (session, commands, history, clipboard, fill, sort, filter, file I/O) |
| L6 | `ui-egui` (Excel-style UI, control channel) · `mcp` (MCP server) |
| L7 | `apps/gridcraft`, `apps/gridcraft-cli`, `apps/gridcraft-web` |

- **Contributor and agent rules** (clean-room, asset policy, never-crash, quality gates): [`AGENTS.md`](AGENTS.md)
- **Bundled assets:** each one is listed with its licence in [`ATTRIBUTION.md`](ATTRIBUTION.md)
- **Release preparation:** adapted macOS, Windows, Linux, BSD and web packaging still requires independent platform validation; see [`docs/releasing.md`](docs/releasing.md)

## Roadmap

GridCraft is in **alpha**: the core workflow works end to end, but depth, fidelity and file
compatibility are still rough. It covers 89% of the Excel ribbon commands in our catalog
([`docs/parity-checklist.md`](docs/parity-checklist.md)) and 513 of Excel's 523 worksheet functions
([`docs/function-parity.md`](docs/function-parity.md)); weighted by what Excel users rely on, we
estimate it is about 50% ready for real work. Next up: XLSX fidelity on real-world files, very large
workbooks and multi-threaded recalc, chart depth, text in every script, and `.xls` import. Stage,
numbers and estimates are in [ROADMAP.md](ROADMAP.md); the ranked work list is [`docs/gaps.md`](docs/gaps.md).

## Source and issue tracking

The source and issue tracker are at [xue-ding-e/GridCraft-Next](https://github.com/xue-ding-e/GridCraft-Next). Report issues with synthetic reproductions and avoid attaching private workbooks.

Platform packaging scripts are retained and adapted for this independent project. Releases will be advertised only after their build, identity, license contents and installation behavior have been verified. Upstream binaries are upstream products, not GridCraft-Next releases.

## License and credits

GridCraft-Next retains the code's [MIT](LICENSE-MIT) OR [Apache-2.0](LICENSE-APACHE) licensing. Copyright (c) 2026 ArtCraft Team and the GridCraft contributors. See [NOTICE](NOTICE) for original attribution and independent-distribution notes.

Asset authors and their licenses are listed in [ATTRIBUTION.md](ATTRIBUTION.md). Contributor records and the About window's contributor/model credits are preserved. The application icon remains the original GridCraft contributors' MIT OR Apache-2.0 placeholder artwork, under [its license](assets/app-icon/LICENSE.txt).

The ArtCraft trademark artwork is not included in this derivative. [Its original terms](docs/brand/LICENSE-brand.txt) remain as legal provenance; they do not grant this project rights to use the removed marks. Plain-text references identify the origin of the work.

Optional builds using `CRAFT_FONTS_DIR` must include each embedded font's license. By default the application uses system fonts and egui's defaults; system fonts are not copied into this repository.

Microsoft, Excel and Microsoft 365 are trademarks or registered trademarks of Microsoft Corporation. This independent project is not affiliated with, sponsored by or endorsed by Microsoft Corporation. Those names describe compatibility goals only.
