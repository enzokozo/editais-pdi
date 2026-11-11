import pytest
from pydantic import ValidationError

from editais import llm_extraction as lx
from editais.evidence import index_lines
from editais.llm import LLMResult
from editais.schema import Status

LINES = [
    (1, "1. OBJETIVO"),
    (1, "Apoiar projetos de inovação de empresas mineiras."),
    (2, "4. QUEM PODE PARTICIPAR"),
    (2, "4.1 O coordenador deverá ter título de doutor e vínculo empregatício com a ICT."),
    (3, "11. CRONOGRAMA"),
    (3, "Submissão das propostas"),
    (3, "16/09/2019"),
    (3, "14/11/2019 às 18h"),
    (9, "ANEXO I - MINUTA"),
    (9, "CLÁUSULA DÉCIMA - A titularidade da propriedade intelectual será compartilhada."),
]
DOC = index_lines(LINES, annex_start=8)


def test_retrieval_picks_relevant_passages_within_budget():
    passages = lx.retrieve(DOC, ["T2", "T3"], budget=400)
    joined = " ".join(passages)
    assert "título de doutor" in joined
    assert sum(len(p) for p in passages) <= 400


def test_retrieval_reaches_annexes():
    assert "propriedade intelectual" in " ".join(lx.retrieve(DOC, ["I1"]))


def test_retrieval_without_keywords_returns_nothing():
    assert lx.retrieve(index_lines([(1, "Texto sem relação.")]), ["I1"]) == []


def test_schema_has_one_key_per_field_with_right_shape():
    schema = lx.answer_schema(["P2", "T2"])
    ok = schema.model_validate({
        "P2": {"evidencias": ["14/11/2019 às 18h"], "valores": ["14/11/2019 às 18h"], "status": "extraido"},
        "T2": {"evidencia": None, "valor": None, "status": "nao_informado"}})
    assert ok.P2.valores == ["14/11/2019 às 18h"]
    with pytest.raises(ValidationError):   # a missing key is still invalid
        schema.model_validate({"P2": {"evidencias": [], "valores": [], "status": "extraido"}})


def test_verified_single_answer_becomes_extracted_with_page():
    ans = lx.SingleAnswer(evidencia="deverá ter título de doutor", valor="doutor", status="extraido")
    r = lx.to_record("T2", ans, DOC)
    assert (r.status, r.value, r.evidence[0].page, r.method) == (Status.EXTRAIDO, "doutor", 2, "llm")


def test_invented_quote_becomes_uncertain_and_keeps_value_for_review():
    ans = lx.SingleAnswer(evidencia="deverá ter título de mestre", valor="mestre", status="extraido")
    r = lx.to_record("T2", ans, DOC)
    assert r.status == Status.INCERTO and r.value == "mestre" and "not found" in r.note


def test_list_answer_keeps_only_verified_values_and_normalizes_dates():
    ans = lx.ListAnswer(evidencias=["14/11/2019 às 18h"], valores=["14/11/2019 às 18h", "20/12/2019"],
                        status="extraido")
    r = lx.to_record("P2", ans, DOC)
    assert r.value == ["14/11/2019 às 18h"]
    assert r.normalized == "2019-11-14 18:00"
    assert "1 unverified" in r.note


def test_not_informed_answer():
    ans = lx.SingleAnswer(evidencia="algo", valor=None, status="nao_informado")
    assert lx.to_record("T2", ans, DOC).status == Status.NAO_INFORMADO


def fake_ask(responses):
    calls = []

    def ask(prompt, schema, model, system):
        calls.append(prompt)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return LLMResult(data=schema.model_validate(r), raw="", prompt_tokens=900, output_tokens=50,
                         seconds=4.0, near_ctx_limit=False)
    return ask, calls


GOOD_T = {"T1": {"evidencia": None, "valor": None, "status": "nao_informado"},
          "T2": {"evidencia": "título de doutor", "valor": "doutor", "status": "extraido"},
          "T3": {"evidencia": "vínculo empregatício com a ICT", "valor": "vínculo empregatício",
                 "status": "extraido"}}


def test_group_call_produces_one_record_per_field(monkeypatch):
    ask, calls = fake_ask([GOOD_T])
    monkeypatch.setattr(lx, "ask_json", ask)
    records, stats = lx.extract_group(DOC, ["T1", "T2", "T3"])
    assert [r.field_id for r in records] == ["T1", "T2", "T3"]
    assert [r.status for r in records] == [Status.NAO_INFORMADO, Status.EXTRAIDO, Status.EXTRAIDO]
    assert (stats.attempts, len(calls)) == (1, 1) and stats.seconds >= 0


def test_invalid_answer_is_not_retried_by_default(monkeypatch):
    ask, calls = fake_ask([lx.LLMError("bad")])
    monkeypatch.setattr(lx, "ask_json", ask)
    records, stats = lx.extract_group(DOC, ["T2"])
    assert stats.attempts == 1 and len(calls) == 1
    assert records[0].status == Status.FALHA


def test_one_incoherent_field_does_not_sink_the_group(monkeypatch):
    answer = dict(GOOD_T, T1={"evidencia": "título de doutor", "valor": None, "status": "extraido"})
    ask, _ = fake_ask([answer])
    monkeypatch.setattr(lx, "ask_json", ask)
    records, _ = lx.extract_group(DOC, ["T1", "T2", "T3"])
    assert [r.status for r in records] == [Status.EXTRAIDO, Status.EXTRAIDO, Status.EXTRAIDO]


def test_text_field_without_value_uses_the_literal_quote():
    ans = lx.SingleAnswer(evidencia="Apoiar projetos de inovação de empresas mineiras.", valor=None, status="extraido")
    r = lx.to_record("E1", ans, DOC)
    assert r.status == Status.EXTRAIDO and r.value == "Apoiar projetos de inovação de empresas mineiras."


def test_typed_field_without_value_is_uncertain():
    ans = lx.ListAnswer(evidencias=["Entre 01/10/2026 e 31/01/2027"], valores=[], status="extraido")
    r = lx.to_record("P1", ans, DOC)
    assert r.status == Status.INCERTO and "without a value" in r.note


def test_not_informed_with_value_is_uncertain():
    ans = lx.SingleAnswer(evidencia=None, valor="doutor", status="nao_informado")
    assert lx.to_record("T2", ans, DOC).status == Status.INCERTO


def test_extract_with_llm_makes_one_call_per_group(monkeypatch):
    ask, calls = fake_ask([GOOD_T, {"E1": {"evidencia": "Apoiar projetos de inovação",
                                           "valor": "Apoiar projetos de inovação", "status": "extraido"}}])
    monkeypatch.setattr(lx, "ask_json", ask)
    records, stats = lx.extract_with_llm(DOC, ["T1", "T2", "T3", "E1"])
    assert len(calls) == 2 and [s.group for s in stats] == ["T", "E"]
    assert records["E1"].status == Status.EXTRAIDO


def test_combine_keeps_rule_results():
    from editais.schema import Evidence, FieldRecord
    ev = [Evidence(text="x", page=1, region="corpo")]
    rules = [FieldRecord(field_id="L1", status="extraido", value="R$ 1,00", evidence=ev, method="regra"),
             FieldRecord(field_id="T2", status="incerto", note="rules: field not covered")]
    llm = {"T2": FieldRecord(field_id="T2", status="nao_informado", method="llm"),
           "L1": FieldRecord(field_id="L1", status="nao_informado", method="llm")}
    out = lx.combine(rules, llm)
    assert out[0].method == "regra" and out[1].status == Status.NAO_INFORMADO


def test_timetable_dates_without_keywords_are_retrieved_for_deadlines():
    lines = [(1, "2.3 Cronograma"), (1, "ENTRADA"), (1, "PERÍODO DE"), (1, "SUBMISSÃO DE"), (1, "PROPOSTA"),
             (1, "RESULTADO"), (1, "1ª"), (1, "Entre 01/10/2026 e"), (1, "31/01/2027"),
             (1, "07/04/2026 a 18/05/2026"), (1, "A partir de 15/07/2026")] + \
            [(2, f"Texto geral sem relação número {i}.") for i in range(30)]
    joined = " ".join(lx.retrieve(index_lines(lines), ["P1", "P2", "P3"]))
    assert "07/04/2026 a 18/05/2026" in joined
