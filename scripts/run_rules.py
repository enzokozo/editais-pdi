"""Run the rule-based extraction on collected calls and print one line per covered field.

Usage:
  python scripts/run_rules.py FAPEMIG-192 FAPEMIG-116 FAPEMIG-132
"""
import argparse

from editais.corpus import load_call
from editais.evidence import index_document
from editais.rules import RULES, extract_with_rules


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="+")
    args = ap.parse_args()

    for edital_id in args.ids:
        _, doc = load_call(edital_id)
        print(f"\n===== {edital_id}")
        found = 0
        for r in extract_with_rules(index_document(doc)):
            if r.field_id not in RULES:
                continue
            if r.evidence:
                found += 1
                ev = r.evidence[0]
                print(f"{r.field_id} {r.status:<9} {str(r.value):<45} -> {r.normalized} (p.{ev.page}, {ev.region})")
                print(f"     {ev.text[:150]}")
            else:
                print(f"{r.field_id} {r.status:<9} {r.note}")
        print(f"-- rules found {found}/{len(RULES)} covered fields")


if __name__ == "__main__":
    main()
