use gridcraft_engine::core::{CellRef, Value};
use gridcraft_engine::model::{Cell, Formula, Workbook};
use gridcraft_engine::{DocState, Session};
use serde_json::json;

fn session_with_blank_values() -> Session {
    let mut wb = Workbook::new();
    let sh = wb.sheet_mut(0).unwrap();
    for row in 0..=8 {
        sh.cells.set(CellRef::new(row, 0), Cell::value(Value::text(format!("row{row}"))));
    }
    sh.cells.set(CellRef::new(0, 1), Cell::value(Value::text("value")));
    // Row 2 has no B cell. Row 3 is a stored zero-length string, as in sharedStrings.xml.
    sh.cells.set(CellRef::new(2, 1), Cell::value(Value::text("")));
    sh.cells.set(CellRef::new(3, 1), Cell::formula(Formula::new("=\"\"")));
    sh.cells.set(CellRef::new(4, 1), Cell::value(Value::text(" ")));
    sh.cells.set(CellRef::new(5, 1), Cell::value(Value::Number(0.0)));
    sh.cells.set(CellRef::new(6, 1), Cell::value(Value::text("kept")));
    sh.cells.set(CellRef::new(7, 1), Cell::value(Value::text("\t")));
    sh.cells.set(CellRef::new(8, 1), Cell::value(Value::text("\u{a0}")));
    // Exercise the XLSX import path without converting the empty text into an absent cell.
    let bytes = gridcraft_xlsx::write_xlsx(&wb).unwrap();
    let (wb, _) = gridcraft_xlsx::read_xlsx(&bytes).unwrap();
    let mut session = Session::new();
    session.add_document(DocState::new(wb, None, "blank values".into()));
    session.execute("data.filter", json!({"range": "A1:B9"})).unwrap();
    session
}

fn visible_rows(session: &Session) -> Vec<u32> {
    let sh = session.doc().unwrap().wb.active().unwrap();
    (1..=8).filter(|row| !sh.is_row_hidden(*row)).collect()
}

#[test]
fn autofilter_blanks_include_empty_text_and_formula_results_but_not_whitespace_or_zero() {
    let mut session = session_with_blank_values();
    session.execute("data.filterBy", json!({"column": "B", "values": [], "blanks": true})).unwrap();
    assert_eq!(visible_rows(&session), [1, 2, 3]);
    let sh = session.doc().unwrap().wb.active().unwrap();
    assert_eq!(sh.value(CellRef::new(1, 1)), Value::Empty);
    assert_eq!(sh.value(CellRef::new(2, 1)), Value::text(""));
    assert_eq!(sh.value(CellRef::new(3, 1)), Value::text(""));
    assert!(sh.cell(CellRef::new(3, 1)).unwrap().formula.is_some());
    assert!(!Value::text("").is_empty(), "filtering must not change the core empty-cell distinction");

    session.execute("data.reapply", json!({})).unwrap();
    assert_eq!(visible_rows(&session), [1, 2, 3]);
    session.execute("data.filterBy", json!({"column": "B", "values": ["", " ", "0", "kept", "\t", "\u{a0}"], "blanks": false})).unwrap();
    assert_eq!(visible_rows(&session), [4, 5, 6, 7, 8]);
    session.execute("edit.undo", json!({})).unwrap();
    assert_eq!(visible_rows(&session), [1, 2, 3]);
    session.execute("edit.redo", json!({})).unwrap();
    assert_eq!(visible_rows(&session), [4, 5, 6, 7, 8]);
}

#[test]
fn autofilter_dropdown_has_one_blank_entry_and_uses_the_same_selection_rule() {
    let mut session = session_with_blank_values();
    for blanks in [true, false] {
        session.execute("data.filterBy", json!({"column": "B", "values": ["kept"], "blanks": blanks})).unwrap();
        let items = gridcraft_engine::cmd::data::filter_values(&session.doc().unwrap().wb, 0, 1);
        assert_eq!(items.iter().filter(|(text, _)| text == "(Blanks)").cloned().collect::<Vec<_>>(), [("(Blanks)".into(), blanks)]);
        assert!(!items.iter().any(|(text, _)| text.is_empty()), "empty text belongs to the single (Blanks) entry");
        assert!(items.contains(&("kept".into(), true)));
        assert!(items.contains(&(" ".into(), false)));
        assert!(items.contains(&("0".into(), false)));
    }
}
