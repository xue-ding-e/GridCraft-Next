use super::*;
use gridcraft_model::*;

fn plain_workbook() -> Workbook {
    let mut wb = Workbook::new();
    let style = wb.styles.derive(StyleId::DEFAULT, |s| s.num_fmt = NumFmt::new("@"));
    let sh = wb.sheet_mut(0).unwrap();
    for row in 0..=130 {
        sh.cells.set(CellRef::new(row, 0), Cell { value: Value::text(format!("00000000000000{row:04}")), style, ..Default::default() });
        sh.cells.set(CellRef::new(row, 1), Cell::value(Value::Number(f64::from(row) / 100.0)));
        if row.is_multiple_of(7) {
            sh.rows.insert(row, LineInfo { size: Some(31.0), hidden: row.is_multiple_of(3), style: Some(style), custom: true, ..Default::default() });
        }
    }
    sh.cols.insert(0, LineInfo { size: Some(90.0), style: Some(style), ..Default::default() });
    sh.autofilter = Some(AutoFilter { range: RangeRef::parse("A1:B131").unwrap(), criteria: vec![] });
    let mut other = Sheet::new("Other");
    other.cells.set(CellRef::new(100, 1), Cell::value(Value::text("unchanged")));
    wb.sheets.push(Arc::new(other));
    wb
}

fn session(wb: Workbook) -> Session {
    let mut s = Session::new();
    s.add_document(DocState::new(wb, None, "bulk delete".into()));
    s
}

fn old_delete(wb: &mut Workbook, areas: &[(u32, u32)]) {
    let name = wb.sheet(0).unwrap().name.clone();
    for &(at, count) in areas {
        let sh = wb.sheet_mut(0).unwrap();
        sh.take_cells(RangeRef::rows(at, at + count - 1));
        sh.cells.shift_rows(at + count, -(count as i64));
        shift_sheet_features(sh, Axis::Rows, at, count, false);
        rewrite(wb, &name, &Edit::Delete { axis: Axis::Rows, at, count });
    }
}

#[test]
fn row_delete_map_handles_empty_overlap_and_worksheet_boundaries() {
    for areas in [vec![], vec![(0, 1)], vec![(MAX_ROWS - 1, 1)], vec![(0, MAX_ROWS)], vec![(7, 3), (5, 4), (5, 4)]] {
        let map = RowDeleteMap::new(&areas);
        let deleted = |row| areas.iter().any(|&(at, count)| row >= at && row < at.saturating_add(count).min(MAX_ROWS));
        let mut before = 0;
        for row in 0..MAX_ROWS {
            if deleted(row) {
                assert_eq!(map.row(row), None);
                before += 1;
            } else {
                assert_eq!(map.row(row), Some(row - before));
            }
        }
        assert_eq!(map.row(MAX_ROWS), None);
        let full = map.range(RangeRef::rows(0, MAX_ROWS - 1));
        assert_eq!(full.map(|r| r.height()), (before < MAX_ROWS).then_some(MAX_ROWS - before));
    }
}

#[test]
fn batch_rows_match_sequential_deletion_for_plain_data_and_line_metadata() {
    let mut seed = 0x5eeda11u64;
    for case in 0..32 {
        let mut s = session(plain_workbook());
        let mut spans = Vec::new();
        for _ in 0..24 {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            let start = 2 + seed % 150;
            let end = start + (seed >> 9) % 5;
            spans.push(format!("{start}:{end}"));
        }
        spans.extend(["65:65".into(), "66:67".into(), "65:65".into(), "1048576:1048576".into()]);
        let params = json!({"rows": spans.join(",")});
        let areas = delete_areas(&s, &params, Axis::Rows).unwrap();
        assert!(can_batch_delete_rows(&s.doc().unwrap().wb, 0, &areas));
        let before = s.doc().unwrap().wb.clone();
        let selection = s.doc().unwrap().selection.clone();
        let other = before.sheets[1].clone();
        let mut expected = (*before).clone();
        old_delete(&mut expected, &areas);
        s.execute("home.deleteRows", params).unwrap();
        assert_eq!(*s.doc().unwrap().wb, expected, "case {case}");
        assert!(Arc::ptr_eq(&s.doc().unwrap().wb.sheets[1], &other));
        assert_eq!(s.doc().unwrap().selection, selection);
        assert_eq!(s.doc().unwrap().undo.len(), 1);
        s.execute("edit.undo", json!({})).unwrap();
        assert_eq!(*s.doc().unwrap().wb, *before);
        s.execute("edit.redo", json!({})).unwrap();
        assert_eq!(*s.doc().unwrap().wb, expected);
    }
}

#[test]
fn batch_rows_keep_filter_criteria_and_delete_only_explicit_visible_rows() {
    let mut wb = plain_workbook();
    let sh = wb.sheet_mut(0).unwrap();
    sh.rows.clear();
    for row in (2..=130).step_by(2) {
        sh.cells.set(CellRef::new(row, 0), Cell::value(Value::text("")));
    }
    let mut s = session(wb);
    s.execute("data.filterBy", json!({"column": "A", "values": [], "blanks": true})).unwrap();
    let visible: Vec<u32> = (1..=130).filter(|r| !s.doc().unwrap().wb.sheet(0).unwrap().is_row_hidden(*r)).collect();
    assert_eq!(visible, (2..=130).step_by(2).collect::<Vec<_>>());
    let rows = visible.iter().map(|r| format!("{}:{}", r + 1, r + 1)).collect::<Vec<_>>().join(",");
    let before = s.doc().unwrap().wb.clone();
    let expected: Vec<_> = before.sheet(0).unwrap().cells.iter().filter(|(c, _)| !visible.contains(&c.row)).map(|(_, v)| v.clone()).collect();
    s.execute("edit.copy", json!({"range": "A1"})).unwrap();
    assert!(s.clipboard.is_some());
    let undo_before = s.doc().unwrap().undo.len();
    s.execute("home.deleteRows", json!({"rows": rows})).unwrap();
    assert!(s.clipboard.is_none());
    let sh = s.doc().unwrap().wb.sheet(0).unwrap();
    assert_eq!(sh.cells.iter().map(|(_, v)| v.clone()).collect::<Vec<_>>(), expected);
    assert_eq!(sh.used_range().unwrap().height(), 66);
    assert_eq!(sh.autofilter.as_ref().unwrap().range.a1(), "A1:B66");
    assert_eq!(sh.autofilter.as_ref().unwrap().criteria, before.sheet(0).unwrap().autofilter.as_ref().unwrap().criteria);
    assert!((1..=65).all(|r| sh.is_row_hidden(r)), "surviving nonblank rows keep their filter-hidden state");
    assert_eq!(s.doc().unwrap().undo.len(), undo_before + 1);
    let after = s.doc().unwrap().wb.clone();
    s.execute("edit.undo", json!({})).unwrap();
    assert_eq!(*s.doc().unwrap().wb, *before);
    s.execute("edit.redo", json!({})).unwrap();
    assert_eq!(*s.doc().unwrap().wb, *after);
    s.execute("data.clearFilter", json!({})).unwrap();
    assert!((1..=65).all(|r| !s.doc().unwrap().wb.sheet(0).unwrap().is_row_hidden(r)));
    let bytes = gridcraft_xlsx::write_xlsx(&s.doc().unwrap().wb).unwrap();
    let (roundtrip, _) = gridcraft_xlsx::read_xlsx(&bytes).unwrap();
    assert_eq!(roundtrip.sheet(0).unwrap().cells, s.doc().unwrap().wb.sheet(0).unwrap().cells);
}

#[test]
fn direct_batch_handles_no_rows_and_all_filter_data_rows() {
    let before = plain_workbook();
    let mut wb = before.clone();
    batch_delete_plain_rows(wb.sheet_mut(0).unwrap(), &[]);
    assert_eq!(wb, before);
    for areas in [vec![(1, 130)], vec![(66, 65), (1, 65)]] {
        let mut wb = before.clone();
        assert!(can_batch_delete_rows(&wb, 0, &areas));
        batch_delete_plain_rows(wb.sheet_mut(0).unwrap(), &areas);
        assert_eq!(wb.sheet(0).unwrap().cells.len(), 2);
        assert_eq!(wb.sheet(0).unwrap().autofilter.as_ref().unwrap().range.a1(), "A1:B1");
        let bytes = gridcraft_xlsx::write_xlsx(&wb).unwrap();
        let (back, _) = gridcraft_xlsx::read_xlsx(&bytes).unwrap();
        assert_eq!(back.sheet(0).unwrap().autofilter, wb.sheet(0).unwrap().autofilter);
        assert_eq!(back.sheet(0).unwrap().cells, wb.sheet(0).unwrap().cells);
    }
}

#[test]
fn command_batch_deletes_a_stored_last_worksheet_row_without_overflow() {
    let mut wb = plain_workbook();
    wb.sheet_mut(0).unwrap().cells.set(CellRef::new(MAX_ROWS - 1, 0), Cell::value(Value::text("999999999999999999")));
    let mut s = session(wb);
    let before = s.doc().unwrap().wb.clone();
    let params = json!({"rows": format!("2:2,{MAX_ROWS}:{MAX_ROWS}")});
    let areas = delete_areas(&s, &params, Axis::Rows).unwrap();
    let mut expected = (*before).clone();
    old_delete(&mut expected, &areas);
    s.execute("home.deleteRows", params).unwrap();
    assert_eq!(*s.doc().unwrap().wb, expected);
    s.execute("edit.undo", json!({})).unwrap();
    assert_eq!(*s.doc().unwrap().wb, *before);
    s.execute("edit.redo", json!({})).unwrap();
    assert_eq!(*s.doc().unwrap().wb, expected);
}

fn assert_rejected(label: &str, change: impl FnOnce(&mut Workbook)) {
    let mut wb = plain_workbook();
    change(&mut wb);
    assert!(!can_batch_delete_rows(&wb, 0, &[(3, 1), (1, 1)]), "unsupported {label} must retain the general path");
}

fn chart() -> Chart {
    Chart {
        id: 1,
        kind: ChartKind::default(),
        anchor: Anchor::default(),
        title: None,
        series: vec![],
        legend: LegendPos::default(),
        data_labels: false,
        gridlines: false,
        style: 1,
        x_title: None,
        y_title: None,
        source: None,
        by_rows: false,
        pivot: None,
    }
}

fn table() -> Table {
    Table {
        id: 1,
        name: "T".into(),
        range: RangeRef::parse("A1:B5").unwrap(),
        header_row: true,
        totals_row: false,
        columns: vec![],
        style: String::new(),
        banded_rows: false,
        banded_cols: false,
        first_col: false,
        last_col: false,
        filter_button: false,
        filter: vec![],
        table_type: None,
        connection_id: None,
    }
}

fn opaque() -> Arc<Preserved> {
    Arc::new(Preserved { elements: vec![PreservedXml { slot: "ext".into(), xml: "<ext/>".into() }], ..Default::default() })
}

#[test]
fn eligibility_rejects_each_reference_host_even_on_another_sheet() {
    assert_rejected("defined name", |wb| {
        wb.names.push(DefinedName { name: "N".into(), scope: None, formula: "Sheet1!A4".into(), comment: String::new(), hidden: false })
    });
    assert_rejected("VBA", |wb| wb.vba_project = Some(vec![]));
    assert_rejected("workbook opaque XML", |wb| wb.preserved = Some(opaque()));
    for sheet in [0, 1] {
        assert_rejected("formula", |wb| wb.sheet_mut(sheet).unwrap().cells.set(CellRef::new(1, 1), Cell::formula(Formula::new("=Sheet1!A4"))));
        assert_rejected("unparseable formula", |wb| wb.sheet_mut(sheet).unwrap().cells.set(CellRef::new(1, 1), Cell::formula(Formula::new("=)"))));
        assert_rejected("array value", |wb| {
            wb.sheet_mut(sheet)
                .unwrap()
                .cells
                .set(CellRef::new(1, 1), Cell::value(Value::Array(Arc::new(gridcraft_core::Array::scalar(Value::Empty)))))
        });
        assert_rejected("spill values", |wb| {
            wb.sheet_mut(sheet).unwrap().spill.insert(CellRef::new(1, 1), Value::Number(1.0));
        });
        assert_rejected("spill ranges", |wb| {
            wb.sheet_mut(sheet).unwrap().spill_ranges.insert(CellRef::new(1, 1), RangeRef::parse("B2:B3").unwrap());
        });
        assert_rejected("table", |wb| wb.sheet_mut(sheet).unwrap().tables.push(table()));
        assert_rejected("CF", |wb| {
            wb.sheet_mut(sheet).unwrap().cond_formats.push(CondFormat {
                ranges: vec![],
                rule: CfRule::Expression { formula: "Sheet1!A4".into(), style: Box::default() },
                priority: 0,
                stop_if_true: false,
            })
        });
        assert_rejected("validation", |wb| wb.sheet_mut(sheet).unwrap().validations.push(Validation::default()));
        assert_rejected("chart", |wb| wb.sheet_mut(sheet).unwrap().charts.push(chart()));
        assert_rejected("sparkline", |wb| {
            wb.sheet_mut(sheet).unwrap().sparklines.push(Sparkline {
                cell: CellRef::default(),
                source: "Sheet1!A1:A4".into(),
                kind: SparklineKind::default(),
                color: Color::Auto,
                markers: false,
            })
        });
        assert_rejected("pivot", |wb| wb.sheet_mut(sheet).unwrap().pivots.push(PivotTable::default()));
        assert_rejected("slicer", |wb| {
            wb.sheet_mut(sheet).unwrap().slicers.push(Slicer {
                id: 1,
                table: "T".into(),
                column: "C".into(),
                caption: String::new(),
                anchor: Anchor::default(),
                selected: None,
                columns: 1,
            })
        });
        assert_rejected("hyperlink", |wb| {
            wb.sheet_mut(sheet)
                .unwrap()
                .hyperlinks
                .insert(CellRef::default(), Hyperlink { target: "Sheet1!A4".into(), tooltip: None, external: false });
        });
        assert_rejected("sheet opaque XML", |wb| wb.sheet_mut(sheet).unwrap().preserved = Some(opaque()));
    }
}

#[test]
fn eligibility_rejects_each_unmapped_target_feature_and_the_filter_header() {
    assert_rejected("merge", |wb| wb.sheet_mut(0).unwrap().merges.push(RangeRef::parse("A2:B2").unwrap()));
    assert_rejected("cell picture", |wb| {
        wb.sheet_mut(0)
            .unwrap()
            .cell_pictures
            .insert(CellRef::default(), Arc::new(CellPicture { data: vec![], mime: "image/png".into(), alt: String::new() }));
    });
    assert_rejected("comment", |wb| {
        wb.sheet_mut(0).unwrap().comments.insert(
            CellRef::default(),
            Comment { author: String::new(), text: String::new(), replies: vec![], threaded: false, resolved: false, visible: false, dates: vec![] },
        );
    });
    assert_rejected("image", |wb| {
        wb.sheet_mut(0).unwrap().images.push(Image { id: 1, anchor: Anchor::default(), data: vec![], mime: "image/png".into(), alt: String::new() })
    });
    assert_rejected("shape", |wb| {
        wb.sheet_mut(0).unwrap().shapes.push(Shape {
            id: 1,
            kind: ShapeKind::default(),
            anchor: Anchor::default(),
            fill: Color::Auto,
            line: Color::Auto,
            text: String::new(),
            format: ShapeFormat::default(),
        })
    });
    assert_rejected("edit range", |wb| {
        wb.sheet_mut(0).unwrap().edit_ranges.push(EditRange { title: "E".into(), ranges: vec![], password: None, security_descriptor: None })
    });
    assert_rejected("protection", |wb| wb.sheet_mut(0).unwrap().protection = Some(SheetProtection::default()));
    assert_rejected("freeze", |wb| wb.sheet_mut(0).unwrap().freeze = Some((1, 0)));
    assert_rejected("print area", |wb| wb.sheet_mut(0).unwrap().print.print_area = Some(RangeRef::parse("A1:B10").unwrap()));
    assert_rejected("row titles", |wb| wb.sheet_mut(0).unwrap().print.title_rows = Some((0, 1)));
    assert_rejected("column titles", |wb| wb.sheet_mut(0).unwrap().print.title_cols = Some((0, 1)));
    assert_rejected("row breaks", |wb| wb.sheet_mut(0).unwrap().print.row_breaks.push(10));
    assert_rejected("column breaks", |wb| wb.sheet_mut(0).unwrap().print.col_breaks.push(1));
    for rows in [true, false] {
        for collapsed in [true, false] {
            assert_rejected("outline/collapse", |wb| {
                let sh = wb.sheet_mut(0).unwrap();
                let lines = if rows { &mut sh.rows } else { &mut sh.cols };
                lines.insert(1, LineInfo { outline: u8::from(!collapsed), collapsed, ..Default::default() });
            });
        }
    }
    assert!(!can_batch_delete_rows(&plain_workbook(), 0, &[(0, 1), (3, 1)]));
    assert!(!can_batch_delete_rows(&plain_workbook(), 99, &[(1, 1), (3, 1)]));
}

#[test]
fn ineligible_cross_sheet_formula_uses_the_general_reference_rewrite() {
    let mut wb = plain_workbook();
    wb.sheet_mut(1).unwrap().cells.set(CellRef::new(0, 0), Cell::formula(Formula::new("=Sheet1!A4")));
    let mut s = session(wb);
    s.execute("home.deleteRows", json!({"rows": "2:2,4:4"})).unwrap();
    assert_eq!(s.doc().unwrap().wb.sheet(1).unwrap().cell(CellRef::new(0, 0)).unwrap().formula.as_ref().unwrap().text, "#REF!");
}

#[test]
fn only_canonical_file_version_metadata_is_allowed_and_preserved() {
    let xml =
        "<fileVersion appName=\"GridCraft &amp; Co\" lastEdited=\"7\" lowestEdited=\"7\" rupBuild=\"123\" codeName=\"a&amp;b&#9;&quot;&lt;&gt;\"/>";
    for xml in ["<fileVersion/>", xml] {
        assert!(plain_file_version_xml(xml));
    }
    for xml in [
        "<fileVersion ref=\"A1\"/>",
        "<fileVersion><ref>A1</ref></fileVersion>",
        "<fileVersion/><ext/>",
        "<fileVersion appName=\"a\" appName=\"b\"/>",
        "<fileVersion appName=\"a<ref>\"/>",
        "<fileVersion appName=\"a&unknown;\"/>",
        "<fileVersion appName=\"a&\"/>",
        "<fileVersion appName=\"a\n\"/>",
        "<fileVersion appName=\"a\u{0}\"/>",
        "<fileVersion appName='a'/>",
        "<fileVersion appName=\"unclosed/>",
        "<fileVersion></fileVersion>",
    ] {
        assert!(!plain_file_version_xml(xml), "{xml:?}");
        assert_rejected("noncanonical fileVersion", |wb| {
            wb.preserved =
                Some(Arc::new(Preserved { elements: vec![PreservedXml { slot: "fileVersion".into(), xml: xml.into() }], ..Default::default() }));
        });
    }
    let kept = Arc::new(Preserved { elements: vec![PreservedXml { slot: "fileVersion".into(), xml: xml.into() }], ..Default::default() });
    for field in 0..7 {
        let mut p = (*kept).clone();
        match field {
            0 => p.parts.push(PreservedPart::default()),
            1 => p.rels.push(PreservedRel::default()),
            2 => p.package_rels.push(PreservedRel::default()),
            3 => p.vml = Some(PreservedPart::default()),
            4 => p.sheets.push(PreservedSheet::default()),
            5 => p.elements.push(PreservedXml { slot: "ext".into(), xml: "<ext/>".into() }),
            _ => p.elements.push(p.elements[0].clone()),
        }
        assert_rejected("fileVersion plus opaque content", |wb| wb.preserved = Some(Arc::new(p)));
    }
    assert_rejected("fileVersion in a sheet slot", |wb| wb.sheet_mut(0).unwrap().preserved = Some(kept.clone()));
    let mut wb = plain_workbook();
    wb.preserved = Some(kept.clone());
    assert!(can_batch_delete_rows(&wb, 0, &[(3, 1), (1, 1)]));
    let mut s = session(wb);
    s.execute("home.deleteRows", json!({"rows": "2:2,4:4"})).unwrap();
    assert!(Arc::ptr_eq(s.doc().unwrap().wb.preserved.as_ref().unwrap(), &kept));
    s.execute("edit.undo", json!({})).unwrap();
    assert!(Arc::ptr_eq(s.doc().unwrap().wb.preserved.as_ref().unwrap(), &kept));
    s.execute("edit.redo", json!({})).unwrap();
    assert!(Arc::ptr_eq(s.doc().unwrap().wb.preserved.as_ref().unwrap(), &kept));
    let bytes = gridcraft_xlsx::write_xlsx(&s.doc().unwrap().wb).unwrap();
    let (back, _) = gridcraft_xlsx::read_xlsx(&bytes).unwrap();
    assert_eq!(back.preserved, Some(kept));
}

#[test]
fn eligibility_rejects_invalid_line_keys_and_inverted_filter_ranges() {
    assert_rejected("out-of-bounds row metadata", |wb| {
        wb.sheet_mut(0).unwrap().rows.insert(MAX_ROWS, LineInfo::default());
    });
    assert_rejected("out-of-bounds column metadata", |wb| {
        wb.sheet_mut(0).unwrap().cols.insert(MAX_COLS, LineInfo::default());
    });
    assert_rejected("inverted filter rows", |wb| {
        wb.sheet_mut(0).unwrap().autofilter.as_mut().unwrap().range = RangeRef { start: CellRef::new(9, 0), end: CellRef::new(4, 1) };
    });
    assert_rejected("inverted filter columns", |wb| {
        wb.sheet_mut(0).unwrap().autofilter.as_mut().unwrap().range = RangeRef { start: CellRef::new(0, 1), end: CellRef::new(130, 0) };
    });

    // The general path can move an invalid row key back into the worksheet. Qualification
    // must decline this malformed input, rather than silently discarding it via the rank map.
    let mut wb = plain_workbook();
    wb.sheet_mut(0).unwrap().rows.insert(MAX_ROWS, LineInfo { hidden: true, ..Default::default() });
    let mut expected = wb.clone();
    old_delete(&mut expected, &[(3, 1), (1, 1)]);
    assert!(expected.sheet(0).unwrap().rows.contains_key(&(MAX_ROWS - 2)));
    let mut s = session(wb);
    s.execute("home.deleteRows", json!({"rows": "2:2,4:4"})).unwrap();
    assert_eq!(*s.doc().unwrap().wb, expected);
}
