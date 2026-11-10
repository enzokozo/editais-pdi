from editais.evidence import index_lines
from editais.rules import (extract_with_rules, norm_datetime, norm_money, norm_percent,
                           sentence_around)
from editais.schema import FIELDS, Status

# Synthetic call that mirrors the wording observed in the development calls.
LINES = [
    (1, "3. RECURSOS FINANCEIROS"),
    (1, "3.1 Serão destinados recursos no valor global de até R$ 2.000.000,00 (dois milhões de reais)."),
    (2, "3.2 O valor solicitado na proposta deverá estar entre o mínimo de R$ 100.000,00 e o"),
    (2, "máximo de R$ 200.000,00 (duzentos mil reais)."),
    (2, "3.3 A empresa deverá aportar contrapartida financeira mínima de 5% (cinco por cento)."),
    (3, "4.1 O prazo de execução do projeto será de até 24 (vinte e quatro) meses."),
    (3, "5.1 As propostas serão recebidas, eletronicamente, até as dezessete horas (17:00) do dia 23/7/2021."),
    (4, "6.1 Eventuais recursos poderão ser interpostos no prazo de 10 (dez) dias corridos."),
    (5, "ANEXO I - MINUTA"),
    (5, "CLÁUSULA - O valor global de R$ 9.999.999,00 do exemplo não vale."),
]


def records(lines=LINES, annex_start=8):
    return {r.field_id: r for r in extract_with_rules(index_lines(lines, annex_start))}


def test_one_record_per_field():
    assert list(records()) == list(FIELDS)


def test_money_rules_with_page_and_normalized_value():
    r = records()
    assert (r["L1"].value, r["L1"].normalized, r["L1"].evidence[0].page) == ("R$ 2.000.000,00", "2000000.00", 1)
    assert r["L2"].normalized == "100000.00"
    assert r["L3"].normalized == "200000.00"


def test_value_split_across_lines_is_found():
    assert records()["L3"].status == Status.EXTRAIDO


def test_percent_months_days_and_deadline():
    r = records()
    assert r["L4"].normalized == "5"
    assert (r["P4"].value, r["P4"].normalized) == ("24 (vinte e quatro) meses", "24")
    assert r["E5"].normalized == "10"
    assert r["P2"].value == ["dezessete horas (17:00) do dia 23/7/2021"]
    assert r["P2"].normalized == "2021-07-23 17:00"


def test_annex_is_not_searched():
    lines = [(1, "1. OBJETIVO"), (1, "Apoiar projetos."), (2, "ANEXO I"), (2, "valor global de R$ 5,00.")]
    assert records(lines, annex_start=2)["L1"].status == Status.INCERTO


def test_rules_never_claim_absence():
    r = records([(1, "1. OBJETIVO"), (1, "Apoiar projetos de pesquisa.")], annex_start=None)
    assert all(rec.status == Status.INCERTO for rec in r.values())
    assert r["L1"].note == "rules: no match"
    assert r["T2"].note == "rules: field not covered"


def test_min_rule_needs_proposal_context():
    lines = [(1, "a) faturamento bruto mínimo de R$ 1.000.000,00 no último ano.")]
    assert records(lines, annex_start=None)["L2"].status == Status.INCERTO


def test_sentence_starts_at_item_number_not_at_section_title():
    text = "3. RECURSOS FINANCEIROS 3.1 Valor global de R$ 10,00. Fim."
    start = text.index("R$")
    assert sentence_around(text, start, start + 8) == "3.1 Valor global de R$ 10,00."


def test_normalizers():
    assert norm_money("R$ 4.800.000, 00") == "4800000.00"
    assert norm_percent("5%") == "5"
    assert norm_datetime("17h do dia 15/03/2026") == "2026-03-15 17:00"
    assert norm_datetime("dia 1/2/2026") == "2026-02-01"
    assert norm_datetime("sem data") is None
