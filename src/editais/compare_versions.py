#!/usr/bin/env python3
"""Compara duas versoes do texto de um edital (ex.: original x retificada) a partir do JSON extraido.

v2: compara PALAVRAS (nao linhas) depois de normalizar o texto (textnorm.py): corrige ligaduras
mal extraidas, remove rodape do SEI e ignora onde as linhas quebram. A v1 comparava linhas
brutas e superestimava as diferencas (ex.: similaridade 0,42 entre versoes quase iguais).

Serve para (a) decidir qual PDF e a versao mais recente do texto integral e (b) ver o que uma
retificacao realmente altera. Compara TEXTO; mudancas de layout, tabelas-imagem etc. nao aparecem.

Uso:
  python -m editais.compare_versions FAPEMIG-116 828 842
  python -m editais.compare_versions FAPEMIG-132 927 929 --max 80 --out data/diff_132_927_929.txt
"""
import argparse
import csv
import difflib
import sys
from pathlib import Path

from editais import textnorm
from editais.config import PROJECT_ROOT


def find_json(root: Path, id_edital: str, id_anexo: str) -> str:
    manifest = root / "data" / "extraction_manifest.csv"
    with open(manifest, newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r["ID_edital"] == id_edital and r["id_anexo"] == str(id_anexo)]
    if not rows or not rows[-1]["json_path"]:
        sys.exit(f"Sem extracao para {id_edital} / anexo {id_anexo} em {manifest}")
    jp = rows[-1]["json_path"].replace("\\", "/")
    return jp if Path(jp).exists() else str(root / jp)


def short(words, limit=60):
    s = " ".join(words)
    return s if len(words) <= limit else " ".join(words[:limit]) + f" [...+{len(words) - limit} palavras]"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id_edital")
    ap.add_argument("anexo_a")
    ap.add_argument("anexo_b")
    ap.add_argument("--root", default=str(PROJECT_ROOT))
    ap.add_argument("--max", type=int, default=40, help="maximo de trechos alterados a mostrar")
    ap.add_argument("--out", help="grava o relatorio tambem neste arquivo")
    args = ap.parse_args()
    root = Path(args.root)

    docA, linesA, infoA = textnorm.load_doc(find_json(root, args.id_edital, args.anexo_a))
    docB, linesB, infoB = textnorm.load_doc(find_json(root, args.id_edital, args.anexo_b))
    ta, pa = textnorm.tokens(linesA)
    tb, pb = textnorm.tokens(linesB)

    out = [f"{args.id_edital}: anexo {args.anexo_a} ({len(ta)} palavras, {len(docA['pages'])} pags) "
           f"x anexo {args.anexo_b} ({len(tb)} palavras, {len(docB['pages'])} pags)"]
    for tag, info in (("A", infoA), ("B", infoB)):
        if info and info["datas"]:
            out.append(f"Rodape SEI {tag}: data/hora {info['datas'][0]} | doc SEI {info['numeros'][0]}")
    if docA["sha256"] == docB["sha256"]:
        out.append("Conteudo IDENTICO (mesmo hash).")

    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    ops = [o for o in sm.get_opcodes() if o[0] != "equal"]
    removed = sum(i2 - i1 for _, i1, i2, _, _ in ops)
    added = sum(j2 - j1 for _, _, _, j1, j2 in ops)
    out.append(f"Similaridade (palavras): {sm.ratio():.3f}")
    out.append(f"Trechos alterados: {len(ops)} | palavras removidas: {removed} | adicionadas: {added}\n")

    for tag, i1, i2, j1, j2 in ops[: args.max]:
        page_a = pa[i1] if i1 < len(pa) else (pa[-1] if pa else "-")
        page_b = pb[j1] if j1 < len(pb) else (pb[-1] if pb else "-")
        ctx = " ".join(ta[max(0, i1 - 6): i1])
        out.append(f"@@ {tag}  (A p.{page_a} / B p.{page_b})  contexto: ...{ctx}")
        if i2 > i1:
            out.append(f"  - {short(ta[i1:i2])}")
        if j2 > j1:
            out.append(f"  + {short(tb[j1:j2])}")
    if len(ops) > args.max:
        out.append(f"\n... {len(ops) - args.max} trechos omitidos (use --max).")

    report = "\n".join(out)
    print(report)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
