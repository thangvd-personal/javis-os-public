"""Pytest conftest for tests/contract/ ensuring server/ and repo roots are in sys.path."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = REPO_ROOT / "server"
BTOS_JAVIS_ROOT = REPO_ROOT.parent

for p in (str(SERVER_DIR), str(REPO_ROOT), str(BTOS_JAVIS_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

# Prevent background workers or judge engines from starting during testing
os.environ["JAVIS_NO_REAL_JUDGE_ENGINE"] = "1"
os.environ["JAVIS_NO_REAL_REVIEW_ENGINE"] = "1"
os.environ["JAVIS_NO_REAL_TEAM_ENGINE"] = "1"
