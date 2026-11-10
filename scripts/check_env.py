"""Record the execution environment (for reproducibility and the thesis methods section).

Usage:
  python scripts/check_env.py
  python scripts/check_env.py --crewai-call qwen3.5:2b   # also tests CrewAI -> Ollama
"""
import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime
from importlib import metadata

import requests

from editais.config import DATA_DIR, OLLAMA_URL

PACKAGES = ["crewai", "litellm", "pydantic", "requests", "pymupdf", "openpyxl", "pytest"]


def package_versions(names: list[str]) -> dict:
    versions = {}
    for name in names:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "not installed"
    return versions


def ollama_info() -> dict:
    try:
        version = requests.get(f"{OLLAMA_URL}/api/version", timeout=5).json()["version"]
        models = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5).json()["models"]
        return {"version": version,
                "models": [{"name": m["name"], "id": m["digest"][:12]} for m in models]}
    except requests.RequestException as e:
        return {"error": f"Ollama not reachable: {e.__class__.__name__}"}


def gpu_info() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "nvidia-smi not available"


def crewai_smoke_test(model: str) -> str:
    """One short call through CrewAI's LLM wrapper, to prove the integration works."""
    from crewai import LLM

    llm = LLM(model=f"ollama/{model}", base_url=OLLAMA_URL, temperature=0)
    return str(llm.call("Responda apenas com a palavra: ok")).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crewai-call", metavar="MODEL", help="Ollama model for a CrewAI smoke test")
    args = ap.parse_args()

    env = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": package_versions(PACKAGES),
        "ollama": ollama_info(),
        "gpu": gpu_info(),
    }
    if args.crewai_call:
        try:
            env["crewai_smoke_test"] = crewai_smoke_test(args.crewai_call)
        except Exception as e:  # report any integration failure instead of crashing
            env["crewai_smoke_test"] = f"FAILED: {e.__class__.__name__}: {e}"

    print(json.dumps(env, ensure_ascii=False, indent=2))
    out = DATA_DIR / "env" / f"env_{datetime.now():%Y%m%dT%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
