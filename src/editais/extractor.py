#!/usr/bin/env python3
"""Etapa 3 - extracao de texto dos PDFs baixados (somente PDFs com texto selecionavel; sem OCR).

Entrada : data/manifest_downloads.csv (usa linhas com status 'ok' ou 'ja_existente')
Saida   : data/extracted/<sha256[:16]>.json   (um por conteudo unico; texto por pagina + linhas
                                               com tamanho de fonte/negrito, para a Etapa 4)
          data/extraction_manifest.csv        (uma linha por anexo, acrescentada a cada execucao)

Status de extracao (campo status_extracao):
  ok             - texto presente em quase todas as paginas;
  texto_parcial  - parte das paginas sem texto (possiveis paginas digitalizadas ou so imagem);
  sem_texto      - nenhuma pagina com texto (provavel PDF digitalizado -> exigira OCR);
  falha_abertura - o arquivo nao abriu (corrompido/protegido).
IMPORTANTE: sem_texto / falha_abertura significam "NAO VERIFICADO", nunca "topico ausente".

Limiares (hipoteses de trabalho, ajustaveis): pagina "com texto" = >= MIN_CHARS caracteres;
'ok' = menos de MAX_PCT_SEM_TEXTO das paginas sem texto.

Uso:
  python -m editais.extractor --ids FAPEMIG-195,FAPEMIG-116
  python -m editais.extractor                    # todos os PDFs do manifesto
"""
import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

from editais.config import PROJECT_ROOT

MIN_CHARS = 30
MAX_PCT_SEM_TEXTO = 0.20
MANIFEST_FIELDS = [
    "ID_edital", "id_anexo", "sha256", "json_path", "n_paginas", "n_caracteres",
    "paginas_sem_texto", "pct_sem_texto", "status_extracao", "erro", "reaproveitado",
    "backend", "extraido_em",
]


# ----------------------------------------------------------------------------- backend
def extract_pymupdf(path: Path) -> dict:
    """Backend padrao. Devolve estrutura neutra: n_pages, metadata, pages[{page,text,lines,n_images}]."""
    try:
        import pymupdf as fitz  # PyMuPDF >= 1.24.3
    except ImportError:
        import fitz  # nomes antigos
    pages = []
    with fitz.open(path) as doc:
        if doc.needs_pass:
            raise ValueError("PDF protegido por senha")
        meta = {k: (doc.metadata or {}).get(k) for k in ("title", "producer", "creator", "creationDate")}
        for i, page in enumerate(doc, start=1):
            lines = []
            for block in page.get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                for ln in block["lines"]:
                    spans = [s for s in ln["spans"] if s["text"].strip()]
                    if not spans:
                        continue
                    lines.append({
                        "t": "".join(s["text"] for s in ln["spans"]).strip(),
                        "size": round(max(s["size"] for s in spans), 1),
                        "bold": any((s["flags"] & 16) or "bold" in s["font"].lower() for s in spans),
                        "y0": round(ln["bbox"][1], 1),
                    })
            pages.append({"page": i, "text": page.get_text("text"), "lines": lines,
                          "n_images": len(page.get_images())})
        return {"n_pages": len(pages), "metadata": meta, "pages": pages,
                "backend_version": getattr(fitz, "__doc__", "").split("\n")[0][:60]}


BACKEND = extract_pymupdf  # substituivel em testes


# ----------------------------------------------------------------------------- nucleo
def summarize(result: dict) -> dict:
    chars = [len((p["text"] or "").strip()) for p in result["pages"]]
    sem = [p["page"] for p, c in zip(result["pages"], chars) if c < MIN_CHARS]
    n = max(result["n_pages"], 1)
    pct = len(sem) / n
    if len(sem) == result["n_pages"]:
        status = "sem_texto"
    elif pct >= MAX_PCT_SEM_TEXTO:
        status = "texto_parcial"
    else:
        status = "ok"
    return {"n_paginas": result["n_pages"], "n_caracteres": sum(chars), "paginas_sem_texto": sem,
            "pct_sem_texto": round(pct, 3), "status_extracao": status}


def process_pdf(pdf_path: Path, sha: str, out_dir: Path):
    """Devolve (resumo, caminho_json, reaproveitado, erro)."""
    json_path = out_dir / f"{sha[:16]}.json"
    if json_path.exists():  # nunca sobrescreve; conteudo identico ja extraido
        doc = json.loads(json_path.read_text(encoding="utf-8"))
        return doc["summary"], json_path, True, ""
    try:
        result = BACKEND(pdf_path)
    except Exception as e:  # PDF corrompido, senha, etc.
        return ({"n_paginas": 0, "n_caracteres": 0, "paginas_sem_texto": [], "pct_sem_texto": "",
                 "status_extracao": "falha_abertura"}, None, False, f"{type(e).__name__}: {e}")
    summary = summarize(result)
    doc = {"sha256": sha, "extracted_at": datetime.now().isoformat(timespec="seconds"),
           "source_example": str(pdf_path), "summary": summary,
           "metadata": result["metadata"], "pages": result["pages"]}
    json_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return summary, json_path, False, ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(PROJECT_ROOT))
    ap.add_argument("--ids", help="ID_edital separados por virgula (default: todos do manifesto)")
    args = ap.parse_args()

    root = Path(args.root)
    dl_manifest = root / "data" / "manifest_downloads.csv"
    if not dl_manifest.exists():
        sys.exit("manifest_downloads.csv nao encontrado: rode o downloader.py primeiro.")
    ids = {x.strip() for x in args.ids.split(",")} if args.ids else None

    with open(dl_manifest, newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f)
                if r["status"] in ("ok", "ja_existente") and (ids is None or r["ID_edital"] in ids)]
    # uma linha por (ID_edital, id_anexo): se houve varias tentativas, vale a ultima
    rows = list({(r["ID_edital"], r["id_anexo"]): r for r in rows}.values())

    out_dir = root / "data" / "extracted"
    out_dir.mkdir(parents=True, exist_ok=True)
    ex_manifest = root / "data" / "extraction_manifest.csv"
    new = not ex_manifest.exists()
    counts = {}
    with open(ex_manifest, "a", newline="", encoding="utf-8-sig") as mf:
        w = csv.DictWriter(mf, fieldnames=MANIFEST_FIELDS)
        if new:
            w.writeheader()
        for i, r in enumerate(rows, 1):
            pdf = Path(r["arquivo_local"])
            if not pdf.is_absolute():
                pdf = Path.cwd() / pdf
            summary, jpath, reused, err = process_pdf(pdf, r["sha256"], out_dir)
            rec = {"ID_edital": r["ID_edital"], "id_anexo": r["id_anexo"], "sha256": r["sha256"],
                   "json_path": str(jpath) if jpath else "", "n_paginas": summary["n_paginas"],
                   "n_caracteres": summary["n_caracteres"],
                   "paginas_sem_texto": ";".join(map(str, summary["paginas_sem_texto"])),
                   "pct_sem_texto": summary["pct_sem_texto"], "status_extracao": summary["status_extracao"],
                   "erro": err, "reaproveitado": reused, "backend": BACKEND.__name__,
                   "extraido_em": datetime.now().isoformat(timespec="seconds")}
            w.writerow(rec)
            mf.flush()
            counts[rec["status_extracao"]] = counts.get(rec["status_extracao"], 0) + 1
            tag = " (conteudo ja extraido)" if reused else ""
            print(f"[{i}/{len(rows)}] {rec['status_extracao']:<15} {rec['n_paginas']:>3} pag  "
                  f"{rec['n_caracteres']:>7} car  {r['ID_edital']} {Path(r['arquivo_local']).name}{tag}")
    print("\nResumo:", counts)
    print(f"Manifesto de extracao: {ex_manifest}")


if __name__ == "__main__":
    main()
