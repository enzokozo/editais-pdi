"""Check evidence quotes against a real extracted call.

Usage:
  python scripts/evidence_demo.py FAPEMIG-116 "a quote copied from the PDF" ["another quote" ...]
"""
import argparse

from editais.corpus import load_call
from editais.evidence import check_evidence, index_document


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("edital_id")
    ap.add_argument("quotes", nargs="+")
    args = ap.parse_args()

    doc = index_document(load_call(args.edital_id)[1])
    annex = "no annex found" if doc.annex_start is None else f"annex starts at line {doc.annex_start}"
    print(f"{args.edital_id}: {len(doc.starts)} lines, {annex}")
    for q in args.quotes:
        c = check_evidence(q, None, doc)
        print(f"[{'OK' if c.ok else 'NO'}] page={c.page} region={c.region} | {c.reason} | {q[:70]!r}")


if __name__ == "__main__":
    main()
