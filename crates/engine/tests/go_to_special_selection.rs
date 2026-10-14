use gridcraft_engine::core::{CellRef, Value};
use gridcraft_engine::model::{Cell, Workbook};
use gridcraft_engine::{DocState, EngineError, Mode, Session, UiRequest};
use serde_json::json;
use std::sync::Arc;

fn alternating_blank_session(areas: u32, column: u32) -> Session {
    let mut wb = Workbook::new();
    let sh = wb.sheet_mut(0).unwrap();
    sh.cells.set(CellRef::new(0, column), Cell::value(Value::text("ID")));
    for row in (1..=areas * 2).step_by(2) {
        sh.cells.set(CellRef::new(row, column), Cell::value(Value::text(format!("kept-{row}"))));
    }
    let mut session = Session::new();
    session.add_document(DocState::new(wb, None, "special selection".into()));
    session.execute("selection.set", json!({"range": format!("{}:{}", CellRef::new(1, column).a1(), CellRef::new(areas * 2, column).a1())})).unwrap();
    session
}

#[test]
fn go_to_special_retains_all_4005_blank_areas_and_reports_the_selected_count() {
    let mut session = alternating_blank_session(4005, 0);
    let result = session.execute("edit.goToSpecial", json!({"kind": "blanks"})).unwrap();
    let selection = &session.doc().unwrap().selection;
    assert_eq!(result["count"], 4005);
    assert_eq!(selection.ranges.len(), 4005);
    assert_eq!(selection.ranges.iter().map(|r| r.count()).sum::<u64>(), 4005);
    assert_eq!(selection.ranges.last().unwrap().a1(), "A8011");
    assert_eq!(result["selection"], selection.a1());
}

#[test]
fn go_to_special_retains_all_4005_filtered_visible_areas() {
    // Same single-column D filter/visible workflow as docs/large-selections.md.
    let mut session = alternating_blank_session(4005, 3);
    session.execute("data.filter", json!({"range": "A1:I8011"})).unwrap();
    session.execute("data.filterBy", json!({"column": "D", "values": [], "blanks": true})).unwrap();
    let result = session.execute("edit.goToSpecial", json!({"kind": "visible"})).unwrap();
    let selection = &session.doc().unwrap().selection;
    assert_eq!(result["count"], 4005);
    assert_eq!(selection.ranges.len(), 4005);
    assert!(selection.ranges.iter().all(|r| r.is_single() && r.start.row.is_multiple_of(2)));
    assert_eq!(selection.ranges.last().unwrap().a1(), "D8011");

    // The ordinary deletion command must consume all selected visible rows, not a prefix.
    let before = session.doc().unwrap().wb.clone();
    session.execute("home.deleteRows", json!({})).unwrap();
    let sh = session.doc().unwrap().wb.sheet(0).unwrap();
    assert_eq!(sh.cells.len(), 4006);
    assert_eq!(sh.used_range().unwrap().end.row, 4005);
    for row in 1..=4005 {
        assert_eq!(sh.value(CellRef::new(row, 3)), Value::text(format!("kept-{}", row * 2 - 1)));
    }
    session.execute("edit.undo", json!({})).unwrap();
    assert_eq!(*session.doc().unwrap().wb, *before);
    session.execute("edit.redo", json!({})).unwrap();
    assert_eq!(session.doc().unwrap().wb.sheet(0).unwrap().cells.len(), 4006);
}

#[test]
fn go_to_special_area_limit_is_explicit_and_does_not_replace_the_selection() {
    let mut at_limit = alternating_blank_session(65_536, 0);
    let result = at_limit.execute("edit.goToSpecial", json!({"kind": "blanks"})).unwrap();
    assert_eq!(result["count"], 65_536);
    assert_eq!(at_limit.doc().unwrap().selection.ranges.len(), 65_536);

    let mut too_many = alternating_blank_session(65_537, 0);
    assert_selection_error_is_atomic(&mut too_many, "65536");
}

#[test]
fn go_to_special_large_contiguous_area_is_compact_and_scan_limit_stays_bounded() {
    let mut session = Session::new();
    session.new_workbook();
    session.execute("selection.set", json!({"range": "A2:A700001"})).unwrap();
    let result = session.execute("edit.goToSpecial", json!({"kind": "blanks"})).unwrap();
    assert_eq!(result["count"], 700_000);
    assert_eq!(session.doc().unwrap().selection.ranges.len(), 1);
    assert_eq!(session.doc().unwrap().selection.a1(), "A2:A700001");

    session.execute("selection.set", json!({"range": "A1:I700000"})).unwrap();
    assert_selection_error_is_atomic(&mut session, "4000000");
}

fn assert_selection_error_is_atomic(session: &mut Session, expected_limit: &str) {
    // Keep nontrivial undo, redo, copy, cursor and edit state across the rejected request.
    session.execute("cell.set", json!({"cell": "B1", "input": "keep-undo"})).unwrap();
    session.execute("cell.set", json!({"cell": "B2", "input": "keep-redo"})).unwrap();
    session.execute("edit.undo", json!({})).unwrap();
    session.execute("edit.copy", json!({"range": "A1"})).unwrap();
    session.mode = Mode::Point;
    session.last_find = Some(json!({"text": "unchanged"}));
    session.ui_requests.push(UiRequest::Message("unchanged".into()));
    let before = session.doc().unwrap().clone();
    let clipboard = session.clipboard.clone().unwrap();
    let journal = session.journal.clone();
    let requests = session.ui_requests.clone();
    let last_find = session.last_find.clone();

    let err = session.execute("edit.goToSpecial", json!({"kind": "blanks"})).unwrap_err();
    assert!(matches!(err, EngineError::BadParams { .. }));
    assert!(err.to_string().contains(expected_limit));
    assert!(err.to_string().contains("smaller range"));
    assert!(err.to_string().contains("batches"));
    let after = session.doc().unwrap();
    assert_eq!(after.selection, before.selection);
    assert_eq!(after.sheet_selections, before.sheet_selections);
    assert!(Arc::ptr_eq(&after.wb, &before.wb));
    assert!(Arc::ptr_eq(&after.saved, &before.saved));
    assert_eq!(after.revision, before.revision);
    assert_eq!(after.undo.len(), before.undo.len());
    assert_eq!(after.redo.len(), before.redo.len());
    assert_eq!(session.journal, journal);
    assert_eq!(session.ui_requests, requests);
    assert_eq!(session.mode, Mode::Point);
    assert_eq!(session.last_find, last_find);
    let after_clipboard = session.clipboard.as_ref().unwrap();
    assert!(Arc::ptr_eq(&after_clipboard.wb, &clipboard.wb));
    assert_eq!(after_clipboard.range, clipboard.range);
    assert_eq!(after_clipboard.selected, clipboard.selected);
    assert_eq!(after_clipboard.areas, clipboard.areas);
    assert_eq!(after_clipboard.text, clipboard.text);
    assert_eq!(after_clipboard.cut, clipboard.cut);
    assert_eq!(after_clipboard.sheet, clipboard.sheet);
    assert_eq!(after_clipboard.doc_uid, clipboard.doc_uid);
}
