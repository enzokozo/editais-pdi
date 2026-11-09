# editais-pdi

LLM-based multi-agent pipeline for capturing and screening R&D funding calls from Brazilian agencies. FAPEMIG is the first supported source.

## Features (current)

- Collects call records from the FAPEMIG portal API and downloads their PDFs.
- Selects the main document and its latest version (retifications).
- Extracts and normalizes PDF text (ligatures, SEI footers).
- Detects document structure and classifies topics with deterministic rules.

## Planned

- Extraction of 18 fields (deadlines, budget limits, applicant requirements, IP rules, complementary fields) with literal evidence.
- Three agents (Scan, Extraction, Consolidation) orchestrated with a CrewAI Flow.
- Human review sheet and evaluation against a manual gold standard.

## Tech stack

- Python 3.11
- pytest
- CrewAI and Ollama (planned)

## Getting started (Windows PowerShell)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pip install -e .
pytest -q
```

## Project layout

```
src/editais/
tests/
scripts/
gold/         manual gold standard
data/         local data, not versioned
```

## Next steps

- Local LLM setup with Ollama and structured outputs.
- Rule-based extraction and end-to-end Flow.
