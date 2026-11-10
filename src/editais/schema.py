"""Data contracts shared by the agents: the 18 fields and the per-field record (decision D26)."""
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Status(StrEnum):
    EXTRAIDO = "extraido"
    NAO_INFORMADO = "nao_informado"   # the call is silent about it: not an error (D12)
    INCERTO = "incerto"               # something was found but could not be confirmed
    FALHA = "falha"                   # the pipeline failed for this field


class Method(StrEnum):
    REGRA = "regra"
    LLM = "llm"
    REGRA_LLM = "regra+llm"


Region = Literal["corpo", "anexo"]
Kind = Literal["date", "datetime", "months", "days", "money", "percent_or_money", "text"]


@dataclass(frozen=True)
class FieldSpec:
    id: str
    name: str
    group: str          # P, L, T, I, E: one LLM call per group
    kind: Kind
    multivalued: bool = False


FIELDS: dict[str, FieldSpec] = {f.id: f for f in [
    FieldSpec("P1", "inicio_submissao", "P", "date", multivalued=True),
    FieldSpec("P2", "fim_submissao", "P", "datetime"),
    FieldSpec("P3", "resultado_preliminar", "P", "date"),
    FieldSpec("P4", "prazo_execucao_projeto", "P", "months"),
    FieldSpec("L1", "valor_global", "L", "money"),
    FieldSpec("L2", "valor_min_proposta", "L", "money"),
    FieldSpec("L3", "valor_max_proposta", "L", "money"),
    FieldSpec("L4", "contrapartida_minima", "L", "percent_or_money"),
    FieldSpec("T1", "tipo_proponente", "T", "text"),
    FieldSpec("T2", "titulacao_minima", "T", "text"),
    FieldSpec("T3", "vinculo_exigido", "T", "text"),
    FieldSpec("I1", "regras_pi", "I", "text", multivalued=True),
    FieldSpec("E1", "objetivo", "E", "text"),
    FieldSpec("E2", "areas_tematicas", "E", "text", multivalued=True),
    FieldSpec("E3", "criterios_avaliacao", "E", "text", multivalued=True),
    FieldSpec("E4", "documentos_exigidos", "E", "text", multivalued=True),
    FieldSpec("E5", "prazo_recurso", "E", "days"),
    FieldSpec("E6", "prazo_prestacao_contas", "E", "text"),
]}


class Evidence(BaseModel):
    text: str = Field(min_length=1)
    page: int = Field(ge=1)
    region: Region


class FieldRecord(BaseModel):
    field_id: str
    status: Status
    value: str | list[str] | None = None     # literal, as written in the call (D37)
    normalized: str | None = None            # filled by code (ISO date, number), never by the LLM
    evidence: list[Evidence] = []
    method: Method | None = None
    note: str | None = None                  # reason for incerto / falha

    @field_validator("field_id")
    @classmethod
    def known_field(cls, v):
        if v not in FIELDS:
            raise ValueError(f"unknown field {v!r}")
        return v

    @model_validator(mode="after")
    def coherent(self):
        spec = FIELDS[self.field_id]
        if self.status == Status.EXTRAIDO:
            if not self.value or not self.evidence:
                raise ValueError("extraido requires a value and at least one evidence")
            if spec.multivalued != isinstance(self.value, list):
                raise ValueError(f"{self.field_id} expects {'a list' if spec.multivalued else 'a single'} value")
        if self.status in (Status.NAO_INFORMADO, Status.FALHA) and (self.value or self.evidence):
            raise ValueError(f"{self.status} requires no value and no evidence")
        return self


class EditalExtraction(BaseModel):
    edital_id: str
    source: str                 # portal, e.g. "fapemig" (D32)
    document_id: str
    document_hash: str
    fields: list[FieldRecord]

    @model_validator(mode="after")
    def all_fields_once(self):
        ids = [f.field_id for f in self.fields]
        if sorted(ids) != sorted(FIELDS):
            missing = set(FIELDS) - set(ids)
            repeated = {i for i in ids if ids.count(i) > 1}
            raise ValueError(f"every field exactly once; missing={sorted(missing)} repeated={sorted(repeated)}")
        return self
