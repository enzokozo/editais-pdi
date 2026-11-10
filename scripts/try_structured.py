"""Real structured calls on synthetic cases (2 positive, 2 negative).

Usage:
  python scripts/try_structured.py
  python scripts/try_structured.py --model hf.co/unsloth/Qwen3.5-2B-GGUF:Q4_K_M
"""
import argparse
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from editais.config import DEFAULT_MODEL
from editais.llm import LLMError, ask_json

SYSTEM = (
    "Voce extrai informacoes de editais de fomento usando somente o trecho fornecido.\n"
    "1. Em 'evidencia', copie literalmente a menor frase do trecho que responde a pergunta.\n"
    "2. Em 'valor', copie a resposta exatamente como esta escrita no trecho, sem converter formatos.\n"
    "3. Em 'status', use 'extraido' se encontrou a resposta. Use 'nao_informado' somente se o trecho "
    "nao contem a resposta; nesse caso, 'evidencia' e 'valor' devem ser null.\n"
    "Nunca invente informacoes."
)


class Deadline(BaseModel):
    # Field order matters: Ollama generates fields in schema order, so the model
    # copies the evidence and the value before committing to a status.
    evidencia: str | None = Field(description="Menor frase copiada literalmente do trecho, ou null")
    valor: str | None = Field(description="Prazo exatamente como escrito no trecho, ou null")
    status: Literal["extraido", "nao_informado"]

    @model_validator(mode="after")
    def status_matches_content(self):
        if self.status == "extraido" and not (self.valor and self.evidencia):
            raise ValueError("status 'extraido' requires valor and evidencia")
        if self.status == "nao_informado":
            if self.valor:
                raise ValueError("status 'nao_informado' requires a null valor")
            # The evidence is written before the decision, so the model often quotes the
            # sentence it inspected. Without a value it proves nothing: discard it.
            self.evidencia = None
        return self


CASES = [
    ("positivo-1",
     "4.1 As propostas deverao ser submetidas exclusivamente pelo sistema eletronico "
     "ate as 17h do dia 15 de marco de 2026, horario de Brasilia. 4.2 Nao serao aceitas "
     "propostas enviadas por e-mail.",
     "extraido"),
    ("positivo-2",
     "O periodo de envio das propostas tem inicio na data de publicacao desta chamada e "
     "se encerra em 30/04/2026. Propostas incompletas serao desclassificadas.",
     "extraido"),
    ("negativo-1",
     "4.1 As propostas deverao conter plano de trabalho, cronograma de execucao e "
     "orcamento detalhado, conforme modelo do Anexo II.",
     "nao_informado"),
    ("negativo-2",
     "O resultado final sera divulgado no portal da instituicao. Os recursos serao "
     "liberados em parcelas, conforme o cronograma aprovado.",
     "nao_informado"),
]


def check_evidence(evidence: str | None, value: str | None, text: str) -> str:
    """Literal is not enough: the evidence must contain the value and be shorter than the passage."""
    if evidence is None:
        return "n/a"
    problems = []
    if evidence not in text:
        problems.append("not literal")
    if value and value not in evidence:
        problems.append("value not in evidence")
    if len(evidence) >= len(text):
        problems.append("whole passage copied")
    return "ok" if not problems else ", ".join(problems)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    hits = 0
    for name, text, expected in CASES:
        prompt = f"Qual e o prazo final de submissao das propostas?\n\nTrecho:\n{text}"
        try:
            r = ask_json(prompt, Deadline, model=args.model, system=SYSTEM)
        except LLMError as e:
            print(f"[{name}] INVALID: {e}")
            continue
        d = r.data
        hits += d.status == expected
        print(f"[{name}] {r.seconds} s | status={d.status} (expected {expected}) | valor={d.valor!r} | "
              f"evidence: {check_evidence(d.evidencia, d.valor, text)}")
        if d.evidencia:
            print(f"    evidencia={d.evidencia!r}")
    print(f"\ncorrect status: {hits}/{len(CASES)}")


if __name__ == "__main__":
    main()
