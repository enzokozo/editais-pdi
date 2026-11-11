"""LLM extraction: the second level of the cascade (decisions D10, D27, D37, D38).

For one group of fields (P, L, T, I or E), keyword retrieval picks candidate passages,
the local model answers in a JSON Schema built for exactly those fields, and every
quote goes through the literal evidence check. One LLM call per group (D22).
"""
import re
import time
from dataclasses import dataclass
from typing import Literal

import requests
from pydantic import BaseModel, create_model

from editais.config import DEFAULT_MODEL
from editais.evidence import LocatedText, check_evidence
from editais.llm import LLMError, ask_json
from editais.rules import norm_datetime, norm_int, norm_money, norm_percent
from editais.schema import FIELDS, Evidence, FieldRecord, Method, Status

PASSAGE_CHARS = 8000       # ~2,000-2,500 tokens of passages, within num_ctx 4096 (D22)
CHUNK_LINES, CHUNK_STEP = 6, 4
SEPARATOR = "\n---\n"
# Timetables are tables whose cells have dates but no keywords: count dates as signal for P1-P3.
DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
DATE_FIELDS = {"P1", "P2", "P3"}

QUESTIONS = {
    "P1": "Data(s) de inicio do periodo de submissao de propostas (uma por entrada ou fase).",
    "P2": "Data(s) e hora limite para submissao de propostas (uma por entrada ou fase).",
    "P3": "Data(s) de divulgacao do resultado preliminar (uma por entrada ou fase).",
    "P4": "Prazo maximo de execucao do projeto.",
    "L1": "Valor global de recursos da chamada.",
    "L2": "Valor minimo que uma proposta pode solicitar.",
    "L3": "Valor maximo que uma proposta pode solicitar (ou a regra de calculo, se nao houver valor fixo).",
    "L4": "Contrapartida minima exigida do proponente (percentual ou valor).",
    "T1": "Quem pode ser proponente (tipo de pessoa ou instituicao).",
    "T2": "Titulacao minima exigida do proponente ou coordenador.",
    "T3": "Vinculo exigido do proponente (empregaticio, institucional, societario).",
    "I1": "Clausulas sobre propriedade intelectual (titularidade, sigilo, divulgacao).",
    "E1": "Objetivo da chamada.",
    "E2": "Areas, temas ou setores apoiados.",
    "E3": "Criterios de avaliacao das propostas (com pesos, se houver).",
    "E4": "Documentos exigidos na submissao.",
    "E5": "Prazo para interposicao de recurso.",
    "E6": "Prazo para prestacao de contas.",
}

KEYWORDS = {
    "P1": ["cronograma", "submiss", "inscri", "período", "lançamento"],
    "P2": ["cronograma", "submiss", "propostas serão recebidas", "data limite", "término do prazo", "até as", "até às"],
    "P3": ["cronograma", "resultado preliminar", "divulgação do resultado"],
    "P4": ["prazo de execução", "duração", "meses"],
    "L1": ["valor global", "recursos", "da ordem de", "r$"],
    "L2": ["mínimo", "valor solicitado", "r$"],
    "L3": ["máximo", "valor solicitado", "por proposta", "r$"],
    "L4": ["contrapartida", "%"],
    "T1": ["proponente", "elegíve", "quem pode participar", "poderão participar", "requisitos"],
    "T2": ["titulação", "doutor", "mestre", "graduação", "título"],
    "T3": ["vínculo", "vinculado", "empregatício", "sócio"],
    "I1": ["propriedade intelectual", "patente", "titularidade", "sigilo", "confidencial", "direitos"],
    "E1": ["objetivo", "apoiar", "visa"],
    "E2": ["temas", "áreas", "setores", "linhas", "prioritári"],
    "E3": ["critérios", "avaliação", "pontuação", "peso", "nota"],
    "E4": ["documentos", "anexar", "documentação", "deverá apresentar"],
    "E5": ["recurso", "interposição", "dias"],
    "E6": ["prestação de contas", "relatório final", "relatório técnico"],
}

SYSTEM = (
    "Voce extrai informacoes de editais de fomento usando somente os trechos fornecidos, "
    "separados por '---'.\n"
    "Para cada campo pedido:\n"
    "1. Em 'evidencia' (ou 'evidencias'), copie literalmente a menor frase do trecho que responde, "
    "sem incluir '---'.\n"
    "2. Em 'valor' (ou 'valores'), copie a resposta exatamente como esta escrita, sem converter formatos.\n"
    "3. Em 'status', use 'extraido' se encontrou a resposta; use 'nao_informado' somente se os trechos "
    "nao contem a resposta, deixando valor nulo ou lista vazia.\n"
    "Nunca invente informacoes."
)


# ----------------------------------------------------------------------------- answer schemas
# The schemas are permissive on purpose: one incoherent field must not invalidate the
# whole group (seen in step 10: one bad field turned 5-6 fields into `falha`).
# Coherence is decided per field in `to_record` (D48).
class SingleAnswer(BaseModel):
    evidencia: str | None
    valor: str | None
    status: Literal["extraido", "nao_informado"]


class ListAnswer(BaseModel):
    evidencias: list[str]
    valores: list[str]
    status: Literal["extraido", "nao_informado"]


def answer_schema(field_ids: list[str]) -> type[BaseModel]:
    """A model with one key per field, e.g. {'P2': ListAnswer, 'L1': SingleAnswer}."""
    return create_model("GroupAnswer", **{
        fid: (ListAnswer if FIELDS[fid].multivalued else SingleAnswer, ...) for fid in field_ids})


# ----------------------------------------------------------------------------- retrieval
def _chunk_text(doc: LocatedText, first: int, last: int) -> str:
    end = doc.starts[last + 1] if last + 1 < len(doc.starts) else len(doc.text)
    return doc.text[doc.starts[first]:end].strip()


def retrieve(doc: LocatedText, field_ids: list[str], budget: int = PASSAGE_CHARS) -> list[str]:
    """Best-scoring windows of lines for these fields, in document order, within `budget` chars."""
    keywords = {k for fid in field_ids for k in KEYWORDS[fid]}
    count_dates = bool(DATE_FIELDS & set(field_ids))
    n = len(doc.starts)
    windows = []
    for first in range(0, n, CHUNK_STEP):
        last = min(first + CHUNK_LINES, n) - 1
        text = _chunk_text(doc, first, last).lower()
        score = sum(text.count(k) for k in keywords)
        if count_dates:
            score += len(DATE_RE.findall(text))
        if score:
            windows.append((score, first, last))
    chosen, used = [], 0
    for score, first, last in sorted(windows, key=lambda w: (-w[0], w[1])):
        size = len(_chunk_text(doc, first, last))
        if used + size > budget:
            continue
        chosen.append((first, last))
        used += size
    merged = []
    for first, last in sorted(chosen):
        if merged and first <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    return [_chunk_text(doc, f, l) for f, l in merged]


def build_prompt(field_ids: list[str], passages: list[str]) -> str:
    asked = "\n".join(f"- {fid}: {QUESTIONS[fid]}" for fid in field_ids)
    return f"Campos pedidos:\n{asked}\n\nTrechos do edital:\n{SEPARATOR.join(passages)}"


# ----------------------------------------------------------------------------- answer -> record
def normalize_value(field_id: str, value: str) -> str | None:
    kind = FIELDS[field_id].kind
    if kind in ("date", "datetime"):
        return norm_datetime(value)
    if kind in ("months", "days"):
        return norm_int(value)
    if kind == "money":
        return norm_money(value)
    if kind == "percent_or_money":
        return norm_percent(value) if "%" in value else norm_money(value)
    return None


def _uncertain(field_id: str, value, reason: str) -> FieldRecord:
    return FieldRecord(field_id=field_id, status=Status.INCERTO, value=value or None, method=Method.LLM,
                       note=f"llm: {reason}")


def to_record(field_id: str, answer: BaseModel, doc: LocatedText, note: str | None = None) -> FieldRecord:
    """Decide the field status from the model's answer (coherence rules of D37, per field)."""
    is_list = isinstance(answer, ListAnswer)
    values = answer.valores if is_list else ([answer.valor] if answer.valor else [])
    quotes = answer.evidencias if is_list else ([answer.evidencia] if answer.evidencia else [])

    if answer.status == "nao_informado":
        if values:
            return _uncertain(field_id, values if is_list else values[0], "status nao_informado with a value")
        return FieldRecord(field_id=field_id, status=Status.NAO_INFORMADO, method=Method.LLM, note=note)

    if not values and quotes and FIELDS[field_id].kind == "text":
        values = list(quotes)        # for text fields the literal quote is the answer
    if not values:
        return _uncertain(field_id, None, "extraido without a value")
    if not quotes:
        return _uncertain(field_id, values if is_list else values[0], "extraido without evidence")

    if not is_list:
        check = check_evidence(quotes[0], values[0], doc)
        if not check.ok:
            return _uncertain(field_id, values[0], f"evidence rejected ({check.reason})")
        return FieldRecord(field_id=field_id, status=Status.EXTRAIDO, value=values[0], method=Method.LLM,
                           normalized=normalize_value(field_id, values[0]), note=note,
                           evidence=[Evidence(text=quotes[0], page=check.page, region=check.region)])

    # list answer: keep only values backed by a verified quote
    kept, evidence = [], []
    for value in values:
        for quote in quotes:
            check = check_evidence(quote, value, doc)
            if check.ok:
                kept.append(value)
                ev = Evidence(text=quote, page=check.page, region=check.region)
                if ev not in evidence:
                    evidence.append(ev)
                break
    if not kept:
        return _uncertain(field_id, values, "no value backed by a literal quote")
    dropped = len(values) - len(kept)
    normalized = [n for v in kept if (n := normalize_value(field_id, v))]
    notes = [x for x in (note, f"llm: {dropped} unverified value(s) dropped" if dropped else None) if x]
    return FieldRecord(field_id=field_id, status=Status.EXTRAIDO, value=kept, method=Method.LLM,
                       normalized="; ".join(normalized) or None, evidence=evidence,
                       note="; ".join(notes) or None)


# ----------------------------------------------------------------------------- one call per group
@dataclass
class CallStats:
    group: str
    fields: list[str]
    seconds: float = 0.0
    prompt_tokens: int = 0
    attempts: int = 0
    error: str | None = None


def extract_group(doc: LocatedText, field_ids: list[str], model: str = DEFAULT_MODEL,
                  max_attempts: int = 1) -> tuple[list[FieldRecord], CallStats]:
    """One call for a group of fields. No retry by default: with temperature 0 and a fixed
    seed the same prompt returns the same answer, so a retry only doubles the time (D48)."""
    stats = CallStats(group=FIELDS[field_ids[0]].group, fields=field_ids)
    passages = retrieve(doc, field_ids)
    if not passages:
        return [FieldRecord(field_id=f, status=Status.INCERTO, method=Method.LLM,
                            note="llm: no candidate passage") for f in field_ids], stats
    schema = answer_schema(field_ids)
    prompt = build_prompt(field_ids, passages)
    for _ in range(max_attempts):
        stats.attempts += 1
        t0 = time.perf_counter()
        try:
            result = ask_json(prompt, schema, model=model, system=SYSTEM)
        except LLMError as e:
            stats.error = str(e)[:300]
            continue
        except requests.RequestException as e:
            stats.error = f"{e.__class__.__name__}: {e}"[:300]
            break
        finally:
            stats.seconds += round(time.perf_counter() - t0, 2)   # failed calls count too
        stats.prompt_tokens = result.prompt_tokens
        stats.error = None
        note = "llm: prompt near context limit" if result.near_ctx_limit else None
        return [to_record(f, getattr(result.data, f), doc, note) for f in field_ids], stats
    return [FieldRecord(field_id=f, status=Status.FALHA, method=Method.LLM,
                        note=f"llm: {stats.error}") for f in field_ids], stats


def extract_with_llm(doc: LocatedText, field_ids: list[str], model: str = DEFAULT_MODEL
                     ) -> tuple[dict[str, FieldRecord], list[CallStats]]:
    """Run one call per group for the given fields (serially: a single GPU, D13)."""
    groups: dict[str, list[str]] = {}
    for fid in field_ids:
        groups.setdefault(FIELDS[fid].group, []).append(fid)
    records, calls = {}, []
    for group_fields in groups.values():
        recs, stats = extract_group(doc, group_fields, model)
        records.update({r.field_id: r for r in recs})
        calls.append(stats)
    return records, calls


def combine(rule_records: list[FieldRecord], llm_records: dict[str, FieldRecord]) -> list[FieldRecord]:
    """Hybrid: keep what the rules extracted; replace the rest by the LLM answer."""
    out = []
    for r in rule_records:
        llm = llm_records.get(r.field_id)
        out.append(llm if (llm and r.status != Status.EXTRAIDO) else r)
    return out

