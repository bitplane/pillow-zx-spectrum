"""The coverage wrapper must preserve failures while writing reports."""

import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("exit_code", [0, 1, 2, 5])
def test_coverage_script_propagates_test_status(tmp_path, exit_code):
    script = Path(__file__).resolve().parents[1] / "scripts" / "coverage.sh"
    activate = tmp_path / ".venv" / "bin" / "activate"
    activate.parent.mkdir(parents=True)
    activate.write_text(
        "pytest() {\n"
        "  echo 'Name Stmts Miss Cover Missing'\n"
        "  echo 'example.py 10 1 90% 42'\n"
        f"  return {exit_code}\n"
        "}\n"
    )
    result = subprocess.run(["bash", str(script), "pillow_zx_spectrum"], cwd=tmp_path, capture_output=True, check=False)
    assert result.returncode == exit_code
    assert b"example.py" in result.stdout
    assert "example.py" in (tmp_path / "htmlcov" / "coverage_report.txt").read_text()
    assert "example.py" in (tmp_path / "htmlcov" / "missing_coverage.txt").read_text()
