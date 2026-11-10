from openpyxl import load_workbook

from editais.consolidation import (SHEET, check_consistency, review_rows, save_json,
                                   write_review_sheet)
from editais.schema import FIELDS, EditalExtraction, Evidence, FieldRecord

EV = [Evidence(text="x", page=1, region="corpo")]


def extraction(**values) -> EditalExtraction:
    """values: field_id -> normalized number as text; other fields are incerto."""
    fields = []
    for fid in FIELDS:
        if fid in values:
            v = values[fid]
            value = [v] if FIELDS[fid].multivalued else v
            fields.append(FieldRecord(field_id=fid, status="extraido", value=value, normalized=v,
                                      evidence=EV, method="regra"))
        else:
            fields.append(FieldRecord(field_id=fid, status="incerto", note="rules: no match"))
    return EditalExtraction(edital_id="TEST-1", source="test", document_id="1", document_hash="h", fields=fields)


def test_consistent_budget_has_no_issues():
    assert check_consistency(extraction(L1="1000.00", L2="10.00", L3="100.00", P4="24")) == []


def test_min_above_max_and_max_above_global_are_flagged():
    issues = check_consistency(extraction(L1="50.00", L2="200.00", L3="100.00"))
    assert {i.field_id for i in issues} == {"L2", "L3"}


def test_out_of_range_months_is_flagged():
    assert [i.field_id for i in check_consistency(extraction(P4="240"))] == ["P4"]


def test_missing_numbers_are_not_flagged():
    assert check_consistency(extraction(L2="200.00")) == []


def test_review_rows_cover_all_fields_and_mark_pending():
    rows = review_rows(extraction(L1="1000.00"), [])
    assert len(rows) == 18
    by_field = {r["Campo"]: r for r in rows}
    assert by_field["L1"]["Pendencia"] is None
    assert by_field["T2"]["Pendencia"].startswith("incerto")


def test_review_sheet_keeps_decision_when_value_is_unchanged(tmp_path):
    path = tmp_path / "revisao.xlsx"
    ext = extraction(L1="1000.00")
    write_review_sheet(review_rows(ext, []), path)

    wb = load_workbook(path)
    ws = wb[SHEET]
    head = [c.value for c in ws[1]]
    l1_row = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, head.index("Campo") + 1).value == "L1")
    ws.cell(l1_row, head.index("Decisao") + 1, "Correto")
    wb.save(path)

    backup = write_review_sheet(review_rows(ext, []), path)
    assert backup is not None and backup.exists()
    ws = load_workbook(path)[SHEET]
    assert ws.cell(l1_row, head.index("Decisao") + 1).value == "Correto"


def test_review_sheet_reopens_decision_when_value_changes(tmp_path):
    path = tmp_path / "revisao.xlsx"
    write_review_sheet(review_rows(extraction(L1="1000.00"), []), path)
    wb = load_workbook(path)
    ws = wb[SHEET]
    head = [c.value for c in ws[1]]
    col = {h: i + 1 for i, h in enumerate(head)}
    row = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, col["Campo"]).value == "L1")
    ws.cell(row, col["Decisao"], "Correto")
    wb.save(path)

    write_review_sheet(review_rows(extraction(L1="2000.00"), []), path)
    ws = load_workbook(path)[SHEET]
    assert ws.cell(row, col["Decisao"]).value is None
    assert "valor mudou" in ws.cell(row, col["Pendencia"]).value


def test_save_json_includes_issues(tmp_path):
    ext = extraction(L1="50.00", L3="100.00")
    out = save_json(ext, check_consistency(ext), tmp_path)
    assert '"issues"' in out.read_text(encoding="utf-8")


def test_review_sheet_keeps_calls_not_in_this_run(tmp_path):
    path = tmp_path / "revisao.xlsx"
    a = extraction(L1="1000.00")
    b = extraction(L1="5.00").model_copy(update={"edital_id": "TEST-2"})
    write_review_sheet(review_rows(a, []) + review_rows(b, []), path)
    write_review_sheet(review_rows(a, []), path)      # second run processes only TEST-1
    ids = {r[0] for r in load_workbook(path)[SHEET].iter_rows(min_row=2, values_only=True)}
    assert ids == {"TEST-1", "TEST-2"}
