//! CSV identifier inference must remain safe through later edits and file round trips.
use gridcraft_core::{CellRef, Value};
use gridcraft_engine::{DocState, Session, io};
use gridcraft_xlsx::{CsvOptions, read_csv, read_xlsx};
use serde_json::json;

#[test]
fn inferred_csv_identifiers_stay_text_when_edited_and_saved() {
    // Invalid regions/dates and leading zeros make these clearly synthetic identifiers.
    let csv = b"id,amount\n990000000000000001,12.50\n000000000000000123,-3.75\n";
    let wb = read_csv(csv, &CsvOptions::default()).unwrap();
    let mut s = Session::new();
    s.add_document(DocState::new(wb, None, "synthetic identifiers".into()));
    s.execute("cell.set", json!({"cell":"A2","input":"990000000000000019"})).unwrap();
    s.execute("range.setValues", json!({"range":"A3","values":[["000000000000000456"]]})).unwrap();
    s.execute("cell.set", json!({"cell":"B2","input":"24.50"})).unwrap();
    s.execute("cell.set", json!({"cell":"C2","input":"=B2*2"})).unwrap();
    let expected = [(CellRef::new(1, 0), "990000000000000019"), (CellRef::new(2, 0), "000000000000000456")];
    for (pos, text) in expected {
        assert_eq!(s.doc().unwrap().wb.active().unwrap().value(pos), Value::text(text));
    }
    for format in ["csv", "xlsx"] {
        let bytes = io::save_bytes(&s.doc().unwrap().wb, &format!("synthetic.{format}")).unwrap();
        let wb = if format == "csv" {
            read_csv(&bytes, &CsvOptions::default()).unwrap()
        } else {
            let (wb, report) = read_xlsx(&bytes).unwrap();
            assert!(report.warnings.is_empty());
            wb
        };
        let sh = wb.active().unwrap();
        for (pos, text) in expected {
            assert_eq!(sh.value(pos), Value::text(text), "{format}: edited identifier changed");
            assert_eq!(wb.styles.get(sh.style_id(pos)).num_fmt.as_str(), "@");
        }
        assert_eq!(sh.value(CellRef::new(1, 1)), Value::Number(24.5));
        assert_eq!(sh.value(CellRef::new(2, 1)), Value::Number(-3.75));
        assert_eq!(sh.value(CellRef::new(1, 2)), Value::Number(49.0));
        if format == "xlsx" {
            assert!(sh.cell(CellRef::new(1, 2)).unwrap().formula.is_some());
        }
    }
}

#[test]
fn text_columns_protect_new_rows_paste_and_crud_after_xlsx_round_trip() {
    let wb = read_csv(b"id,amount\n990000000000000001,12.50\n000000000000000123,-3.75\n", &CsvOptions::default()).unwrap();
    let mut s = Session::new();
    s.add_document(DocState::new(wb, None, "synthetic text columns".into()));
    s.execute("home.numberFormat", json!({"range":"A:A","format":"Text"})).unwrap();
    s.execute("cell.set", json!({"cell":"C2","input":"=B2*2"})).unwrap();
    s.execute("home.insertRows", json!({"rows":"3:3"})).unwrap();
    s.execute("range.setValues", json!({"range":"A3","values":[["990000000000000099",10.25]]})).unwrap();
    s.execute("edit.paste", json!({"at":"A4","text":"000000000000000456\t-3.75"})).unwrap();
    let before_delete = s.doc().unwrap().wb.clone();
    s.execute("home.deleteRows", json!({"rows":"3:3"})).unwrap();
    assert_eq!(s.doc().unwrap().wb.active().unwrap().value(CellRef::new(2, 0)), Value::text("000000000000000456"));
    s.execute("edit.undo", json!({})).unwrap();
    assert_eq!(*s.doc().unwrap().wb, *before_delete);
    s.execute("edit.redo", json!({})).unwrap();
    let bytes = io::save_bytes(&s.doc().unwrap().wb, "synthetic.xlsx").unwrap();
    let (wb, report) = read_xlsx(&bytes).unwrap();
    assert!(report.warnings.is_empty());
    let mut reopened = Session::new();
    reopened.add_document(DocState::new(wb, None, "reopened text columns".into()));
    reopened.execute("home.insertRows", json!({"rows":"3:3"})).unwrap();
    reopened.execute("cell.set", json!({"cell":"A3","input":"990000000000000077"})).unwrap();
    reopened.execute("range.setValues", json!({"range":"A4","values":[["000000000000000088",-3.75]]})).unwrap();
    reopened.execute("edit.paste", json!({"at":"A5","text":"990000000000000089\t10.25", "html":r#"<table><tr><td x:num="990000000000000000">990000000000000089</td><td x:num="10.25">10.25</td></tr></table>"#})).unwrap();
    // A second save proves column defaults, rather than only existing per-cell formats, survive.
    let bytes = io::save_bytes(&reopened.doc().unwrap().wb, "synthetic-again.xlsx").unwrap();
    let (wb, report) = read_xlsx(&bytes).unwrap();
    assert!(report.warnings.is_empty());
    let sh = wb.active().unwrap();
    for (row, expected) in [(1, "990000000000000001"), (2, "990000000000000077"), (3, "000000000000000088"), (4, "990000000000000089")] {
        let at = CellRef::new(row, 0);
        assert_eq!(sh.value(at), Value::text(expected));
        assert_eq!(wb.styles.get(sh.style_id(at)).num_fmt.as_str(), "@");
    }
    assert_eq!(sh.value(CellRef::new(1, 1)), Value::Number(12.5));
    assert_eq!(sh.value(CellRef::new(3, 1)), Value::Number(-3.75));
    assert_eq!(sh.value(CellRef::new(4, 1)), Value::Number(10.25));
    assert_eq!(sh.value(CellRef::new(1, 2)), Value::Number(25.0));
    assert!(sh.cell(CellRef::new(1, 2)).unwrap().formula.is_some());
}

#[test]
fn set_values_explicit_json_types_are_not_reinterpreted_by_text_format() {
    let mut s = Session::new();
    s.add_document(DocState::new(gridcraft_engine::model::Workbook::new(), None, "JSON types".into()));
    s.execute("home.numberFormat", json!({"range":"A:C","format":"Text"})).unwrap();
    s.execute(
        "range.setValues",
        json!({"range":"A1","values":[[12.5,true,"000000000000000123"],[990000000000000001u64,false,"990000000000000001"]]}),
    )
    .unwrap();
    let wb = &s.doc().unwrap().wb;
    let sh = wb.active().unwrap();
    assert_eq!(sh.value(CellRef::new(0, 0)), Value::Number(12.5));
    assert_eq!(sh.value(CellRef::new(0, 1)), Value::Bool(true));
    assert_eq!(sh.value(CellRef::new(0, 2)), Value::text("000000000000000123"));
    // JSON numbers deliberately use the spreadsheet's f64 model; callers must send IDs as strings.
    assert_eq!(sh.value(CellRef::new(1, 0)), Value::Number(990000000000000001u64 as f64));
    assert_eq!(sh.value(CellRef::new(1, 1)), Value::Bool(false));
    assert_eq!(sh.value(CellRef::new(1, 2)), Value::text("990000000000000001"));
}
