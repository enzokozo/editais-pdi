#!/usr/bin/env python3
"""Etapa 5 (v1) - classificacao de topicos por regras (titulos + palavras-chave). Sem LLM.

Entrada : documentos_principais.csv, data/extracted/<json>, data/extraction_manifest.csv
Saida   : data/snapshots/topicos_<timestamp>.csv   (colunas da aba 'Topicos' + auditoria)
          matriz topico x edital no terminal

Escopo do texto analisado (v1): a chamada (apos o sumario, ate o primeiro anexo) E os anexos que fazem parte do
PDF principal (regra definida pelo pesquisador: anexos contam como parte do edital). Cada topico e avaliado nas
duas regioes e o resultado fica registrado separadamente:
  Estado_somente_corpo : so o texto da chamada (analise de sensibilidade; vale se a banca nao aceitar anexos);
  Estado               : corpo + anexos (regra principal). Os anexos so podem ELEVAR o estado, nunca rebaixa-lo;
  Regiao               : onde esta a evidencia usada em 'Estado' (corpo | anexo).
Cuidado: minutas de Termo de Outorga sao modelos longos que citam muitos assuntos (vigencia, sigilo, obrigacoes);
nos anexos so contam (a) linha curta em CAIXA ALTA/negrito que bata com o titulo do topico, ou (b) >= 2 linhas
de conteudo. Anexos de PDFs separados do principal NAO sao lidos (limitacao conhecida).
O topico 18 (anexos e formularios) usa a estrutura.

ESTADOS (por edital x topico):
  Presente        : titulo de secao do corpo bate com o topico (metodo 'titulo_secao'), OU >= 2 linhas distintas
                    do corpo batem com palavras-chave do topico ('palavras_chave').
  Incerto         : exatamente 1 linha do corpo bate so por palavra-chave (evidencia fraca/ambigua).
  Ausente         : documento examinado (texto extraido ok e corpo com texto) e NENHUMA regra bateu.
                    Significa "sem evidencia conforme estas regras", nao "o edital nao trata do assunto".
  Nao verificado  : extracao ruim/parcial ou corpo vazio: nao se afirma nada.

ATENCAO: confirmar que um trecho existe NAO confirma que o significado esta certo (ex.: a palavra
"cronograma" aparece em "cronograma fisico do projeto", que e outra coisa). Por isso a validacao manual
(aba Auditoria) e obrigatoria antes de qualquer frequencia ser citada. Os padroes abaixo sao HIPOTESES
iniciais (taxonomia do projeto), nao resultados.

Uso:  python -m editais.topics [--root .] [--ids FAPEMIG-192] [--show]
"""
import argparse
import csv
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from editais import structure
from editais.config import PROJECT_ROOT

# (nome, [padroes de TITULO], [padroes de CONTEUDO]) - sem acentos, minusculas
TOPICS = [
    ("Objetivo da chamada",
     [r"^objetivo"],
     [r"(esta|a presente) chamada (publica )?(visa|tem (por|como) (objetivo|finalidade))", r"objetivo (geral|principal) d[ao]", r"tem por objetivo", r"visa apoiar"]),
    ("Publico-alvo",
     [r"quem pode participar", r"publico[- ]alvo", r"destinatarios", r"^proponentes?$"],
     [r"publico[- ]alvo", r"(podem|poderao) (participar|submeter|apresentar propostas)", r"convidamos (os )?(pesquisadores|empresas)", r"convida os interessados", r"sao elegiveis"]),
    ("Criterios de elegibilidade",
     [r"elegibilidade", r"requisitos para participacao", r"condicoes para submissao", r"quem pode participar", r"^requisitos"],
     [r"sao elegiveis", r"criterios? de elegibilidade", r"requisitos (do|da|para (o|a)) (proponente|coordenador|proposta|participa)", r"\bpor parte d[aoe]s? (proponente|coordenador|proposta|membros|subcoordenador)", r"podera ser (coordenador|proponente)"]),
    ("Areas de conhecimento",
     [r"areas? (de|do) conhecimento", r"tematicas", r"temas", r"setores prioritarios", r"areas? tematicas"],
     [r"areas? (de|do) conhecimento", r"temas? prioritari", r"setores prioritarios", r"tematicas e setores", r"linhas tematicas", r"areas? tematicas"]),
    ("Modalidades de financiamento",
     [r"categorias? de apoio", r"modalidades?", r"tipos? de apoio"],
     [r"subvencao economica", r"modalidades? de (apoio|financiamento|fomento)", r"categorias? de apoio", r"recursos nao[- ]reembolsaveis", r"\bbolsas?\b.*\b(modalidade|categoria)"]),
    ("Valor global da chamada",
     [r"recursos financeiros", r"valor global", r"dotacao orcamentaria"],
     [r"valor global", r"recursos no valor (global )?de", r"montante (total|global)", r"orcamento (total|global)", r"serao destinados .*recursos .*r\$", r"recursos financeiros .*r\$ ?[\d\.]+"]),
    ("Limites financeiros por projeto",
     [r"valor(es)? por proposta", r"prazos e valores", r"valor(es)? do projeto", r"dos valores$", r"categorias de apoio e dos valores"],
     [r"valor (maximo|minimo)", r"limite (maximo|de valor|financeiro)", r"valor(es)? por proposta", r"enquadrar-se entre o minimo", r"ate r\$ ?[\d\.]+,\d{2} por (projeto|proposta)", r"valores? (maximos?|minimos?) (por|de)"]),
    ("Despesas elegiveis",
     [r"itens (nao )?financiaveis", r"despesas (elegiveis|financiaveis)"],
     [r"itens financiaveis", r"despesas de (custeio|capital)", r"poderao ser custeadas", r"serao (financiados|apoiados) .*(itens|despesas)", r"rubricas? (financiaveis|elegiveis)", r"itens nao financiaveis"]),
    ("Cronograma e prazos",
     [r"cronograma", r"calendario", r"^prazos?"],
     [r"\bcronograma\b", r"\bcalendario\b", r"prazo (final|limite|de submissao|para submissao)", r"periodo de submissao", r"ate as \d{1,2}[:h]\d{0,2}"]),
    ("Documentacao exigida",
     [r"documentos? eletronicos", r"documentacao", r"documentos? (exigidos|necessarios|obrigatorios)"],
     [r"documentos? (obrigatorios|exigidos|necessarios)", r"documentacao (exigida|necessaria|obrigatoria|prevista)", r"entrega d[ae] documentacao", r"lista de documentos", r"relacao de documentos", r"(devera|deverao) (ser )?(anexad|apresentad)\w+ .*(documento|declaracao|termo)", r"declaracao de"]),
    ("Submissao de propostas",
     [r"submissao", r"preenchimento d[ae]s? propostas?", r"apresentacao da proposta", r"caracteristicas d[ae]s? propostas?"],
     [r"propostas (serao|deverao ser) (recebidas|submetidas|enviadas|apresentadas)", r"sistema everest", r"formulario eletronico", r"submissao (da|das|de) propostas?", r"submetid[ao]s? .*(sistema|eletronic)"]),
    ("Criterios de avaliacao e selecao",
     [r"julgamento", r"selecao", r"avaliacao", r"criterios de avaliacao", r"analise"],
     [r"criterios? de (avaliacao|julgamento|selecao)", r"nota (final|minima|maxima)", r"\bmerito\b", r"pontuacao", r"camara de avaliacao", r"etapa \d? ?(-|–)? ?(de )?(avaliacao|selecao|analise)"]),
    ("Divulgacao dos resultados",
     [r"divulgacao d[oe]s? resultados?", r"^resultados?$"],
     [r"divulgacao d[oe]s? resultados?", r"resultado (preliminar|final)", r"lista final", r"sera divulgad[ao]"]),
    ("Obrigacoes dos beneficiarios",
     [r"obrigacoes"],
     [r"obrigacoes d[ao]s? (beneficiari|coordenador|proponente|partícipes|participes)", r"(beneficiari[ao]s?|coordenador|proponente) (devera|devem|deverao|se obriga)", r"compromete-se", r"responsabilizar-se"]),
    ("Acompanhamento e prestacao de contas",
     [r"prestacao de contas", r"acompanhamento"],
     [r"prestacao de contas", r"relatorios? (tecnico|parcial|final|de execucao)", r"acompanhamento (tecnico|do projeto|e avaliacao)", r"avaliacao final"]),
    ("Recursos administrativos",
     [r"recursos? administrativos?", r"interposicao de recursos?"],
     [r"recursos? administrativos?", r"interposicao de recurso", r"interpor recurso", r"interpostos"]),
    ("Disposicoes gerais e legais",
     [r"disposicoes (gerais|finais)", r"base legal", r"orientacoes gerais", r"informacoes complementares"],
     [r"reger-se-a", r"lei (federal |estadual )?n[o°º]?\.? ?[\d\.]+", r"decreto (federal |estadual )?n[o°º]?\.? ?[\d\.]+", r"casos omissos", r"\bforo\b"]),
]
ANEXOS_TOPIC = "Anexos e formularios"
# Topicos citados de passagem em muitas clausulas (ex.: "...pela prestacao de contas"): so palavra-chave NAO basta
# para 'Presente'; exige titulo de secao. Hipotese a revisar na auditoria manual.
MENTION_PRONE = {"Acompanhamento e prestacao de contas", "Obrigacoes dos beneficiarios", "Documentacao exigida"}
OUT_FIELDS = ["ID_edital", "ID_documento", "Topico_padronizado", "Estado", "Estado_somente_corpo", "Regiao",
              "Trecho_evidencia", "Pagina", "Secao", "Metodo_extracao", "Status_validacao", "N_titulos",
              "N_linhas_conteudo", "Confianca"]
RANK = {"Nao verificado": 0, "Ausente": 1, "Incerto": 2, "Presente": 3}
CLAUSULA_PREFIX = re.compile(r"^clausula\s+\S+\s*[-–:.]?\s*")


def norm(s):
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def compile_topics():
    return [(n, [re.compile(p) for p in th], [re.compile(p) for p in tc]) for n, th, tc in TOPICS]


def annex_evidence(name, th, tc, annex_lines, mention_prone):
    """Evidencia nos anexos: titulo curto (caixa alta ou negrito) OU >= 2 linhas de conteudo.
    Devolve dict(estado, k, metodo, n_tit, n_cont) ou None."""
    tit_idx, cont_idx = [], []
    norms = [norm(l["t"]) for l in annex_lines]
    for k, l in enumerate(annex_lines):
        t = l["t"]
        if len(t) <= 80 and (t.isupper() or l.get("bold")):
            nt = CLAUSULA_PREFIX.sub("", norms[k]).strip(" -–:.")
            if nt and any(p.search(nt) for p in th):
                tit_idx.append(k)
        window = norms[k] + (" " + norms[k + 1] if k + 1 < len(norms) else "")
        for p in tc:
            m = p.search(window)
            if m and m.start() < len(norms[k]) + 1:
                cont_idx.append(k)
                break
    if tit_idx:
        return dict(estado="Presente", k=tit_idx[0], metodo="titulo_anexo", n_tit=len(tit_idx), n_cont=len(cont_idx))
    if len(cont_idx) >= 2 and name not in mention_prone:
        return dict(estado="Presente", k=cont_idx[0], metodo="palavras_chave_anexo", n_tit=0, n_cont=len(cont_idx))
    if cont_idx:
        return dict(estado="Incerto", k=cont_idx[0], metodo="palavras_chave_anexo", n_tit=0, n_cont=len(cont_idx))
    return None


def classify_doc(doc, extraction_ok, struct_rows=None):
    """Devolve lista de dicts {topico, estado, trecho, pagina, secao, metodo, n_tit, n_cont, conf}."""
    lines, body, toc, has_toc, (ini, fim) = structure.parse(doc)
    corpo = lines[ini:fim]
    heads = [c for c in body if c["regiao"] == "corpo" and c["tipo"] in ("secao", "subsecao") and c["idx"] >= ini]
    anexos = [c for c in body if c["tipo"] == "anexo"]
    verified = extraction_ok and len(corpo) >= 20
    results = []

    def section_of(idx):
        cur = None
        for h in heads:
            if h["idx"] <= idx:
                cur = h
            else:
                break
        return (f"{cur['num']} {cur['titulo']}".strip() if cur else "")

    annex_lines = lines[fim:] if fim < len(lines) else []
    win = [norm(l["t"]) for l in corpo]
    for name, th, tc in compile_topics():
        if not verified:
            results.append(dict(topico=name, estado="Nao verificado", estado_corpo="Nao verificado", regiao="", trecho="",
                                pagina="", secao="", metodo="", n_tit=0, n_cont=0, conf=""))
            continue
        tit_hits = [h for h in heads if any(p.search(norm(h["titulo"])) for p in th)]
        cont_hits = []
        for k, base in enumerate(win):
            window = base + (" " + win[k + 1] if k + 1 < len(win) else "")
            for p in tc:
                m = p.search(window)
                if m and m.start() < len(base) + 1:
                    cont_hits.append(k)
                    break
        if tit_hits:
            h = tit_hits[0]
            results.append(dict(topico=name, estado="Presente", trecho=h["evid"][:220], pagina=h["page"],
                                secao=f"{h['num']} {h['titulo']}".strip(), metodo="titulo_secao" if not cont_hits else "titulo_secao+palavras_chave",
                                estado_corpo="Presente", regiao="corpo",
                                n_tit=len(tit_hits), n_cont=len(cont_hits), conf="alta"))
        elif len(cont_hits) >= 2 and name not in MENTION_PRONE:
            k = cont_hits[0]
            results.append(dict(topico=name, estado="Presente", trecho=corpo[k]["t"][:220], pagina=corpo[k]["page"],
                                secao=section_of(ini + k), metodo="palavras_chave", n_tit=0, n_cont=len(cont_hits), conf="media",
                                estado_corpo="Presente", regiao="corpo"))
        elif cont_hits:
            k = cont_hits[0]
            results.append(dict(topico=name, estado="Incerto", trecho=corpo[k]["t"][:220], pagina=corpo[k]["page"],
                                secao=section_of(ini + k), metodo="palavras_chave", n_tit=0, n_cont=1, conf="baixa",
                                estado_corpo="Incerto", regiao="corpo"))
        else:
            results.append(dict(topico=name, estado="Ausente", estado_corpo="Ausente", regiao="", trecho="", pagina="",
                                secao="", metodo="sem_evidencia_nas_regras", n_tit=0, n_cont=0, conf="media"))
        # anexos: so podem elevar o estado
        cur = results[-1]
        ev = annex_evidence(name, th, tc, annex_lines, MENTION_PRONE)
        if ev and RANK[ev["estado"]] > RANK[cur["estado"]]:
            l = annex_lines[ev["k"]]
            cur.update(estado=ev["estado"], regiao="anexo", trecho=l["t"][:220], pagina=l["page"], secao="",
                       metodo=ev["metodo"], n_tit=ev["n_tit"], n_cont=ev["n_cont"],
                       conf="media" if ev["estado"] == "Presente" else "baixa")
    # 18: anexos e formularios (estrutura)
    if not extraction_ok:
        results.append(dict(topico=ANEXOS_TOPIC, estado="Nao verificado", estado_corpo="Nao verificado", regiao="", trecho="", pagina="", secao="", metodo="", n_tit=0, n_cont=0, conf=""))
    elif anexos:
        a = anexos[0]
        results.append(dict(topico=ANEXOS_TOPIC, estado="Presente", trecho=a["evid"][:220], pagina=a["page"], secao=a["titulo"][:100],
                            metodo="estrutura_anexo", n_tit=len(anexos), n_cont=0, conf="alta",
                            estado_corpo="Presente", regiao="anexo"))
    else:
        results.append(dict(topico=ANEXOS_TOPIC, estado="Ausente", estado_corpo="Ausente", regiao="", trecho="", pagina="",
                            secao="", metodo="sem_evidencia_nas_regras", n_tit=0, n_cont=0, conf="media"))
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(PROJECT_ROOT))
    ap.add_argument("--docs", default="documentos_principais.csv")
    ap.add_argument("--ids")
    ap.add_argument("--show", action="store_true", help="imprime a evidencia de cada topico")
    args = ap.parse_args()
    root = Path(args.root)
    ids = {x.strip() for x in args.ids.split(",")} if args.ids else None

    ext = {}
    with open(root / "data" / "extraction_manifest.csv", newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            ext[(r["ID_edital"], r["id_anexo"])] = r["status_extracao"]
    with open(root / args.docs, newline="", encoding="utf-8-sig") as f:
        docs = [d for d in csv.DictReader(f) if ids is None or d["ID_edital"] in ids]

    rows, matrix = [], {}
    for d in docs:
        jp = root / "data" / "extracted" / d["arquivo_json"]
        doc = json.loads(jp.read_text(encoding="utf-8"))
        ok = ext.get((d["ID_edital"], d["id_anexo"])) == "ok"
        for r in classify_doc(doc, ok):
            matrix.setdefault(r["topico"], {})[d["ID_edital"]] = r["estado"]
            rows.append({"ID_edital": d["ID_edital"], "ID_documento": d["id_anexo"], "Topico_padronizado": r["topico"],
                         "Estado": r["estado"], "Estado_somente_corpo": r["estado_corpo"], "Regiao": r["regiao"],
                         "Trecho_evidencia": r["trecho"], "Pagina": r["pagina"], "Secao": r["secao"],
                         "Metodo_extracao": r["metodo"], "Status_validacao": "automatico_nao_validado",
                         "N_titulos": r["n_tit"], "N_linhas_conteudo": r["n_cont"], "Confianca": r["conf"]})
            if args.show:
                print(f"{d['ID_edital']} | {r['topico']:<34} {r['estado']:<14} (corpo: {r['estado_corpo']:<8}) {r['regiao']:<6} p.{str(r['pagina']):<4} {r['metodo']:<26} {r['trecho'][:70]}")

    ids_order = [d["ID_edital"] for d in docs]
    print("\n" + "Topico".ljust(38) + " ".join(i.replace("FAPEMIG-", "").rjust(9) for i in ids_order))
    short = {"Presente": "Presente", "Incerto": "Incerto", "Ausente": "Ausente", "Nao verificado": "NaoVerif"}
    for t, per in matrix.items():
        print(t[:37].ljust(38) + " ".join(short[per.get(i, 'Nao verificado')].rjust(9) for i in ids_order))

    out = root / "data" / "snapshots" / f"topicos_{datetime.now().strftime('%Y%m%dT%H%M%S')}.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"\nGravado: {out} ({len(rows)} linhas)")


if __name__ == "__main__":
    main()
