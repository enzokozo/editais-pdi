"""End-to-end pipeline: one CrewAI Flow run per call (decisions D13, D24, D30).

    Scan agent (Varredura)        -> main document of the call and its text
    Extraction agent (Extracao)   -> 18 field records (rules for now; LLM fallback in step 10)
    Consolidation agent           -> consistency checks, JSON and review rows

Calls are processed one after another (a single GPU, D13). Each step's time is logged;
the run summary reports the execution success rate (TSE) and the median latency.

Usage:
  python -m editais.pipeline FAPEMIG-192 FAPEMIG-116 FAPEMIG-132
"""
import argparse
import csv
import statistics
import time
from datetime import datetime

from crewai.flow.flow import Flow, listen, start
from pydantic import BaseModel

from editais.config import OUTPUT_DIR
from editais.consolidation import check_consistency, review_rows, save_json, write_review_sheet
from editais.corpus import load_call
from editais.evidence import index_document
from editais.rules import extract_with_rules
from editais.schema import EditalExtraction, FieldRecord, Status


# ----------------------------------------------------------------------------- steps (plain functions)
def scan_step(edital_id: str) -> tuple[dict, dict]:
    """Scan agent: resolve the main document chosen by the collector and load its text."""
    return load_call(edital_id)


def extraction_step(doc: dict) -> list[FieldRecord]:
    """Extraction agent: rules first; uncovered fields stay `incerto` for the LLM (step 10)."""
    return extract_with_rules(index_document(doc))


def consolidation_step(edital_id: str, row: dict, doc: dict, fields: list[FieldRecord]):
    """Consolidation agent: build the call record, check it, save JSON, prepare review rows."""
    ext = EditalExtraction(edital_id=edital_id, source=edital_id.split("-")[0].lower(),
                           document_id=row["id_anexo"], document_hash=doc["sha256"], fields=fields)
    issues = check_consistency(ext)
    save_json(ext, issues, OUTPUT_DIR / "extractions")
    return ext, issues, review_rows(ext, issues)


# ----------------------------------------------------------------------------- flow
class CallState(BaseModel):
    edital_id: str = ""
    timings: dict[str, float] = {}
    error: str | None = None
    n_extracted: int = 0
    n_pending: int = 0
    rows: list[dict] = []


class CallFlow(Flow[CallState]):
    """Scan -> Extraction -> Consolidation for one call. A failed step skips the next ones."""

    def _timed(self, name: str, fn, *args):
        if self.state.error:
            return None
        t0 = time.perf_counter()
        try:
            return fn(*args)
        except Exception as e:  # recorded for the TSE instead of stopping the whole run
            self.state.error = f"{name}: {e.__class__.__name__}: {e}"
            return None
        finally:
            self.state.timings[name] = round(time.perf_counter() - t0, 3)

    # Each listener receives the return value of the step it listens to (CrewAI Flow).
    @start()
    def scan(self):
        return self._timed("scan", scan_step, self.state.edital_id)

    @listen(scan)
    def extract(self, loaded):
        fields = self._timed("extract", extraction_step, loaded[1]) if loaded else None
        return loaded, fields

    @listen(extract)
    def consolidate(self, previous):
        loaded, fields = previous
        if self.state.error:
            return self.state
        row, doc = loaded
        result = self._timed("consolidate", consolidation_step, self.state.edital_id, row, doc, fields)
        if result:
            _, _, rows = result
            self.state.rows = rows
            self.state.n_extracted = sum(r["Status"] == Status.EXTRAIDO for r in rows)
            self.state.n_pending = sum(bool(r["Pendencia"]) for r in rows)
        return self.state


# ----------------------------------------------------------------------------- run
def run(ids: list[str]) -> list[CallState]:
    results = []
    for edital_id in ids:
        t0 = time.perf_counter()
        flow = CallFlow()
        flow.kickoff(inputs={"edital_id": edital_id})
        state = flow.state
        state.timings["total"] = round(time.perf_counter() - t0, 3)
        results.append(state)
    return results


def write_run_log(results: list[CallState]) -> str:
    out = OUTPUT_DIR / "runs" / f"run_{datetime.now():%Y%m%dT%H%M%S}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["edital_id", "ok", "error", "t_scan", "t_extract", "t_consolidate", "t_total",
            "n_extracted", "n_pending"]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for s in results:
            t = s.timings
            w.writerow([s.edital_id, s.error is None, s.error or "", t.get("scan"), t.get("extract"),
                        t.get("consolidate"), t.get("total"), s.n_extracted, s.n_pending])
    return str(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="+")
    args = ap.parse_args()

    results = run(args.ids)
    ok = [s for s in results if s.error is None]
    write_review_sheet([r for s in ok for r in s.rows], OUTPUT_DIR / "revisao_campos.xlsx")
    log = write_run_log(results)

    print("\n===== run summary")
    for s in results:
        status = "OK  " if s.error is None else "FAIL"
        print(f"{status} {s.edital_id}: {s.n_extracted} extracted, {s.n_pending} pending, "
              f"{s.timings.get('total')} s" + (f" | {s.error}" if s.error else ""))
    tse = 100 * len(ok) / len(results)
    median = statistics.median(s.timings["total"] for s in results)
    print(f"TSE = {len(ok)}/{len(results)} = {tse:.0f}% | median latency = {median:.2f} s")
    print(f"run log: {log}")


if __name__ == "__main__":
    main()
