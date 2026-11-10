import pytest

pytest.importorskip("crewai")

from editais import pipeline  # noqa: E402

LINES = [
    {"t": "1. OBJETIVO", "bold": True, "size": 11.0},
    {"t": "3.1 Recursos no valor global de R$ 1.000.000,00 (um milhão de reais).", "bold": False, "size": 11.0},
    {"t": "4.1 O prazo de execução será de até 12 (doze) meses.", "bold": False, "size": 11.0},
]
DOC = {"sha256": "abc", "pages": [{"page": 1, "text": "", "lines": LINES}]}


@pytest.fixture
def fake_corpus(monkeypatch, tmp_path):
    def load_call(edital_id):
        if edital_id == "BROKEN-1":
            raise FileNotFoundError("no extracted text")
        return {"id_anexo": "1"}, DOC

    monkeypatch.setattr(pipeline, "load_call", load_call)
    monkeypatch.setattr(pipeline, "OUTPUT_DIR", tmp_path)


def test_flow_runs_three_agents_in_order(fake_corpus):
    [state] = pipeline.run(["TEST-1"])
    assert state.error is None
    assert list(state.timings) == ["scan", "extract", "consolidate", "total"]
    assert len(state.rows) == 18
    assert state.n_extracted == 2      # L1 and P4 found by the rules


def test_failure_is_recorded_and_does_not_stop_the_run(fake_corpus):
    broken, ok = pipeline.run(["BROKEN-1", "TEST-1"])
    assert broken.error.startswith("scan: FileNotFoundError")
    assert ok.error is None
