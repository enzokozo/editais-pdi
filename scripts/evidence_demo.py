"""Check evidence quotes against a real extracted call.

Usage:
  python scripts/evidence_demo.py FAPEMIG-116 "a quote copied from the PDF" ["another quote" ...]
"""
import argparse
import csv
import json

from editais.config import DATA_DIR, MAIN_DOCS_CSV
from editais.evidence import check_evidence, index_document


def load_call(edital_id: str) -> dict:
    with open(MAIN_DOCS_CSV, newline="", encoding="utf-8-sig") as f:
        row = next((r for r in csv.DictReader(f) if r["ID_edital"] == edital_id), None)
    if row is None:
        raise SystemExit(f"{edital_id} not in {MAIN_DOCS_CSV.name}")
    return json.loads((DATA_DIR / "extracted" / row["arquivo_json"]).read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("edital_id")
    ap.add_argument("quotes", nargs="+")
    args = ap.parse_args()

    doc = index_document(load_call(args.edital_id))
    annex = "no annex found" if doc.annex_start is None else f"annex starts at line {doc.annex_start}"
    print(f"{args.edital_id}: {len(doc.starts)} lines, {annex}")
    for q in args.quotes:
        c = check_evidence(q, None, doc)
        print(f"[{'OK' if c.ok else 'NO'}] page={c.page} region={c.region} | {c.reason} | {q[:70]!r}")


if __name__ == "__main__":
    main()
