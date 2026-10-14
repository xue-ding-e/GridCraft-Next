# Large Go To Special selections

Updated: 2026-10-14

`edit.goToSpecial` scans at most 4,000,000 cells and returns at most 65,536 separate
areas. Adjacent matching cells in the same column form one vertical area. These
are resource limits, not a promise of interactive rendering speed.

Every successful result includes every matching cell, and `count` reports the
number of selected cells, not the number of areas. If either limit would be
exceeded, the command returns an error before changing the selection, active
cell, clipboard or history. It never silently drops later matches. Select a
smaller range, one relevant column, or work in batches instead.

For 700,000 data rows, scanning one identifier column is within the cell limit;
scanning nine columns (6,300,000 cells) is not. A full column is also bounded by
the sheet's 1,048,576-row limit, but selecting only the data body avoids selecting
unused blank rows and the header.

## Filtered-row deletion

Filtering hides rows; it does not turn a rectangular selection into a selection
of visible rows. `home.deleteRows` deletes the physical row numbers supplied by
its explicit `rows` parameter, or those covered by the current selection. Use
Go To Special `visible` on the filtered identifier column to build the intended
selection first, then inspect its result before deleting:

```jsonl
{"command":"data.filter","params":{"range":"A1:I700001"}}
{"command":"data.filterBy","params":{"column":"D","values":[],"blanks":true}}
{"command":"selection.set","params":{"range":"D2:D700001"}}
{"command":"edit.goToSpecial","params":{"kind":"visible"}}
```

The example is synthetic: column D is the identifier column and row 1 is the
header. After verifying the returned count and selection, `home.deleteRows` with
empty parameters deletes those selected rows. A CLI script stops at a failing
step, so a Go To Special limit error must not be ignored by other callers.

AutoFilter's blank category includes empty text and formula results of `""`.
Go To Special `blanks` keeps its existing empty-cell predicate; use the filter
plus `visible` workflow when empty-text identifiers must also be included.

Revision history:
- 2026-10-14: Document bounded complete selections and explicit errors in place of
  the previous silent 2,000-area truncation.
