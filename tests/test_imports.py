import importlib

import pytest

from editais.config import USER_AGENT

MODULES = [
    "editais.config",
    "editais.fapemig_collector",
    "editais.downloader",
    "editais.compare_versions",
    "editais.extractor",
    "editais.textnorm",
    "editais.structure",
    "editais.topics",
    "editais.excel_writer",
    "editais.llm",
    "editais.schema",
    "editais.evidence",
    "editais.rules",
    "editais.corpus",
    "editais.consolidation",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name):
    importlib.import_module(module_name)


def test_user_agent_has_no_email():
    assert "@" not in USER_AGENT


def test_user_agent_points_to_repository():
    assert "github.com/" in USER_AGENT
