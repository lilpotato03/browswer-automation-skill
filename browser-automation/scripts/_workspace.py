"""Where the browser-automation workspace lives. Stdlib only (runs before any venv).

Resolution order for the workspace root:
  1. $BROWSER_AUTOMATION_HOME
  2. ~/.browser-automation.json  {"root": "..."}   (written by bootstrap.py --install)
  3. ~/BrowserAutomation                            (default)

Layout (created by bootstrap.py --install):
  <root>/
    workspace.json        registry: version, created, projects[]
    README.md             what this folder is, for humans
    .mcp.json             browser MCP config (optional; copied into new projects)
    .tools/venv/          shared tooling env (optional, --prefetch)
    projects/<slug>/      one self-contained project per use case
    outputs/<slug>/       a copy of every spreadsheet, one folder per project
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

POINTER = Path.home() / ".browser-automation.json"
DEFAULT_ROOT = Path.home() / "BrowserAutomation"
SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATE = SKILL_DIR / "template"


def resolve_root(explicit: str = "") -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("BROWSER_AUTOMATION_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if POINTER.exists():
        try:
            return Path(json.loads(POINTER.read_text(encoding="utf-8"))["root"]).expanduser()
        except (ValueError, KeyError):
            pass
    return DEFAULT_ROOT


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"


def load_registry(root: Path) -> dict:
    path = root / "workspace.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"version": 1, "created": datetime.now().isoformat(timespec="seconds"),
            "root": str(root), "projects": []}


def save_registry(root: Path, reg: dict) -> None:
    (root / "workspace.json").write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")
