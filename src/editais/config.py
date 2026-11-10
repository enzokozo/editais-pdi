"""Project-wide settings: paths and HTTP client identity."""
from pathlib import Path

# src/editais/config.py -> parents[2] is the project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
MAIN_DOCS_CSV = PROJECT_ROOT / "documentos_principais.csv"

# Identifies the crawler without exposing personal data (decision D29).
USER_AGENT = "editais-pdi/0.1 (+https://github.com/enzokozo/editais-pdi)"
REQUEST_DELAY_SECONDS = 1.5

# Local Ollama server (default port).
OLLAMA_URL = "http://localhost:11434"
