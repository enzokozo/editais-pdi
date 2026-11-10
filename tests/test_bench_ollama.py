import importlib.util

from editais.config import PROJECT_ROOT

_spec = importlib.util.spec_from_file_location("bench_ollama", PROJECT_ROOT / "scripts" / "bench_ollama.py")
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)


def test_summarize_converts_nanoseconds_to_rates():
    resp = {
        "response": " 15 de marco de 2026. ",
        "load_duration": 2_000_000_000,
        "total_duration": 5_000_000_000,
        "prompt_eval_count": 60,
        "prompt_eval_duration": 500_000_000,
        "eval_count": 20,
        "eval_duration": 1_000_000_000,
    }
    s = bench.summarize(resp)
    assert s["load_s"] == 2.0
    assert s["total_s"] == 5.0
    assert s["prompt_tok_per_s"] == 120.0
    assert s["output_tok_per_s"] == 20.0
    assert s["answer"] == "15 de marco de 2026."


def test_summarize_handles_missing_counters():
    s = bench.summarize({"response": "x"})
    assert s["output_tok_per_s"] is None
    assert s["prompt_tok_per_s"] is None


def test_build_prompt_puts_question_last():
    prompt = bench.build_prompt(3)
    assert prompt.count(bench.FILLER) == 3
    assert prompt.endswith(bench.PROMPT)


def test_build_prompt_differs_per_run_to_avoid_cache():
    assert bench.build_prompt(3, 0) != bench.build_prompt(3, 1)
