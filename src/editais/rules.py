"""Rule-based extraction: the first level of the cascade (decision D10).

Rules cover fields whose wording is regular across calls (money, months, days,
a deadline written as a sentence). They search the body of the call only, and
every hit goes through the literal evidence check.

A rule can say "found", never "the call is silent": a regex that does not match
proves nothing. Fields without a hit therefore come out as `incerto`, to be
handed to the LLM (hybrid) or to the reviewer (rules only) (decision D41).
"""
import re
from dataclasses import dataclass
from typing import Callable

from editais.evidence import LocatedText, check_evidence
from editais.schema import FIELDS, Evidence, FieldRecord, Method, Status

MONEY = r"R\$ ?\d{1,3}(?:\.\d{3})*(?:, ?\d{2})?"
NUMBER_WORD = r"\d+ ?(?:\([a-zà-ú ]+\) ?)?"   # "12 (doze) " or "24 "
MAX_SENTENCE = 300


@dataclass(frozen=True)
class Rule:
    pattern: re.Pattern
    normalize: Callable[[str], str | None]
    must_contain: tuple[str, ...] = ()     # at least one of these words in the sentence


def norm_money(value: str) -> str | None:
    digits = re.sub(r"[^\d,]", "", value).replace(",", ".")
    try:
        return f"{float(digits):.2f}"
    except ValueError:
        return None


def norm_int(value: str) -> str | None:
    m = re.match(r"\d+", value)
    return m.group() if m else None


def norm_percent(value: str) -> str | None:
    m = re.match(r"\d+(?:,\d+)?", value)
    return m.group().replace(",", ".") if m else None


def norm_datetime(value: str) -> str | None:
    """'dezessete horas (17:00) do dia 23/7/2021' -> '2021-07-23 17:00'."""
    d = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
    if not d:
        return None
    day, month, year = (int(x) for x in d.groups())
    t = re.search(r"(\d{1,2})(?::(\d{2})|h(\d{2})?)", value[:d.start()] + " " + value[d.end():])
    time = f" {int(t.group(1)):02d}:{t.group(2) or t.group(3) or '00'}" if t else ""
    return f"{year:04d}-{month:02d}-{day:02d}{time}"


FLAGS = re.IGNORECASE
RULES: dict[str, Rule] = {
    "P2": Rule(re.compile(r"propostas? ser[aã]o recebidas[^.]{0,120}?at[eé] [àa]s? "
                          r"([^.;]{0,60}?\d{1,2}/\d{1,2}/\d{4})", FLAGS), norm_datetime),
    "P4": Rule(re.compile(r"prazo de (?:execu[cç][aã]o|dura[cç][aã]o)[^.]{0,80}?(" + NUMBER_WORD + r"meses)", FLAGS),
               norm_int),
    "L1": Rule(re.compile(r"(?:valor global de|da ordem de|valor total de)(?: at[eé])? (" + MONEY + ")", FLAGS),
               norm_money),
    "L2": Rule(re.compile(r"m[ií]nimo de (" + MONEY + ")", FLAGS), norm_money, ("propost", "solicit")),
    "L3": Rule(re.compile(r"m[áa]ximo de (" + MONEY + ")", FLAGS), norm_money, ("propost", "solicit")),
    "L4": Rule(re.compile(r"contrapartida.{0,250}?(\d{1,3}(?:,\d+)? ?%)", FLAGS), norm_percent),
    "E5": Rule(re.compile(r"recursos?[^.]{0,200}?no prazo de (" + NUMBER_WORD + r"dias(?: corridos| [úu]teis)?)",
                          FLAGS), norm_int),
}


ITEM_NUMBER = re.compile(r"(?:^| )\d{1,2}(?:\.\d{1,3})+\.? ")   # " 3.1 ", " 5.7.4.2. "


def sentence_around(text: str, start: int, end: int, max_chars: int = MAX_SENTENCE) -> str:
    """The sentence containing text[start:end]. It starts after '. ', '; ', ': ' or at an item
    number such as '3.1' (so section titles stay out), and ends at '. ' or '; ' (not at '2.000')."""
    left = max(text.rfind(sep, 0, start) for sep in (". ", "; ", ": "))
    left = 0 if left < 0 else left + 2
    items = [m.start() + 1 for m in ITEM_NUMBER.finditer(text, max(0, start - max_chars), start)]
    if items and items[-1] > left:
        left = items[-1]
    right = min((i for i in (text.find(sep, end) for sep in (". ", "; ")) if i >= 0), default=len(text))
    left = max(left, end - max_chars)
    return text[left:min(right + 1, left + max_chars)].strip()


def apply_rule(field_id: str, rule: Rule, doc: LocatedText) -> FieldRecord:
    spec = FIELDS[field_id]
    body_end = doc.starts[doc.annex_start] if doc.annex_start is not None else len(doc.text)
    body = doc.text[:body_end]
    for m in rule.pattern.finditer(body):
        sentence = sentence_around(body, m.start(), m.end())
        if rule.must_contain and not any(w in sentence.lower() for w in rule.must_contain):
            continue
        value = m.group(1).strip()
        check = check_evidence(sentence, value, doc)
        if not check.ok:
            return FieldRecord(field_id=field_id, status=Status.INCERTO, value=None,
                               method=Method.REGRA, note=f"rules: evidence rejected ({check.reason})")
        return FieldRecord(
            field_id=field_id, status=Status.EXTRAIDO,
            value=[value] if spec.multivalued else value,
            normalized=rule.normalize(value), method=Method.REGRA,
            evidence=[Evidence(text=sentence, page=check.page, region=check.region)])
    return FieldRecord(field_id=field_id, status=Status.INCERTO, method=Method.REGRA, note="rules: no match")


def extract_with_rules(doc: LocatedText) -> list[FieldRecord]:
    """One record per field, in catalog order."""
    return [apply_rule(fid, RULES[fid], doc) if fid in RULES
            else FieldRecord(field_id=fid, status=Status.INCERTO, note="rules: field not covered")
            for fid in FIELDS]
