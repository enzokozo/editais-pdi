#!/usr/bin/env python3
"""Etapa 2 - download dos anexos PDF de editais selecionados.

Entrada : anexos_*.csv gerado pelo collector.py (usa o mais recente por padrao).
Saida   : data/pdfs/<ID_edital>/<id_anexo>_<nome>.pdf  +  data/manifest_downloads.csv

Garantias:
  - nunca sobrescreve: arquivo ja existente vira status 'ja_existente';
  - o manifesto e acrescentado a cada arquivo (append), entao uma interrupcao nao perde o historico;
  - valida o PDF (cabecalho %PDF-, marcador %%EOF no final, bytes == Content-Length);
  - arquivos suspeitos sao mantidos com sufixo .incompleto/.invalido para auditoria;
  - arquivos identicos (SHA-256) sao marcados em 'duplicado_de', sem apagar nada.

Uso:
  python -m editais.downloader --ids FAPEMIG-195,FAPEMIG-116 --dry-run
  python -m editais.downloader --ids FAPEMIG-195,FAPEMIG-116
"""
import argparse
import csv
import glob
import hashlib
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from editais.config import PROJECT_ROOT, REQUEST_DELAY_SECONDS, USER_AGENT

CHUNK = 64 * 1024
MANIFEST_FIELDS = [
    "ID_edital", "id_anexo", "url_anexo", "arquivo_local", "http_status", "content_type",
    "bytes", "content_length_declarado", "sha256", "status", "duplicado_de", "erro", "baixado_em",
]


def safe_name(name: str) -> str:
    out = "".join(c if (c.isalnum() or c in "._-") else "_" for c in (name or "arquivo"))
    return out[:120]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def check_pdf(path: Path):
    """Verificacao estrutural minima. NAO garante que o PDF tenha texto extraivel."""
    size = path.stat().st_size
    if size == 0:
        return False, "arquivo vazio"
    with open(path, "rb") as f:
        if f.read(5) != b"%PDF-":
            return False, "cabecalho %PDF- ausente (provavel HTML/erro)"
        f.seek(max(0, size - 1024))
        if b"%%EOF" not in f.read():
            return False, "marcador %%EOF ausente nos ultimos 1024 bytes"
    return True, ""


def read_manifest(path: Path) -> list:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def append_manifest(path: Path, rec: dict):
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        if new:
            w.writeheader()
        w.writerow(rec)


def download_one(session, row: dict, pdf_root: Path, max_bytes: int, known_hashes: dict) -> dict:
    rec = {k: "" for k in MANIFEST_FIELDS}
    rec.update(ID_edital=row["ID_edital"], id_anexo=row["id_anexo"], url_anexo=row["url_anexo"],
               baixado_em=datetime.now().isoformat(timespec="seconds"))
    dest_dir = pdf_root / row["ID_edital"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{row['id_anexo']}_{safe_name(row['nome_original'])}"
    rec["arquivo_local"] = str(dest)

    if dest.exists():
        rec.update(status="ja_existente", bytes=dest.stat().st_size, sha256=sha256_file(dest))
        return rec

    part = dest.with_name(dest.name + ".part")
    try:
        with session.get(row["url_anexo"], stream=True, timeout=60) as r:
            rec["http_status"] = r.status_code
            rec["content_type"] = r.headers.get("Content-Type", "")
            declared = r.headers.get("Content-Length")
            rec["content_length_declarado"] = declared or ""
            if r.status_code != 200:
                rec["status"] = "falha_http"
                return rec
            if declared and int(declared) > max_bytes:
                rec["status"] = "ignorado_tamanho"
                return rec
            n = 0
            with open(part, "wb") as f:
                for chunk in r.iter_content(CHUNK):
                    n += len(chunk)
                    if n > max_bytes:
                        raise ValueError(f"excedeu o limite de {max_bytes} bytes")
                    f.write(chunk)
    except (requests.RequestException, ValueError) as e:
        rec["erro"] = f"{type(e).__name__}: {e}"
        truncated = isinstance(e, requests.exceptions.ChunkedEncodingError)
        if part.exists() and part.stat().st_size > 0:
            part.rename(dest.with_name(dest.name + ".incompleto"))
            rec["status"] = "incompleto"
        elif truncated:
            part.unlink(missing_ok=True)
            rec["status"] = "incompleto"
        else:
            part.unlink(missing_ok=True)
            rec["status"] = "falha_rede"
        return rec

    rec["bytes"] = part.stat().st_size
    if declared and int(declared) != rec["bytes"]:
        part.rename(dest.with_name(dest.name + ".incompleto"))
        rec.update(status="incompleto", erro=f"recebido {rec['bytes']} de {declared} bytes")
        return rec

    ok, motivo = check_pdf(part)
    if not ok:
        part.rename(dest.with_name(dest.name + ".invalido"))
        rec.update(status="invalido_nao_pdf", erro=motivo)
        return rec

    part.rename(dest)
    rec["sha256"] = sha256_file(dest)
    rec["status"] = "ok"
    if rec["sha256"] in known_hashes:
        rec["duplicado_de"] = known_hashes[rec["sha256"]]
    else:
        known_hashes[rec["sha256"]] = str(dest)
    return rec


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(PROJECT_ROOT))
    ap.add_argument("--anexos", help="CSV de anexos (default: o mais recente em data/snapshots)")
    ap.add_argument("--ids", help="ID_edital separados por virgula, ex.: FAPEMIG-195,FAPEMIG-116")
    ap.add_argument("--ids-file", help="arquivo texto com um ID_edital por linha")
    ap.add_argument("--ext", default="pdf", help="extensao a baixar (default: pdf)")
    ap.add_argument("--max-mb", type=float, default=25.0, help="ignora arquivos maiores que isso")
    ap.add_argument("--dry-run", action="store_true", help="so lista o que seria baixado")
    args = ap.parse_args()

    root = Path(args.root)
    ids = set()
    if args.ids:
        ids |= {x.strip() for x in args.ids.split(",") if x.strip()}
    if args.ids_file:
        ids |= {l.strip() for l in Path(args.ids_file).read_text(encoding="utf-8").splitlines() if l.strip()}
    if not ids:
        sys.exit("Informe --ids ou --ids-file (a Etapa 2 nao baixa a populacao inteira por padrao).")

    anexos_csv = args.anexos or (sorted(glob.glob(str(root / "data" / "snapshots" / "anexos_*.csv"))) or [None])[-1]
    if not anexos_csv:
        sys.exit("Nenhum anexos_*.csv encontrado. Rode o collector.py primeiro ou use --anexos.")
    with open(anexos_csv, newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r["ID_edital"] in ids and r["extensao_url"] == args.ext]

    missing = ids - {r["ID_edital"] for r in rows}
    if missing:
        print(f"AVISO: sem anexos .{args.ext} para: {sorted(missing)}")
    kb = sum(float(r["tamanho_kb_portal"] or 0) for r in rows)
    print(f"{len(rows)} arquivos .{args.ext} em {len(ids) - len(missing)} editais (~{kb/1024:.1f} MB segundo o portal)")

    if args.dry_run:
        for r in rows:
            print(f"  {r['ID_edital']:<13} {r['id_anexo']:<6} {r['tamanho_kb_portal'] or '?':>7} KB  {r['nome_original']}")
        return

    manifest = root / "data" / "manifest_downloads.csv"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    known_hashes = {m["sha256"]: m["arquivo_local"] for m in read_manifest(manifest) if m.get("sha256")}

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    counts = {}
    for i, row in enumerate(rows, 1):
        rec = download_one(session, row, root / "data" / "pdfs", int(args.max_mb * 1024 * 1024), known_hashes)
        append_manifest(manifest, rec)
        counts[rec["status"]] = counts.get(rec["status"], 0) + 1
        dup = f"  [duplicado de {rec['duplicado_de']}]" if rec["duplicado_de"] else ""
        print(f"[{i}/{len(rows)}] {rec['status']:<17} {row['ID_edital']} {row['nome_original']}{dup}")
        if rec["status"] not in ("ja_existente",) and i < len(rows):
            time.sleep(REQUEST_DELAY_SECONDS)

    print("\nResumo:", counts)
    print(f"Manifesto: {manifest}")


if __name__ == "__main__":
    main()
