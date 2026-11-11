"""Structured calls to the local LLM through Ollama's chat API.

Ollama receives a JSON Schema in `format` and constrains generation to it;
Pydantic then validates the answer again on our side (decisions D21, D22).
"""
from dataclasses import dataclass

import requests
from pydantic import BaseModel, ValidationError

from editais.config import DEFAULT_MODEL, NUM_CTX, OLLAMA_URL, SEED

NS_PER_S = 1e9


class LLMError(Exception):
    """The model answered, but not with valid data for the requested schema."""


class OllamaUnavailable(Exception):
    """The Ollama server is not running or not reachable."""


@dataclass
class LLMResult:
    data: BaseModel          # validated answer
    raw: str                 # exact text returned by the model
    prompt_tokens: int
    output_tokens: int
    seconds: float
    near_ctx_limit: bool     # True if the prompt may have been truncated


def inline_refs(schema: dict) -> dict:
    """Replace every {"$ref": "#/$defs/X"} by the definition itself and drop "$defs".
    Nested Pydantic models produce $ref; a flat schema is the safest input for Ollama's
    grammar-constrained decoding."""
    defs = schema.get("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(x) for x in node]
        return node

    return resolve(schema)


def build_payload(prompt: str, schema: type[BaseModel], model: str,
                  system: str | None, num_ctx: int) -> dict:
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": prompt})
    return {
        "model": model,
        "messages": messages,
        "format": inline_refs(schema.model_json_schema()),
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "seed": SEED, "num_ctx": num_ctx},
    }


def ask_json(prompt: str, schema: type[BaseModel], *, model: str = DEFAULT_MODEL,
             system: str | None = None, num_ctx: int = NUM_CTX, timeout: float = 180) -> LLMResult:
    """Send one prompt and return the answer validated against `schema`.

    Raises LLMError if the answer is not valid JSON for the schema, and
    OllamaUnavailable if the Ollama server cannot be reached.
    """
    payload = build_payload(prompt, schema, model, system, num_ctx)
    try:
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
    except requests.ConnectionError as e:
        raise OllamaUnavailable(
            f"Ollama is not reachable at {OLLAMA_URL}. Open the Ollama app or run `ollama serve`."
        ) from e
    r.raise_for_status()
    body = r.json()
    raw = body.get("message", {}).get("content", "")
    try:
        data = schema.model_validate_json(raw)
    except ValidationError as e:
        raise LLMError(f"answer does not match {schema.__name__}: {e.errors()[:3]} | raw={raw[:200]!r}") from e

    prompt_tokens = body.get("prompt_eval_count", 0)
    return LLMResult(
        data=data,
        raw=raw,
        prompt_tokens=prompt_tokens,
        output_tokens=body.get("eval_count", 0),
        seconds=round(body.get("total_duration", 0) / NS_PER_S, 2),
        near_ctx_limit=prompt_tokens >= num_ctx - 64,
    )
