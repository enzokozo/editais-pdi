"""Consolidation agent (deterministic part): consistency checks and the human review sheet.

The review sheet (output/revisao_campos.xlsx, tab "Revisao") lists all 18 fields of every
call. The reviewer marks Correto / Corrigido / Nao encontrado; the TCH metric is computed
from it (decision D15). Rewriting the sheet never erases a decision whose value is unchanged.
"""
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from editais.schema import FIELDS, EditalExtraction, Status

SHEET = "Revisao"
DECISIONS = ("Correto", "Corrigido", "Nao encontrado")
COLUMNS = ["ID_edital", "Campo", "Nome", "Status", "Valor", "Normalizado", "Evidencia", "Pagina",
           "Regiao", "Metodo", "Pendencia", "Decisao", "Valor_corrigido", "Observacao"]
REVIEWER_COLUMNS = ("Decisao", "Valor_corrigido", "Observacao")

# Plausible ranges (inclusive) for normalized single values; outside them -> review.
RANGES = {"P4": (1, 60), "E5": (1, 60), "L4": (0, 100)}


@dataclass
class Issue:
    field_id: str
    reason: str


def _number(ext: EditalExtraction, field_id: str) -> float | None:
    rec = next(r for r in ext.fields if r.field_id == field_id)
    try:
        return float(rec.normalized) if rec.normalized is not None else None
    except ValueError:
        return None


def check_consistency(ext: EditalExtraction) -> list[Issue]:
    issues = []
    lo, hi, total = _number(ext, "L2"), _number(ext, "L3"), _number(ext, "L1")
    if lo is not None and hi is not None and lo > hi:
        issues.append(Issue("L2", f"minimo ({lo:,.2f}) maior que o maximo ({hi:,.2f})"))
    if hi is not None and total is not None and hi > total:
        issues.append(Issue("L3", f"maximo por proposta ({hi:,.2f}) maior que o valor global ({total:,.2f})"))
    for fid, (a, b) in RANGES.items():
        v = _number(ext, fid)
        if v is not None and not a <= v <= b:
            issues.append(Issue(fid, f"valor {v:g} fora da faixa plausivel [{a}, {b}]"))
    return issues


def pending_reason(status: Status, note: str | None, issues: list[str]) -> str:
    reasons = list(issues)
    if status == Status.INCERTO:
        reasons.insert(0, f"incerto ({note})" if note else "incerto")
    elif status == Status.FALHA:
        reasons.insert(0, f"falha ({note})" if note else "falha")
    return "; ".join(reasons)


def review_rows(ext: EditalExtraction, issues: list[Issue]) -> list[dict]:
    by_field = {}
    for i in issues:
        by_field.setdefault(i.field_id, []).append(i.reason)
    rows = []
    for rec in ext.fields:
        ev = rec.evidence[0] if rec.evidence else None
        value = " | ".join(rec.value) if isinstance(rec.value, list) else rec.value
        rows.append({
            "ID_edital": ext.edital_id, "Campo": rec.field_id, "Nome": FIELDS[rec.field_id].name,
            "Status": rec.status.value, "Valor": value, "Normalizado": rec.normalized,
            "Evidencia": ev.text if ev else None, "Pagina": ev.page if ev else None,
            "Regiao": ev.region if ev else None, "Metodo": rec.method.value if rec.method else None,
            "Pendencia": pending_reason(rec.status, rec.note, by_field.get(rec.field_id, [])) or None,
        })
    return rows


def _previous_decisions(path: Path) -> dict:
    """(ID_edital, Campo) -> reviewer columns + the value they reviewed."""
    if not path.exists():
        return {}
    ws = load_workbook(path)[SHEET]
    head = [c.value for c in ws[1]]
    out = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        r = dict(zip(head, row))
        if any(r.get(c) for c in REVIEWER_COLUMNS):
            out[(r["ID_edital"], r["Campo"])] = r
    return out


def write_review_sheet(rows: list[dict], path: Path) -> Path | None:
    """Write the review sheet, keeping earlier decisions. Returns the backup path, if any."""
    previous = _previous_decisions(path)
    backup = None
    if path.exists():
        backup = path.parent / "backups" / f"{path.stem}_{datetime.now():%Y%m%dT%H%M%S}{path.suffix}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)

    for row in rows:
        old = previous.get((row["ID_edital"], row["Campo"]))
        if not old:
            continue
        if old.get("Valor") == row["Valor"]:
            for c in REVIEWER_COLUMNS:
                row[c] = old.get(c)
        else:  # the system changed its answer: the old decision no longer applies
            note = f"revisao anterior: {old.get('Decisao')} (valor era {old.get('Valor')!r})"
            row["Observacao"] = note
            row["Pendencia"] = "; ".join(filter(None, [row["Pendencia"], "valor mudou desde a revisao"]))

    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.append(COLUMNS)
    for row in rows:
        ws.append([row.get(c) for c in COLUMNS])

    header_fill = PatternFill("solid", fgColor="DDEBF7")
    pending_fill = PatternFill("solid", fgColor="FFF2CC")
    for c in ws[1]:
        c.font, c.fill = Font(bold=True), header_fill
    pend_col = COLUMNS.index("Pendencia") + 1
    for r in range(2, ws.max_row + 1):
        for c in ws[r]:
            c.alignment = Alignment(wrap_text=True, vertical="top")
        if ws.cell(row=r, column=pend_col).value:
            for c in ws[r]:
                c.fill = pending_fill
    widths = [13, 7, 24, 13, 30, 14, 60, 7, 8, 8, 35, 15, 25, 30]
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(ord("A") + i)].width = w
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions

    dv = DataValidation(type="list", formula1=f'"{",".join(DECISIONS)}"', allow_blank=True)
    ws.add_data_validation(dv)
    col = chr(ord("A") + COLUMNS.index("Decisao"))
    dv.add(f"{col}2:{col}{max(ws.max_row, 2)}")

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return backup


def save_json(ext: EditalExtraction, issues: list[Issue], folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{ext.edital_id}.json"
    payload = ext.model_dump(mode="json") | {"issues": [i.__dict__ for i in issues]}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
