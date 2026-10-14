//! Synthetic identifier regression through egui input events (no native window is required).
use egui::{Event, Key, Modifiers};
use gridcraft_engine::core::{CellRef, Value};
use gridcraft_engine::{Session, io};
use gridcraft_ui_egui::{Services, SheetApp};
use serde_json::json;

type Harness = egui_kittest::Harness<'static, SheetApp>;

fn value(h: &Harness, a1: &str) -> Value {
    h.state().session.doc().unwrap().wb.active().unwrap().value(CellRef::parse(a1).unwrap())
}

fn type_and_commit(h: &mut Harness, at: &str, text: &str) {
    h.state_mut().run("selection.set", json!({"cell":at})).unwrap();
    h.run_steps(2);
    h.input_mut().events.push(Event::Text(text.into()));
    h.run_steps(2);
    h.key_press(Key::Enter);
    h.run_steps(2);
}

#[test]
fn text_column_protects_identifier_paste_typing_undo_and_xlsx_save() {
    let mut s = Session::new();
    s.new_workbook();
    s.execute("range.setValues", json!({"range":"A1","values":[["id","amount","double"]]})).unwrap();
    s.execute("home.numberFormat", json!({"range":"A:A","format":"Text"})).unwrap();
    s.execute("selection.set", json!({"cell":"A2"})).unwrap();
    let services = Services {
        paste_html: Some(Box::new(|| {
            Some(r#"<table><tr><td x:num="990000000000000000">990000000000000019</td><td x:num="12.5">12.5</td></tr></table>"#.into())
        })),
        ..Default::default()
    };
    let mut h = Harness::builder().with_size(egui::vec2(1200.0, 800.0)).build_ui_state(
        |ui, app: &mut SheetApp| {
            app.logic(ui.ctx());
            app.ui(ui);
        },
        SheetApp::new(s, services),
    );
    h.run_steps(4);
    h.input_mut().events.push(Event::Paste("990000000000000019\t12.5".into()));
    h.run_steps(3);
    assert_eq!(value(&h, "A2"), Value::text("990000000000000019"));
    assert_eq!(value(&h, "B2"), Value::Number(12.5));
    type_and_commit(&mut h, "A3", "000000000000000123");
    assert_eq!(value(&h, "A3"), Value::text("000000000000000123"));
    type_and_commit(&mut h, "C2", "=B2*2");
    assert_eq!(value(&h, "C2"), Value::Number(25.0));
    let ctrl = Modifiers { ctrl: true, command: true, ..Modifiers::NONE };
    h.key_press_modifiers(ctrl, Key::Z);
    h.run_steps(2);
    assert_eq!(value(&h, "C2"), Value::Empty);
    h.key_press_modifiers(ctrl, Key::Y);
    h.run_steps(2);
    assert_eq!(value(&h, "C2"), Value::Number(25.0));
    let bytes = io::save_bytes(&h.state().session.doc().unwrap().wb, "synthetic.xlsx").unwrap();
    let (wb, warnings) = io::open_bytes("synthetic.xlsx", &bytes).unwrap();
    assert!(warnings.is_empty());
    let sh = wb.active().unwrap();
    assert_eq!(sh.value(CellRef::new(1, 0)), Value::text("990000000000000019"));
    assert_eq!(sh.value(CellRef::new(2, 0)), Value::text("000000000000000123"));
    assert_eq!(sh.value(CellRef::new(1, 1)), Value::Number(12.5));
    assert_eq!(sh.value(CellRef::new(1, 2)), Value::Number(25.0));
}
