#!/usr/bin/env python3
"""Etapa 4 (v1) - deteccao de ESTRUTURA FORMAL por regras deterministicas (sem LLM).

Entrada : documentos_principais.csv + data/extracted/<arquivo_json>  (linhas com fonte/negrito)
Saida   : data/snapshots/estrutura_<timestamp>.csv  (colunas da aba 'Estrutura' + colunas de auditoria)
          relatorio no terminal, com a CONFERENCIA contra o SUMARIO do proprio documento.

Estrutura formal != conteudo tematico: aqui so se registra ONDE ha titulos. Um topico sem titulo
proprio NAO aparece aqui (isso e a Etapa 5).

Regras (calibradas em 5 PDFs da FAPEMIG; a v0 com regras "caixa alta + negrito" gerava centenas de falsos
positivos porque cabecalhos de tabela tambem sao negrito em caixa alta):
  capitulo : "CAPITULO I - ..."
  anexo    : "ANEXO I - ..." (so MAIUSCULAS; "Anexo 1." no meio de uma frase nao conta)
  secao    : "1. OBJETIVO" / "1 OBJETIVO" (numero + titulo em MAIUSCULAS), inclusive quando o numero esta
             sozinho numa linha ("1.") e o titulo na seguinte; titulo quebrado em 2 linhas e reunido
  subsecao : "1.1 Titulo curto" (numeracao composta, ate 8 palavras, sem pontuacao final, sem data)
  clausula : "CLAUSULA PRIMEIRA - ..." (minutas de Termo de Outorga dentro dos anexos)
Cada elemento recebe a REGIAO: 'corpo' (texto da chamada) ou 'anexo:<rotulo>'. Clausulas de uma minuta
contratual nao se misturam com as secoes do edital.

O SUMARIO (quando existe) e excluido da estrutura e usado so como referencia: quantos titulos principais
listados nele foram achados no corpo. E uma referencia FRACA (cobre so titulos principais), nao substitui
a conferencia manual.

Tamanho de fonte NAO decide nada (nos 5 documentos titulos e corpo tem o mesmo tamanho; o negrito varia:
em 193 o sumario nao e negrito e o corpo e). Fonte e negrito ficam registrados para auditoria.

Uso:  python -m editais.structure [--root .] [--ids FAPEMIG-116,FAPEMIG-192] [--show]
"""
import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from editais import textnorm
from editais.config import PROJECT_ROOT

CAP_RE = re.compile(r"^CAP[ÍI]TULO\s+([IVXLC]+|\d+)\b\s*[-–:]?\s*(.*)$")
ANEXO_RE = re.compile(r"^ANEXO\s+([IVXLC]+|\d+|[ÚU]NICO)\b\s*[-–:]?\s*(.*)$")
ANEXO_MIX_RE = re.compile(r"^Anexo\s+([IVXLC]+|\d+)\s*[-–:]\s*(\S.*)$")  # "Anexo 4 – MODELO ..." (so se negrito)
CLAUSULA_RE = re.compile(r"^CL[ÁA]USULA\s+(.+)$")
STOP_JOIN_RE = re.compile(r"^(ANEXOS?|SUM[ÁA]RIO)$")
SEC_RE = re.compile(r"^(\d{1,2})\.?\s+(\S.*)$")
SEC_ALONE_RE = re.compile(r"^(\d{1,2})\.?$")
SUB_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,3})+)\.?\s+([A-Za-zÀ-ÿ].*)$")
TOC_RE = re.compile(r"^(SUM[ÁA]RIO|[ÍI]NDICE)$")
FOOTER_RE = re.compile(r"^(FAPEMIG - .*\(\d+\).*SEI\b.*|\d+\s*/\s*\d+)$")
MAX_SUB_WORDS = 8
OUT_FIELDS = ["ID_edital", "ID_documento", "Tipo_elemento", "Titulo_secao", "Nivel_hierarquico",
              "Pagina_inicial", "Pagina_final", "Evidencia", "Status_validacao",
              "Regiao", "Numero", "Negrito", "Tamanho_fonte", "Confianca"]


def upper_ratio(s):
    letters = [c for c in s if c.isalpha()]
    return (sum(c.isupper() for c in letters) / len(letters)) if letters else 0.0


def is_title(s, min_letters=3):
    return sum(c.isalpha() for c in s) >= min_letters and upper_ratio(s) >= 0.7 and len(s) < 130


def valid_sec_title(title):
    """Titulo de secao plausivel: comeca com letra, sem ':' no meio, sem marcadores de modelo ([ ou {)."""
    return title[:1].isalpha() and ":" not in title and not re.search(r"[\[\]{}]", title)


def join_title(lines, i, base):
    """Reune linhas seguintes que continuam um titulo em MAIUSCULAS (mesma pagina, mesmo negrito, nao numeradas).
    Devolve (texto_adicional, ultimo_indice_consumido)."""
    extra, j = [], i
    while j + 1 < len(lines) and len(extra) < 12:
        nx = lines[j + 1]
        if nx["page"] != base["page"] or nx["bold"] != base["bold"] or not is_title(nx["t"], 1) or len(nx["t"]) >= 60 \
                or SEC_RE.match(nx["t"]) or SUB_RE.match(nx["t"]) or CAP_RE.match(nx["t"]) or ANEXO_RE.match(nx["t"]) \
                or CLAUSULA_RE.match(nx["t"]) or STOP_JOIN_RE.match(nx["t"]) or SEC_ALONE_RE.match(nx["t"]):
            break
        extra.append(nx["t"])
        j += 1
    return " ".join(extra), j


def load_lines(doc):
    out = []
    for p in doc["pages"]:
        raw = [dict(l, t=re.sub(r"\s+", " ", textnorm.fix_glyphs(l["t"])).strip(), page=p["page"])
               for l in p.get("lines", [])]
        raw = [l for l in raw if l["t"]]
        drop = set()
        for i, l in enumerate(raw):
            if textnorm.SEI_RE.match(l["t"]) or textnorm.URL_SEI_RE.match(l["t"]) or FOOTER_RE.match(l["t"]):
                drop.add(i)
                if i and textnorm.DATE_RE.match(raw[i - 1]["t"]):
                    drop.add(i - 1)
        out.extend(l for i, l in enumerate(raw) if i not in drop)
    return out


def classify(lines):
    cands, i, n = [], 0, len(lines)
    while i < n:
        l = lines[i]
        t = l["t"]
        start = i
        item = None
        if TOC_RE.match(t):
            item = ("sumario", 0, t, t, None)
        elif (m := CAP_RE.match(t)) and (is_title(t) or m.group(2) == ""):
            item = ("capitulo", 1, t, t, m.group(1))
        elif (m := ANEXO_RE.match(t)):
            item = ("anexo", 1, t, t, m.group(1))
        elif (m := ANEXO_MIX_RE.match(t)) and l["bold"] and is_title(m.group(2)):
            item = ("anexo", 1, t, t, m.group(1))
        elif (m := CLAUSULA_RE.match(t)) and upper_ratio(t) >= 0.7:
            item = ("clausula", 2, t, t, re.split(r"\s*[-–]", m.group(1))[0].strip())
        elif SEC_ALONE_RE.match(t) and i + 1 < n and is_title(lines[i + 1]["t"]) \
                and not SEC_RE.match(lines[i + 1]["t"]) and lines[i + 1]["page"] == l["page"] \
                and not (ANEXO_RE.match(lines[i + 1]["t"]) or CAP_RE.match(lines[i + 1]["t"])
                         or CLAUSULA_RE.match(lines[i + 1]["t"]) or STOP_JOIN_RE.match(lines[i + 1]["t"])) \
                and (t.endswith(".") or (l["bold"] and lines[i + 1]["bold"])):  # "9" sem ponto so com negrito (evita celulas de tabela)
            title = lines[i + 1]["t"]
            extra, j = join_title(lines, i + 1, lines[i + 1])
            if extra:
                title += " " + extra
            if valid_sec_title(title):
                item = ("secao", 1, title, f"{t} {title}", SEC_ALONE_RE.match(t).group(1))
                i = j
        elif (m := SEC_RE.match(t)) and is_title(m.group(2)) and not m.group(2).strip().isdigit() \
                and valid_sec_title(m.group(2)):
            title = m.group(2)
            extra, j = join_title(lines, i, l)
            if extra:
                title += " " + extra
                i = j
            item = ("secao", 1, title, t, m.group(1))
        elif (m := SUB_RE.match(t)):
            body = m.group(2)
            first = body.split()[0]
            # "4.3 desta Chamada" / "4.3 A" sao referencias cruzadas no inicio de linha, nao titulos
            if len(body.split()) <= MAX_SUB_WORDS and not re.search(r"[.;,:]$", body) \
                    and not re.search(r"\d{1,2}/\d{1,2}", body) and first[0].isupper() and len(body.split()) >= 2 \
                    or (len(body.split()) == 1 and len(first) > 2 and first[0].isupper() and not re.search(r"[.;,:]$", body)):
                item = ("subsecao", m.group(1).count(".") + 1, body, t, m.group(1))
        if item:
            tipo, nivel, titulo, evid, num = item
            cands.append({"tipo": tipo, "nivel": nivel, "titulo": titulo, "evid": evid, "page": l["page"],
                          "bold": l["bold"], "size": l["size"], "num": num, "idx": start})
        i += 1
    return cands


def key(c):
    return re.sub(r"[^A-Z0-9]", "", (c["titulo"] + (c["num"] or "")).upper())[:40]


def parse(doc):
    """Devolve (linhas, elementos_do_corpo_e_anexos, elementos_do_sumario, tem_sumario, (ini, fim)).
    (ini, fim) = intervalo de indices de linha do TEXTO DA CHAMADA: apos o sumario e antes do primeiro anexo."""
    lines = load_lines(doc)
    cands = classify(lines)
    toc, toc_idx = [], set()
    starts = [k for k, c in enumerate(cands) if c["tipo"] == "sumario"]
    if starts:
        first = None
        for k in range(starts[0] + 1, len(cands)):
            c = cands[k]
            if first is None:
                first = key(c)
            elif key(c) == first:
                break
            toc_idx.add(k)
            toc.append(c)
    body = [c for k, c in enumerate(cands) if c["tipo"] != "sumario" and k not in toc_idx]

    region = "corpo"
    for c in body:
        if c["tipo"] == "anexo":
            region = f"anexo:{c['num']}"
        elif c["tipo"] == "capitulo":
            region = "corpo"
        c["regiao"] = region
    first_body = body[0]["idx"] if (starts and body) else 0
    first_anexo = next((c["idx"] for c in body if c["tipo"] == "anexo"), len(lines))
    return lines, body, toc, bool(starts), (first_body, first_anexo)


def build(doc, id_edital, id_doc):
    lines, body, toc, has_toc, _ = parse(doc)
    n_pages = len(doc["pages"])
    for j, c in enumerate(body):
        end = n_pages
        for d in body[j + 1:]:
            if d["nivel"] <= c["nivel"] and d["tipo"] != "clausula":
                end = max(d["page"], c["page"])
                break
        c["page_end"] = max(end, c["page"])
        c["conf"] = "media" if c["tipo"] == "subsecao" else ("alta" if (c["bold"] or not has_toc) else "media")

    rows = [{"ID_edital": id_edital, "ID_documento": id_doc, "Tipo_elemento": c["tipo"], "Titulo_secao": c["titulo"],
             "Nivel_hierarquico": c["nivel"], "Pagina_inicial": c["page"], "Pagina_final": c["page_end"],
             "Evidencia": c["evid"], "Status_validacao": "automatico_nao_validado", "Regiao": c["regiao"],
             "Numero": c["num"] or "", "Negrito": c["bold"], "Tamanho_fonte": c["size"], "Confianca": c["conf"]}
            for c in body]

    check = None
    if toc:
        main = lambda c: c["tipo"] in ("secao", "capitulo", "anexo")
        toc_main = [c for c in toc if main(c)]
        body_keys = {key(c) for c in body if main(c)}
        toc_keys = {key(c) for c in toc_main}
        check = {"toc_n": len(toc_main),
                 "achados": sum(1 for c in toc_main if key(c) in body_keys),
                 "faltando": [f"{c['num'] or ''} {c['titulo']}".strip() for c in toc_main if key(c) not in body_keys],
                 "extras": [f"{c['num'] or ''} {c['titulo']}".strip() for c in body
                            if main(c) and c["regiao"] == "corpo" and key(c) not in toc_keys]}
    return rows, check


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(PROJECT_ROOT))
    ap.add_argument("--docs", default="documentos_principais.csv")
    ap.add_argument("--ids")
    ap.add_argument("--show", action="store_true", help="imprime o esboco de cada documento")
    args = ap.parse_args()
    root = Path(args.root)
    ids = {x.strip() for x in args.ids.split(",")} if args.ids else None

    with open(root / args.docs, newline="", encoding="utf-8-sig") as f:
        docs = [d for d in csv.DictReader(f) if ids is None or d["ID_edital"] in ids]
    all_rows = []
    for d in docs:
        jp = root / "data" / "extracted" / d["arquivo_json"]
        if not jp.exists():
            print(f"[{d['ID_edital']}] JSON nao encontrado: {jp}")
            continue
        doc = json.loads(jp.read_text(encoding="utf-8"))
        rows, check = build(doc, d["ID_edital"], d["id_anexo"])
        all_rows += rows
        corpo = Counter(r["Tipo_elemento"] for r in rows if r["Regiao"] == "corpo")
        anexos = sum(1 for r in rows if r["Regiao"] != "corpo")
        print(f"\n=== {d['ID_edital']} anexo {d['id_anexo']} ({len(doc['pages'])} pags) ===")
        print(f"corpo: {dict(corpo) or 'nada detectado'} | elementos dentro de anexos: {anexos}")
        if check:
            print(f"sumario: {check['achados']}/{check['toc_n']} titulos principais achados no corpo")
            for s in check["faltando"]:
                print(f"   FALTOU: {s}")
            for s in check["extras"]:
                print(f"   EXTRA (nao esta no sumario): {s}")
        else:
            print("sem SUMARIO: nao ha referencia interna para conferir")
        if args.show:
            for r in rows:
                ind = "  " * (int(r["Nivel_hierarquico"]) - 1)
                print(f"  p.{r['Pagina_inicial']:>3}-{r['Pagina_final']:<3} {r['Regiao']:<9} {ind}{r['Tipo_elemento'][:4]} "
                      f"{r['Numero']} {r['Titulo_secao'][:70]}")

    if all_rows:
        out = root / "data" / "snapshots" / f"estrutura_{datetime.now().strftime('%Y%m%dT%H%M%S')}.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
            w.writeheader()
            w.writerows(all_rows)
        print(f"\nGravado: {out} ({len(all_rows)} elementos)")


if __name__ == "__main__":
    main()
