"""Measure load time, speed and repeatability of a local Ollama model.

Usage:
  python scripts/bench_ollama.py --model qwen3.5:2b
  python scripts/bench_ollama.py --model qwen3.5:2b --runs 5 --num-ctx 4096
  python scripts/bench_ollama.py --model qwen3.5:2b --filler 50   # long prompt; check the printed token count
"""
import argparse
import json
import platform
from datetime import datetime

import requests

from editais.config import DATA_DIR, OLLAMA_URL

NS_PER_S = 1e9

PROMPT = (
    "Leia o trecho e responda em uma frase curta qual e o prazo final de submissao.\n\n"
    "Trecho: As propostas deverao ser submetidas exclusivamente pelo sistema eletronico "
    "ate as 17h do dia 15 de marco de 2026, horario de Brasilia."
)


# Neutral filler that mimics the size of real retrieved passages (no text from the test set).
FILLER = (
    "O proponente devera observar as disposicoes gerais desta chamada, os anexos que a integram "
    "e as normas da instituicao de fomento aplicaveis a concessao de apoio financeiro a projetos.\n"
)


def build_prompt(filler: int, run: int = 0) -> str:
    # A different first line per run defeats Ollama's prompt cache, so every run
    # really reads the whole prompt (as each real extraction call will).
    return f"Execucao {run}.\n" + FILLER * filler + "\n" + PROMPT


def summarize(resp: dict) -> dict:
    """Convert Ollama's nanosecond counters into seconds and tokens per second."""
    eval_s = resp.get("eval_duration", 0) / NS_PER_S
    prompt_s = resp.get("prompt_eval_duration", 0) / NS_PER_S
    return {
        "load_s": round(resp.get("load_duration", 0) / NS_PER_S, 2),
        "total_s": round(resp.get("total_duration", 0) / NS_PER_S, 2),
        "prompt_tokens": resp.get("prompt_eval_count", 0),
        "prompt_tok_per_s": round(resp.get("prompt_eval_count", 0) / prompt_s, 1) if prompt_s else None,
        "output_tokens": resp.get("eval_count", 0),
        "output_tok_per_s": round(resp.get("eval_count", 0) / eval_s, 1) if eval_s else None,
        "answer": resp.get("response", "").strip(),
    }


def generate(model: str, num_ctx: int, prompt: str) -> dict:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,  # reasoning mode off (decision D22)
        "options": {"temperature": 0, "seed": 42, "num_ctx": num_ctx},
    }
    r = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=300)
    r.raise_for_status()
    return r.json()


def loaded_models() -> list:
    """What `ollama ps` shows: size in memory and how much of it is in VRAM."""
    r = requests.get(f"{OLLAMA_URL}/api/ps", timeout=10)
    r.raise_for_status()
    return [
        {"name": m["name"], "size_gb": round(m["size"] / 1e9, 2),
         "vram_gb": round(m.get("size_vram", 0) / 1e9, 2),
         "pct_gpu": round(100 * m.get("size_vram", 0) / m["size"]) if m["size"] else 0}
        for m in r.json().get("models", [])
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--num-ctx", type=int, default=4096)
    ap.add_argument("--filler", type=int, default=0, help="filler paragraphs before the question")
    args = ap.parse_args()

    version = requests.get(f"{OLLAMA_URL}/api/version", timeout=10).json().get("version")
    runs = []
    for i in range(args.runs):
        s = summarize(generate(args.model, args.num_ctx, build_prompt(args.filler, i)))
        runs.append(s)
        print(f"run {i + 1}: total {s['total_s']} s | load {s['load_s']} s | "
              f"prompt {s['prompt_tokens']} tok @ {s['prompt_tok_per_s']} tok/s | "
              f"output {s['output_tok_per_s']} tok/s | answer: {s['answer'][:70]}")

    # Speed runs use different prompts, so their answers may differ. Repeatability is
    # checked separately: the exact prompt of run 1, sent again.
    again = summarize(generate(args.model, args.num_ctx, build_prompt(args.filler, 0)))
    repeatable = again["answer"] == runs[0]["answer"]

    memory = loaded_models()
    print(f"\nollama {version} | memory: {memory}")
    print(f"same prompt sent twice -> identical answer: {repeatable}")

    out = DATA_DIR / "benchmarks" / f"ollama_{args.model.replace(':', '_').replace('/', '_')}_{datetime.now():%Y%m%dT%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "model": args.model, "ollama_version": version, "num_ctx": args.num_ctx, "filler": args.filler,
        "machine": platform.platform(), "memory": memory, "repeatable": repeatable, "runs": runs,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
