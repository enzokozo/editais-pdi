#!/usr/bin/env python3
"""Cria/atualiza output/catalogo_editais_pdi.xlsx sem perder registros anteriores.

Abas: Editais | Estrutura | Topicos | Auditoria (nomes e colunas conforme a especificacao do projeto)
      + Contagens (formulas vivas que mantem SEPARADAS as contagens exigidas: registros do portal,
        candidatos com documento, baixados, extraidos, verificados manualmente).

Fontes (todas geradas pelas etapas anteriores):
  data/snapshots/editais_*.csv      (collector.py; usa o mais recente)
  data/manifest_downloads.csv       (downloader.py)
  data/extraction_manifest.csv      (extractor.py)
  documentos_principais.csv         (decisao manual/assistida: qual PDF e o texto da chamada)

Regras de preservacao:
  - ja existindo o .xlsx, ele e copiado para output/backups/ ANTES de qualquer alteracao;
  - atualizacao por ID_edital (upsert); colunas manuais (Observacoes) nunca sao tocadas;
  - um valor existente NAO e trocado por um valor vazio/indisponivel;
  - toda alteracao de valor existente e listada em output/changelog_<timestamp>.csv;
  - as abas Estrutura, Topicos, Auditoria e Contagens nao sao tocadas se ja existirem.

Estados padronizados para lacunas: NAO_DISPONIVEL (a fonte nao informa) | NAO_TENTADO (etapa nao executada)
| SEM_PRINCIPAL_DESIGNADO (ha PDFs baixados mas nenhum foi designado como texto da chamada).
"""
import argparse
import csv
import glob
import shutil
import sys
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from editais.config import PROJECT_ROOT

NA, NT = "NAO_DISPONIVEL", "NAO_TENTADO"
BASE_PAGE = "https://fapemig.br/oportunidades/chamadas-e-editais/"
FONT = "Arial"

SHEETS = {
    "Editais": [
        "ID_edital", "Instituicao", "Numero_edital", "Titulo", "Ano", "Status", "Data_publicacao",
        "Prazo_submissao", "URL_oficial", "URL_documento", "Arquivo_local", "Hash_documento",
        "Status_download", "Status_extracao", "Observacoes",
        # extras (alem dos campos sugeridos)
        "Inicio_submissao", "Valor_portal", "N_anexos", "N_anexos_pdf", "Escopo_triagem",
        "Id_anexo_principal", "Coletado_em",
    ],
    "Estrutura": ["ID_edital", "ID_documento", "Tipo_elemento", "Titulo_secao", "Nivel_hierarquico",
                  "Pagina_inicial", "Pagina_final", "Evidencia", "Status_validacao",
                  "Regiao", "Numero", "Negrito", "Tamanho_fonte", "Confianca"],  # 5 ultimas = extras de auditoria
    "Topicos": ["ID_edital", "ID_documento", "Topico_padronizado", "Estado", "Trecho_evidencia", "Pagina",
                "Secao", "Metodo_extracao", "Status_validacao"],
    "Auditoria": ["ID_edital", "Campo_ou_topico", "Valor_automatico", "Valor_referencia",
                  "Resultado_comparacao", "Tipo_erro", "Observacoes", "Data_validacao"],
}
MANUAL_COLS = {"Observacoes"}
KEY = "ID_edital"


# ----------------------------------------------------------------------------- leitura
def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def latest(root: Path, pattern: str):
    found = sorted(glob.glob(str(root / pattern)))
    return found[-1] if found else None


def to_bool(v):
    return str(v).strip().lower() in ("true", "1", "sim")


def num_or_text(v):
    try:
        return float(v) if v not in ("", NA, None) else NA
    except ValueError:
        return v


def same_value(old, new):
    """Compara valores tolerando 20000000 (Excel) == 20000000.0 (CSV)."""
    if old is None:
        old = ""
    try:
        return float(old) == float(new)
    except (TypeError, ValueError):
        return str(old) == str(new)


def build_rows(root: Path, editais_csv: str) -> list:
    editais = read_csv(editais_csv)
    dl = {}
    for r in read_csv(root / "data" / "manifest_downloads.csv") if (root / "data" / "manifest_downloads.csv").exists() else []:
        dl[(r["ID_edital"], r["id_anexo"])] = r  # ultima linha vale
    ex = {}
    exm = root / "data" / "extraction_manifest.csv"
    for r in read_csv(exm) if exm.exists() else []:
        ex[(r["ID_edital"], r["id_anexo"])] = r
    principais = {}
    pm = root / "documentos_principais.csv"
    for r in read_csv(pm) if pm.exists() else []:
        principais[r["ID_edital"]] = r["id_anexo"]
    has_download = {k[0] for k in dl}

    rows = []
    for e in editais:
        eid = e["ID_edital"]
        n_anexos = int(e["N_anexos"] or 0)
        if n_anexos == 0:
            escopo = "sem_anexos"
        elif to_bool(e["Heuristica_so_resultados"]):
            escopo = "so_resultados_heuristica"
        else:
            escopo = "candidato_com_documento"
        url_oficial = e["URL_oficial"]
        if url_oficial in ("", NA) and e.get("slug"):
            url_oficial = BASE_PAGE + e["slug"]

        row = {
            "ID_edital": eid, "Instituicao": e["Instituicao"], "Numero_edital": e["Numero_edital"],
            "Titulo": e["Titulo"], "Ano": int(e["Ano"]) if e["Ano"].isdigit() else NA,
            "Status": e["Status"], "Data_publicacao": e["Data_publicacao"],
            "Prazo_submissao": e["Prazo_submissao"], "URL_oficial": url_oficial,
            "URL_documento": NA, "Arquivo_local": NA, "Hash_documento": NA,
            "Status_download": NT, "Status_extracao": NT, "Observacoes": "",
            "Inicio_submissao": e["Inicio_submissao"], "Valor_portal": num_or_text(e["Valor_portal"]),
            "N_anexos": n_anexos, "N_anexos_pdf": int(e["N_anexos_pdf"] or 0), "Escopo_triagem": escopo,
            "Id_anexo_principal": principais.get(eid, ""), "Coletado_em": e["Coletado_em"],
        }
        main = principais.get(eid)
        if main:
            d, x = dl.get((eid, main)), ex.get((eid, main))
            if d:
                row.update(URL_documento=d["url_anexo"], Arquivo_local=d["arquivo_local"].replace("\\", "/"),
                           Hash_documento=d["sha256"] or NA, Status_download=d["status"])
            if x:
                row["Status_extracao"] = x["status_extracao"]
        elif eid in has_download:
            row["Status_download"] = row["Status_extracao"] = "SEM_PRINCIPAL_DESIGNADO"
        rows.append(row)
    return rows


# ----------------------------------------------------------------------------- escrita
def style_header(ws, ncols):
    fill = PatternFill("solid", fgColor="1F3864")
    for c in range(1, ncols + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF", size=10)
        cell.fill = fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{get_column_letter(ncols)}1"


def set_widths(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def ensure_sheet(wb, name, headers):
    if name in wb.sheetnames:
        return wb[name], False
    ws = wb.create_sheet(name)
    ws.append(headers)
    style_header(ws, len(headers))
    set_widths(ws, [16] * len(headers))
    return ws, True


def update_estrutura(wb, estrutura_csv, refresh):
    """Acrescenta linhas de data/snapshots/estrutura_*.csv. Por padrao so entram documentos que ainda NAO
    tem linhas na aba; com --refresh-estrutura as linhas desses documentos sao substituidas (ha backup)."""
    ws = wb["Estrutura"]
    heads = SHEETS["Estrutura"]
    head_row = [c.value for c in ws[1]]
    for h in heads:
        if h not in head_row:
            ws.cell(row=1, column=len(head_row) + 1, value=h)
            head_row.append(h)
    ci = {h: i + 1 for i, h in enumerate(head_row) if h}
    src = read_csv(estrutura_csv)
    docs_new = {(r["ID_edital"], r["ID_documento"]) for r in src}
    present = {(ws.cell(row=r, column=ci["ID_edital"]).value, str(ws.cell(row=r, column=ci["ID_documento"]).value))
               for r in range(2, ws.max_row + 1) if ws.cell(row=r, column=ci["ID_edital"]).value}
    n_sheet = sum(1 for r in range(2, ws.max_row + 1) if ws.cell(row=r, column=ci["ID_edital"]).value)
    print(f"Estrutura: CSV usado = {estrutura_csv} ({len(src)} linhas, {len(docs_new)} documentos)")
    print(f"Estrutura: a aba ja tinha {n_sheet} linhas, de {len(present)} documentos")
    if not src:
        print("Estrutura: AVISO - o CSV esta vazio ou sem as colunas ID_edital/ID_documento; rode structure.py de novo.")
    if refresh:
        keep = [[ws.cell(row=r, column=c).value for c in range(1, ws.max_column + 1)] for r in range(2, ws.max_row + 1)
                if (ws.cell(row=r, column=ci["ID_edital"]).value, str(ws.cell(row=r, column=ci["ID_documento"]).value)) not in docs_new]
        ws.delete_rows(2, ws.max_row)
        for row in keep:
            ws.append(row)
        todo = docs_new
    else:
        todo = docs_new - present
    n = 0
    for r in src:
        if (r["ID_edital"], r["ID_documento"]) in todo:
            vals = {h: r.get(h, "") for h in heads}
            ws.append([vals.get(head_row[c], "") for c in range(len(head_row))])
            n += 1
    for r in range(2, ws.max_row + 1):
        for c in range(1, ws.max_column + 1):
            ws.cell(row=r, column=c).font = Font(name=FONT, size=10)
    print(f"Estrutura: {n} linhas acrescentadas ({len(todo)} documentos)")
    if n == 0 and src and not refresh:
        print("Estrutura: nada a acrescentar porque os documentos do CSV ja constam na aba. "
              "Para substituir as linhas deles, rode com --refresh-estrutura.")


def build_contagens(wb):
    ws = wb.create_sheet("Contagens")
    h = SHEETS["Editais"]
    col = lambda name: get_column_letter(h.index(name) + 1)
    rng = lambda name: f"Editais!${col(name)}$2:${col(name)}$5000"
    items = [
        ("Registros do portal (ids unicos coletados)", f"=COUNTA({rng('ID_edital')})",
         "Nao sao necessariamente editais unicos: inclui registros so com resultados e sem anexos."),
        ("  com candidato a documento da chamada", f'=COUNTIF({rng("Escopo_triagem")},"candidato_com_documento")',
         "Heuristica por nome de arquivo (Escopo_triagem); NAO verificado manualmente. Teto, nao contagem final."),
        ("  so com anexos de resultado (heuristica)", f'=COUNTIF({rng("Escopo_triagem")},"so_resultados_heuristica")', "Conferir em revisao_escopo.csv."),
        ("  sem nenhum anexo", f'=COUNTIF({rng("Escopo_triagem")},"sem_anexos")', "Sem documento disponivel no portal."),
        ("Registros com documento principal designado", f'=SUMPRODUCT(--(LEN({rng("Id_anexo_principal")})>0))',
         "Designacao manual/assistida (documentos_principais.csv)."),
        ("Documento principal baixado com sucesso", f'=COUNTIF({rng("Status_download")},"ok")',
         "Denominador: registros com documento principal designado."),
        ("Documento principal com texto extraido (ok)", f'=COUNTIF({rng("Status_extracao")},"ok")',
         "Extracao 'ok' = texto em >=80% das paginas; nao garante qualidade do conteudo."),
        ("Linhas verificadas manualmente (aba Auditoria)", "=COUNTA(Auditoria!$A$2:$A$5000)",
         "Campos/topicos conferidos, nao editais. Metricas de precisao so apos haver referencia."),
    ]
    ws.append(["Contagem", "Valor", "Definicao / ressalva"])
    for label, formula, note in items:
        ws.append([label, formula, note])
    ws.append([])
    ws.append(["Legenda: as contagens acima sao SEPARADAS de proposito (portal, unicos, baixados, processados, validados). "
               "Nunca use uma como denominador de outra sem declarar."])
    style_header(ws, 3)
    ws.freeze_panes = None
    ws.auto_filter.ref = None
    set_widths(ws, [52, 10, 95])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name=FONT, size=10)
            c.alignment = Alignment(wrap_text=True, vertical="top")


def update_workbook(xlsx: Path, rows: list, out_dir: Path, estrutura_csv=None, refresh=False):
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    headers = SHEETS["Editais"]
    changes = []
    if xlsx.exists():
        backup = out_dir / "backups" / f"{xlsx.stem}_{stamp}.xlsx"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(xlsx, backup)
        print(f"Backup: {backup}")
        wb = load_workbook(xlsx)
    else:
        wb = Workbook()
        wb.remove(wb.active)

    ws, created = ensure_sheet(wb, "Editais", headers)
    if created:
        set_widths(ws, [14, 11, 14, 60, 7, 11, 14, 19, 55, 55, 45, 20, 14, 14, 30, 19, 12, 9, 12, 24, 12, 19])
    # mapeia colunas pelo cabecalho existente (tolera colunas reordenadas/adicionadas)
    head_row = [c.value for c in ws[1]]
    for h in headers:
        if h not in head_row:
            ws.cell(row=1, column=len(head_row) + 1, value=h)
            head_row.append(h)
    colidx = {h: i + 1 for i, h in enumerate(head_row) if h}
    existing = {ws.cell(row=r, column=colidx[KEY]).value: r for r in range(2, ws.max_row + 1)
                if ws.cell(row=r, column=colidx[KEY]).value}

    n_new = n_upd = 0
    for row in rows:
        r = existing.get(row[KEY])
        if r is None:
            r = ws.max_row + 1
            n_new += 1
            for h in headers:
                ws.cell(row=r, column=colidx[h], value=row[h])
        else:
            touched = False
            for h in headers:
                if h in MANUAL_COLS or h == KEY:
                    continue
                old = ws.cell(row=r, column=colidx[h]).value
                new = row[h]
                if new in ("", None, NA, NT) and old not in ("", None, NA, NT):
                    continue  # nunca troca valor existente por vazio/indisponivel
                if not same_value(old, new):
                    changes.append((row[KEY], h, old, new))
                    ws.cell(row=r, column=colidx[h], value=new)
                    touched = True
            n_upd += touched
    for r in range(2, ws.max_row + 1):
        for c in range(1, ws.max_column + 1):
            ws.cell(row=r, column=c).font = Font(name=FONT, size=10)

    for name in ("Estrutura", "Topicos", "Auditoria"):
        ensure_sheet(wb, name, SHEETS[name])
    if estrutura_csv:
        update_estrutura(wb, estrutura_csv, refresh)
    if "Contagens" not in wb.sheetnames:
        build_contagens(wb)
    wb._sheets.sort(key=lambda s: ["Editais", "Estrutura", "Topicos", "Auditoria", "Contagens"].index(s.title)
                    if s.title in ("Editais", "Estrutura", "Topicos", "Auditoria", "Contagens") else 99)

    xlsx.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx)

    if changes:
        log = out_dir / f"changelog_{stamp}.csv"
        with open(log, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["ID_edital", "campo", "valor_antigo", "valor_novo"])
            w.writerows(changes)
        print(f"Alteracoes em valores existentes: {len(changes)} -> {log}")
    print(f"Editais: {n_new} novos, {n_upd} atualizados, {len(rows) - n_new - n_upd} sem mudanca. Arquivo: {xlsx}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(PROJECT_ROOT))
    ap.add_argument("--editais", help="CSV de editais (default: o mais recente em data/snapshots)")
    ap.add_argument("--estrutura", help="CSV de estrutura (default: o mais recente em data/snapshots)")
    ap.add_argument("--refresh-estrutura", action="store_true", help="substitui as linhas de Estrutura dos documentos presentes no CSV")
    args = ap.parse_args()
    root = Path(args.root)
    editais_csv = args.editais or latest(root, "data/snapshots/editais_*.csv")
    if not editais_csv:
        sys.exit("Nenhum editais_*.csv em data/snapshots. Rode o collector.py primeiro.")
    rows = build_rows(root, editais_csv)
    est = args.estrutura or latest(root, "data/snapshots/estrutura_*.csv")
    update_workbook(root / "output" / "catalogo_editais_pdi.xlsx", rows, root / "output", est, args.refresh_estrutura)


if __name__ == "__main__":
    main()
