"""Re-extract known calls with the installed PyMuPDF and compare with the stored text (D33).

The stored JSONs in data/extracted were produced with PyMuPDF 1.x. This checks whether the
installed version produces the same text and, above all, the same rule-based results.

Usage:
  python scripts/check_pymupdf.py FAPEMIG-192 FAPEMIG-116 FAPEMIG-132
"""
import argparse
import difflib
from importlib import metadata

from editais.config import DATA_DIR
from editais.corpus import load_call
from editais.evidence import index_document, normalize
from editais.extractor import extract_pymupdf
from editais.rules import extract_with_rules


def page_similarity(old: dict, new: dict) -> list[float]:
    return [difflib.SequenceMatcher(None, normalize(a["text"] or ""), normalize(b["text"] or ""),
                                    autojunk=False).ratio()
            for a, b in zip(old["pages"], new["pages"])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="+")
    args = ap.parse_args()
    print(f"installed pymupdf {metadata.version('pymupdf')}")

    for edital_id in args.ids:
        row, old = load_call(edital_id)
        pdfs = sorted((DATA_DIR / "pdfs" / edital_id).glob(f"{row['id_anexo']}_*.pdf"))
        if not pdfs:
            print(f"{edital_id}: PDF for attachment {row['id_anexo']} not found")
            continue
        new = extract_pymupdf(pdfs[0])
        sims = page_similarity(old, new)
        rules_old = {r.field_id: (r.status, r.value) for r in extract_with_rules(index_document(old))}
        rules_new = {r.field_id: (r.status, r.value) for r in extract_with_rules(index_document(new))}
        changed = [f for f in rules_old if rules_old[f] != rules_new[f]]
        print(f"{edital_id}: pages {len(old['pages'])} -> {len(new['pages'])} | "
              f"text similarity min {min(sims):.3f}, mean {sum(sims) / len(sims):.3f} | "
              f"rule results changed: {changed or 'none'}")
        for f in changed:
            print(f"   {f}: {rules_old[f]} -> {rules_new[f]}")


if __name__ == "__main__":
    main()
