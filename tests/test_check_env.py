import importlib.util
import os

import editais
from editais.config import PROJECT_ROOT

_spec = importlib.util.spec_from_file_location("check_env", PROJECT_ROOT / "scripts" / "check_env.py")
check_env = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_env)


def test_package_versions_reports_installed_and_missing():
    versions = check_env.package_versions(["pytest", "surely-not-a-real-package"])
    assert versions["pytest"][0].isdigit()
    assert versions["surely-not-a-real-package"] == "not installed"


def test_package_import_disables_crewai_telemetry():
    assert editais is not None
    assert os.environ["CREWAI_DISABLE_TELEMETRY"] == "true"
