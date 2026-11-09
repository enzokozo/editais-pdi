#!/usr/bin/env python3
"""Etapa 1 - coleta de metadados das chamadas da FAPEMIG.

Usa o endpoint JSON que o proprio portal consome (https://fapemig.br/api/calls),
identificado no DevTools. NAO e uma API documentada: pode mudar sem aviso.

Esta etapa NAO baixa PDFs. Ela:
  1. salva o JSON bruto de cada pagina (nunca sobrescreve; nome com timestamp);
  2. gera duas tabelas planas (editais e anexos) em CSV, tambem com timestamp;
  3. imprime um diagnostico da populacao (o que sao, de fato, os "193 resultados").

Uso:
  python -m editais.fapemig_collector                      # coleta tudo (sem filtro de status)
  python -m editais.fapemig_collector --status encerrada   # so um status
  python -m editais.fapemig_collector --offline data/raw   # reprocessa JSON ja salvo, sem rede
"""
import argparse
import csv
import glob
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from editais.config import PROJECT_ROOT, REQUEST_DELAY_SECONDS, USER_AGENT

API_URL = "https://fapemig.br/api/calls"
MAX_PAGES = 50      # trava de seguranca
NA = "NAO_DISPONIVEL"

TEXT_FIELDS = [
    "descricao_chamada", "quem_pode_participar", "o_que_pode_ser_financiado",
    "requisitos_submissao", "criterios_avaliacao",
]
# Heuristica de TRIAGEM (nao de classificacao final): anexos que parecem ser so resultados.
RESULT_RE = re.compile(r"aprovad|resultado|homologa|recomendad|julgamento", re.I)


# ----------------------------------------------------------------------------- coleta
def collect(raw_dir: Path, per_page: int, status: str, max_pages: int) -> list:
    import requests

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    saved, page, total_pages = [], 1, 1
    while page <= min(total_pages, max_pages):
        params = {"page": page, "per_page": per_page, "publicacao_status": "publish"}
        if status:
            params["status"] = status
        resp = session.get(API_URL, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        total_pages = (payload.get("pagination") or {}).get("total_pages", 1)
        path = raw_dir / f"calls_{status or 'all'}_{stamp}_p{page:03d}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        saved.append(path)
        print(f"  pagina {page}/{total_pages}: {len(payload.get('data', []))} registros -> {path.name}")
        page += 1
        if page <= min(total_pages, max_pages):
            time.sleep(REQUEST_DELAY_SECONDS)
    return saved


def load_raw(paths) -> tuple:
    """Une os JSON; se um id se repetir, vale o arquivo mais recente."""
    by_id, reported_total = {}, None
    for p in sorted(paths):
        payload = json.loads(Path(p).read_text(encoding="utf-8"))
        reported_total = (payload.get("pagination") or {}).get("total", reported_total)
        for rec in payload.get("data", []):
            by_id[rec["id"]] = rec
    return list(by_id.values()), reported_total


# ----------------------------------------------------------------------------- normalizacao
def is_placeholder(text) -> bool:
    """Vazio ou so pontuacao (ex.: '.....'), que o portal usa como preenchimento."""
    return not re.search(r"\w", text or "")


def year_from(rec):
    for s in (rec.get("numero") or "", rec.get("titulo") or ""):
        m = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", s)
        if m:
            return int(m.group(1))
    return None


def file_ext(url: str) -> str:
    return Path(urlparse(url or "").path).suffix.lower().lstrip(".")


def only_results(anexos) -> bool:
    return bool(anexos) and all(
        RESULT_RE.search(f"{a.get('descricao') or ''} {a.get('nome_original') or ''}") for a in anexos
    )


def edital_row(rec, collected_at):
    anexos = rec.get("anexos") or []
    selected = lambda k: "; ".join(x["slug"] for x in (rec.get(k) or {}).get("selected", []))
    row = {
        "ID_edital": f"FAPEMIG-{rec['id']}",
        "Instituicao": "FAPEMIG",
        "Numero_edital": rec.get("numero") or NA,
        "Titulo": rec.get("titulo"),
        "Ano": year_from(rec) or NA,           # derivado de numero/titulo (nao vem da API)
        "Status": rec.get("status_chamada"),
        "Data_publicacao": NA,                 # API devolve data_publicacao nula nos casos vistos
        "Inicio_submissao": rec.get("data_inicio_submissao") or NA,
        "Prazo_submissao": rec.get("data_fim_submissao") or NA,
        "Valor_portal": rec.get("valor") or NA,
        "URL_oficial": NA,                     # padrao de URL da pagina publica ainda nao verificado
        "N_anexos": len(anexos),
        "N_anexos_pdf": sum(1 for a in anexos if file_ext(a.get("url_anexo")) == "pdf"),
        "Heuristica_so_resultados": only_results(anexos),
        "Publico_alvo": selected("publico_alvo"),
        "Linhas_fomento": selected("linhas_fomento"),
        "Coletado_em": collected_at,
        "id_api": rec["id"],
        "slug": rec.get("slug"),
    }
    for f in TEXT_FIELDS:
        row[f"Portal_{f}_preenchido"] = not is_placeholder(rec.get(f))
    return row


def anexo_rows(rec):
    for a in rec.get("anexos") or []:
        yield {
            "ID_edital": f"FAPEMIG-{rec['id']}",
            "id_anexo": a.get("id"),
            "descricao_portal": a.get("descricao"),
            "tipo_portal": a.get("tipo"),
            "retificacao_portal": a.get("retificacao"),
            "nome_original": a.get("nome_original"),
            "extensao_url": file_ext(a.get("url_anexo")),
            "tipo_arquivo_portal": a.get("tipo_arquivo") or NA,
            "tamanho_kb_portal": a.get("tamanho_kb"),
            "data_upload_portal": a.get("data_publicacao") or NA,  # data de upload, NAO de publicacao do edital
            "url_anexo": a.get("url_anexo"),
        }


def write_csv(path: Path, rows: list):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: Excel abre acentos certo
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# ----------------------------------------------------------------------------- diagnostico
def diagnose(records, editais, anexos, reported_total):
    n = len(records)
    print("\n=== DIAGNOSTICO DA POPULACAO ===")
    print(f"Registros unicos por id_api ........ {n}")
    print(f"Total informado pela API ........... {reported_total}")
    print("Por status_chamada ................. ", dict(Counter(r.get('status_chamada') for r in records)))
    print("Por fluxo_tipo ..................... ", dict(Counter(r.get('fluxo_tipo') for r in records)))
    print(f"Sem nenhum anexo ................... {sum(1 for e in editais if e['N_anexos'] == 0)}")
    print(f"Sem nenhum PDF ..................... {sum(1 for e in editais if e['N_anexos_pdf'] == 0)}")
    so_res = sum(1 for e in editais if e['Heuristica_so_resultados'])
    print(f"So anexos com cara de resultado .... {so_res} (heuristica; conferir manualmente)")
    print(f"Numero ausente ('-' ou vazio) ...... {sum(1 for e in editais if e['Numero_edital'] in ('-', NA))}")
    print(f"Ano nao derivavel .................. {sum(1 for e in editais if e['Ano'] == NA)}")
    for f in TEXT_FIELDS:
        k = sum(1 for e in editais if e[f'Portal_{f}_preenchido'])
        print(f"Portal: {f:<28} preenchido em {k}/{n}")
    print("Extensoes de anexos ................ ", dict(Counter(a['extensao_url'] for a in anexos)))
    print(f"Anexos com tamanho 0/ausente ....... {sum(1 for a in anexos if not a['tamanho_kb_portal'])}")
    keys = Counter(
        (str(e['Numero_edital']).strip().upper(), e['Ano'])
        for e in editais if e['Numero_edital'] not in ('-', NA)
    )
    dups = {k: v for k, v in keys.items() if v > 1}
    print(f"Pares (numero, ano) repetidos ...... {len(dups)} {dups if dups else ''}")


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(PROJECT_ROOT), help="raiz do projeto (default: raiz do projeto)")
    ap.add_argument("--status", default="", help="filtro de status da API (ex.: encerrada); vazio = todos")
    ap.add_argument("--per-page", type=int, default=20, help="registros por pagina (20 foi o valor observado)")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES)
    ap.add_argument("--offline", metavar="DIR", help="le calls_*.json de DIR e nao acessa a rede")
    args = ap.parse_args()

    root = Path(args.root)
    raw_dir, snap_dir = root / "data" / "raw", root / "data" / "snapshots"
    raw_dir.mkdir(parents=True, exist_ok=True)
    snap_dir.mkdir(parents=True, exist_ok=True)

    if args.offline:
        paths = glob.glob(str(Path(args.offline) / "calls_*.json"))
        if not paths:
            sys.exit(f"Nenhum calls_*.json em {args.offline}")
    else:
        print(f"Coletando {API_URL} (status='{args.status or 'todos'}')...")
        paths = collect(raw_dir, args.per_page, args.status, args.max_pages)

    records, reported_total = load_raw(paths)
    now = datetime.now()
    collected_at = now.isoformat(timespec="seconds")
    editais = [edital_row(r, collected_at) for r in records]
    anexos = [a for r in records for a in anexo_rows(r)]

    stamp = now.strftime("%Y%m%dT%H%M%S")
    write_csv(snap_dir / f"editais_{stamp}.csv", editais)
    write_csv(snap_dir / f"anexos_{stamp}.csv", anexos)
    print(f"\nGravado: {snap_dir}/editais_{stamp}.csv ({len(editais)} linhas) e anexos_{stamp}.csv ({len(anexos)} linhas)")

    diagnose(records, editais, anexos, reported_total)
    if reported_total is not None and reported_total != len(records) and not args.status:
        print(f"\nATENCAO: a API informa {reported_total}, mas ha {len(records)} ids unicos. Investigar.")


if __name__ == "__main__":
    main()
