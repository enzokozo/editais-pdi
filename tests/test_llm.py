from typing import Literal

import pytest
from pydantic import BaseModel

from editais import llm


class Answer(BaseModel):
    status: Literal["extraido", "nao_informado"]
    valor: str | None


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


def fake_ollama(monkeypatch, content, prompt_eval_count=100):
    calls = []

    def fake_post(url, json, timeout):
        calls.append({"url": url, "payload": json})
        return FakeResponse({"message": {"content": content}, "prompt_eval_count": prompt_eval_count,
                             "eval_count": 12, "total_duration": 2_500_000_000})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    return calls


def test_payload_fixes_decoding_parameters():
    payload = llm.build_payload("p", Answer, "m", "sys", 4096)
    assert payload["think"] is False
    assert payload["stream"] is False
    assert payload["options"] == {"temperature": 0, "seed": 42, "num_ctx": 4096}
    assert payload["format"] == llm.inline_refs(Answer.model_json_schema())
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]


def test_payload_without_system_message():
    payload = llm.build_payload("p", Answer, "m", None, 4096)
    assert [m["role"] for m in payload["messages"]] == ["user"]


def test_valid_answer_is_parsed(monkeypatch):
    calls = fake_ollama(monkeypatch, '{"status": "extraido", "valor": "2026-03-15"}')
    result = llm.ask_json("p", Answer)
    assert result.data.valor == "2026-03-15"
    assert result.seconds == 2.5
    assert result.near_ctx_limit is False
    assert calls[0]["url"].endswith("/api/chat")


def test_invalid_json_raises(monkeypatch):
    fake_ollama(monkeypatch, "O prazo e 15 de marco")
    with pytest.raises(llm.LLMError):
        llm.ask_json("p", Answer)


def test_value_outside_schema_raises(monkeypatch):
    fake_ollama(monkeypatch, '{"status": "talvez", "valor": null}')
    with pytest.raises(llm.LLMError):
        llm.ask_json("p", Answer)


def test_flags_prompt_near_context_limit(monkeypatch):
    fake_ollama(monkeypatch, '{"status": "nao_informado", "valor": null}', prompt_eval_count=4090)
    assert llm.ask_json("p", Answer).near_ctx_limit is True


def test_server_down_raises_clear_error(monkeypatch):
    def refuse(url, json, timeout):
        raise llm.requests.ConnectionError("connection refused")

    monkeypatch.setattr(llm.requests, "post", refuse)
    with pytest.raises(llm.OllamaUnavailable, match="ollama serve"):
        llm.ask_json("p", Answer)


def test_inline_refs_removes_defs():
    class Inner(BaseModel):
        x: int

    class Outer(BaseModel):
        a: Inner
        b: list[Inner]

    flat = llm.inline_refs(Outer.model_json_schema())
    assert "$defs" not in flat and "$ref" not in str(flat)
    assert flat["properties"]["a"]["properties"]["x"]["type"] == "integer"
