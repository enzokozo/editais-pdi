"""Rules-only extraction + consolidation for a few calls: JSON per call and the review sheet.

Usage:
  python scripts/consolidate_rules.py FAPEMIG-192 FAPEMIG-116 FAPEMIG-132
"""
import argparse

from editais.config import OUTPUT_DIR
from editais.consolidation import check_consistency, review_rows, save_json, write_review_sheet
from editais.corpus import load_call
from editais.evidence import index_document
from editais.rules import extract_with_rules
from editais.schema import EditalExtraction


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="+")
    args = ap.parse_args()

    all_rows = []
    for edital_id in args.ids:
        row, doc = load_call(edital_id)
        ext = EditalExtraction(
            edital_id=edital_id, source=edital_id.split("-")[0].lower(),
            document_id=row["id_anexo"], document_hash=doc["sha256"],
            fields=extract_with_rules(index_document(doc)))
        issues = check_consistency(ext)
        out = save_json(ext, issues, OUTPUT_DIR / "extractions")
        rows = review_rows(ext, issues)
        all_rows += rows
        pending = sum(1 for r in rows if r["Pendencia"])
        print(f"{edital_id}: {18 - pending} fields without pending, {pending} pending, "
              f"{len(issues)} consistency issues -> {out.name}")
        for i in issues:
            print(f"   ! {i.field_id}: {i.reason}")

    sheet = OUTPUT_DIR / "revisao_campos.xlsx"
    backup = write_review_sheet(all_rows, sheet)
    print(f"review sheet: {sheet}" + (f" (backup: {backup.name})" if backup else ""))


if __name__ == "__main__":
    main()
