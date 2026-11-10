import pytest
from pydantic import ValidationError

from editais.schema import FIELDS, EditalExtraction, Evidence, FieldRecord, Status

EV = [Evidence(text="ate as 17h do dia 15 de marco de 2026", page=3, region="corpo")]


def test_catalog_has_18_fields_in_5_groups():
    assert len(FIELDS) == 18
    assert {f.group for f in FIELDS.values()} == {"P", "L", "T", "I", "E"}


def test_extracted_record_is_valid():
    r = FieldRecord(field_id="P2", status="extraido", value="17h do dia 15 de marco de 2026",
                    evidence=EV, method="llm")
    assert r.status == Status.EXTRAIDO


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        FieldRecord(field_id="X9", status="nao_informado")


def test_extracted_without_evidence_is_rejected():
    with pytest.raises(ValidationError):
        FieldRecord(field_id="P2", status="extraido", value="15/03/2026")


def test_not_informed_with_value_is_rejected():
    with pytest.raises(ValidationError):
        FieldRecord(field_id="P2", status="nao_informado", value="15/03/2026")


def test_multivalued_field_requires_list():
    with pytest.raises(ValidationError):
        FieldRecord(field_id="E2", status="extraido", value="Saude", evidence=EV)
    r = FieldRecord(field_id="E2", status="extraido", value=["Saude", "Energia"], evidence=EV)
    assert r.value == ["Saude", "Energia"]


def test_single_field_rejects_list():
    with pytest.raises(ValidationError):
        FieldRecord(field_id="P2", status="extraido", value=["a", "b"], evidence=EV)


def test_uncertain_may_keep_value_for_review():
    r = FieldRecord(field_id="T2", status="incerto", value="doutor", note="evidence not literal")
    assert r.status == Status.INCERTO


def _all_not_informed():
    return [FieldRecord(field_id=f, status="nao_informado") for f in FIELDS]


def test_extraction_requires_every_field_once():
    ok = EditalExtraction(edital_id="FAPEMIG-116", source="fapemig", document_id="845",
                          document_hash="abc", fields=_all_not_informed())
    assert len(ok.fields) == 18
    with pytest.raises(ValidationError):
        EditalExtraction(edital_id="FAPEMIG-116", source="fapemig", document_id="845",
                         document_hash="abc", fields=_all_not_informed()[:-1])
