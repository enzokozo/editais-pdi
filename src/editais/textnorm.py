#!/usr/bin/env python3
"""Normalizacao do texto extraido dos PDFs (usada pelo comparador e, depois, pelo analisador).

Problemas reais observados nos PDFs da FAPEMIG (ex.: FAPEMIG-116, anexo 828):
  1. Ligaduras de fonte mal mapeadas na extracao: "desƟnados" (destinados), "cerƟﬁcação"
     (certificação), "cienơﬁco" (científico), "İsicas" (físicas), "hƩps" (https).
     Sem corrigir, buscas por palavra-chave ("objetivo", "elegibilidade") FALHAM nesses documentos.
  2. Rodape do SEI repetido em cada pagina: data/hora + "SEI/GOVMG - <n> - ..." + URL.
     Polui deteccao de secoes e comparacoes, mas a data/hora e util para ordenar versoes.
  3. Espacos de largura zero e espacos nao separaveis.

Os JSON extraidos NAO sao alterados; a normalizacao e aplicada em memoria.

Mapeamento de glifos: inferido de ~15 exemplos (todos consistentes), NAO exaustivo.
Use a auditoria para descobrir outros:   python -m editais.textnorm audit [--raw]
"""
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from editais.config import PROJECT_ROOT

GLYPH_MAP = {
    "\ufb01": "fi", "\ufb02": "fl", "\ufb00": "ff", "\ufb03": "ffi", "\ufb04": "ffl",  # ligaduras padrao
    "\u019f": "ti",   # Ɵ  (desƟnados -> destinados)
    "\u01a1": "t\u00ed",  # ơ  (ơtulo -> titulo; cienơﬁco -> cientifico)
    "\u0130": "f\u00ed",  # İ  (İsicas -> fisicas)
    "\u01a9": "tt",   # Ʃ  (hƩps -> https)
}
_GLYPH_RE = re.compile("|".join(map(re.escape, GLYPH_MAP)))
_INVISIBLE = {"\u200b": "", "\u00ad": "", "\xa0": " ", "\u202f": " "}

SEI_RE = re.compile(r"^SEI/GOVMG\s*-\s*(\d+)\s*-")
URL_SEI_RE = re.compile(r"^https?://www\.sei\.mg\.gov\.br")
DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})(?:\s+(\d{2}:\d{2}))?$")
PAGENUM_RE = re.compile(r"^(p[\u00e1a]gina\s*)?\d+(\s*(/|de)\s*\d+)?$", re.I)


def fix_glyphs(text: str) -> str:
    for k, v in _INVISIBLE.items():
        text = text.replace(k, v)

    def repl(m):
        rep = GLYPH_MAP[m.group()]
        i = m.start()
        around = [c for c in (text[i - 1] if i else "", text[m.end()] if m.end() < len(text) else "") if c.isalpha()]
        if around and all(c.isupper() for c in around):  # OBJEƟVO -> OBJETIVO
            rep = rep.upper()
        return rep

    return _GLYPH_RE.sub(repl, text)


def clean_lines(pages):
    """pages: [{'page': n, 'text': str}] -> ([(page, linha)], info_sei).

    Remove linhas vazias, numeros de pagina soltos e o rodape do SEI.
    info_sei: {'datas': [...], 'numeros': [...]} lidos do rodape (None se nao houver)."""
    flat = []
    for p in pages:
        for raw in (p.get("text") or "").splitlines():
            line = re.sub(r"\s+", " ", fix_glyphs(raw)).strip()
            if line:
                flat.append((p["page"], line))
    drop, datas, numeros = set(), [], []
    for i, (_, line) in enumerate(flat):
        m = SEI_RE.match(line)
        if not m:
            continue
        numeros.append(m.group(1))
        drop.add(i)
        if i > 0 and DATE_RE.match(flat[i - 1][1]):
            d = DATE_RE.match(flat[i - 1][1])
            datas.append(" ".join(x for x in d.groups() if x))
            drop.add(i - 1)
        if i + 1 < len(flat) and URL_SEI_RE.match(flat[i + 1][1]):
            drop.add(i + 1)
    kept = [x for j, x in enumerate(flat) if j not in drop and not PAGENUM_RE.match(x[1])]
    info = {"datas": datas, "numeros": numeros} if numeros else None
    return kept, info


def load_doc(json_path):
    jp = Path(str(json_path).replace("\\", "/"))  # manifestos gerados no Windows usam '\'
    doc = json.loads(jp.read_text(encoding="utf-8"))
    lines, info = clean_lines(doc["pages"])
    return doc, lines, info


def tokens(lines):
    """Fluxo de palavras (ignora quebras de linha) com a pagina de cada uma."""
    toks, pgs = [], []
    for page, line in lines:
        for w in line.split():
            toks.append(w)
            pgs.append(page)
    return toks, pgs


# ----------------------------------------------------------------------------- auditoria
_OK_EXTRA = set("áàâãäéèêëíìîïóòôõöúùûüçñÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑºª°§–—‘’“”„…•€²³½¼¾±×·«»‐‑−≥≤→")


def audit(root: Path, raw: bool):
    files = sorted((root / "data" / "extracted").glob("*.json"))
    if not files:
        sys.exit("Nenhum JSON em data/extracted.")
    counts, examples, where = Counter(), {}, defaultdict(set)
    for fp in files:
        doc = json.loads(fp.read_text(encoding="utf-8"))
        for p in doc["pages"]:
            text = p.get("text") or ""
            if not raw:
                text = fix_glyphs(text)
            for i, ch in enumerate(text):
                if ch.isascii() or ch in _OK_EXTRA or ch.isspace():
                    continue
                counts[ch] += 1
                where[ch].add(fp.stem[:8])
                examples.setdefault(ch, text[max(0, i - 12): i + 12].replace("\n", " "))
    print(f"{len(files)} documentos; {'texto BRUTO' if raw else 'apos fix_glyphs'}. Caracteres fora do conjunto esperado:\n")
    print(f"{'char':<5}{'codigo':<9}{'qtd':>6}  {'docs':>4}  nome / exemplo")
    for ch, n in counts.most_common(40):
        name = unicodedata.name(ch, "?")
        print(f"{ch:<5}U+{ord(ch):04X}  {n:>6}  {len(where[ch]):>4}  {name} | ...{examples[ch]}...")
    if not counts:
        print("(nenhum)")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "audit":
        root = Path(sys.argv[sys.argv.index("--root") + 1]) if "--root" in sys.argv else PROJECT_ROOT
        audit(root, raw="--raw" in sys.argv)
    else:
        print(__doc__)
