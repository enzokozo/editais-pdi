"""Access to the calls already collected: main document per call and its extracted text."""
import csv
import json

from editais.config import DATA_DIR, MAIN_DOCS_CSV


def main_documents() -> list[dict]:
    """Rows of documentos_principais.csv (one main document per call)."""
    with open(MAIN_DOCS_CSV, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_call(edital_id: str) -> tuple[dict, dict]:
    """(row of documentos_principais.csv, extracted JSON of its main document)."""
    row = next((r for r in main_documents() if r["ID_edital"] == edital_id), None)
    if row is None:
        raise KeyError(f"{edital_id} not in {MAIN_DOCS_CSV.name}")
    doc = json.loads((DATA_DIR / "extracted" / row["arquivo_json"]).read_text(encoding="utf-8"))
    return row, doc
