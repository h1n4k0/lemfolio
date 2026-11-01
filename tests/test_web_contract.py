"""Behavioral regressions for the real compiled TypeScript UI (no AI calls)."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("scenario", [
    "reject-preserves-draft",
    "restore-warns-about-unsaved-draft",
    "project-switch-detaches-runtime",
    "async-save-keeps-later-keystrokes-dirty",
    "stale-editor-cannot-bind-a-new-proposal",
    "late-run-list-keeps-explicit-selection",
])
def test_editor_and_project_state_survive_user_operations(scenario):
    root = Path(__file__).resolve().parents[1]
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for compiled TypeScript behavior tests")
    result = subprocess.run([node, str(root / "tests" / "web_contract.mjs"), scenario],
                            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
