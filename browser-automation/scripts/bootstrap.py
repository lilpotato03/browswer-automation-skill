"""Check prerequisites and set up the browser-automation workspace.

  python3 bootstrap.py                     check only (safe; run on every skill load)
  python3 bootstrap.py --json              same, machine-readable
  python3 bootstrap.py --install           create the workspace folder structure
        [--root PATH]                      somewhere other than ~/BrowserAutomation
        [--mcp playwright|chrome-devtools] write <root>/.mcp.json for Claude-driven tracing
        [--prefetch]                       build <root>/.tools/venv and download Chromium now
  python3 bootstrap.py --status            list projects and their last run

Never installs system software (Python, Node). It reports what is missing and
how to get it; Claude asks the user before installing those.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _workspace import POINTER, TEMPLATE, load_registry, resolve_root, save_registry  # noqa: E402

MCP_SERVERS = {
    "playwright": {"command": "npx", "args": ["@playwright/mcp@latest", "--output-dir", "./mcp-output",
                                             "--save-session"]},
    "chrome-devtools": {"command": "npx", "args": ["chrome-devtools-mcp@latest"]},
}
MIN_NODE = {"playwright": (18, 0), "chrome-devtools": (20, 19)}


def _version(cmd: list) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return (out.stdout or out.stderr).strip().splitlines()[0] if out.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return ""


def _parse(v: str) -> tuple:
    m = re.search(r"(\d+)\.(\d+)", v)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def find_python() -> tuple:
    """Best Python 3.9+ on this machine (the one projects' .venv will be built from)."""
    names = ["py -3"] if platform.system() == "Windows" else []
    names += ["python3.12", "python3.11", "python3.13", "python3.10", "python3", "python"]
    for n in names:
        v = _version(n.split() + ["--version"])
        if v and _parse(v) >= (3, 9):
            return n, v
    return "", ""


def check(root: Path) -> dict:
    py_cmd, py_ver = find_python()
    node = _version(["node", "--version"])
    npx = bool(shutil.which("npx"))
    try:
        urllib.request.urlopen("https://pypi.org/simple/playwright/", timeout=6)
        online = True
    except Exception:  # noqa: BLE001 - any failure = treat as offline
        online = False
    probe = root if root.exists() else Path.home()
    free_gb = round(shutil.disk_usage(probe).free / 1e9, 1)
    reg = load_registry(root) if (root / "workspace.json").exists() else None
    items = [
        {"name": "Python 3.9+", "ok": bool(py_cmd), "found": py_ver or "not found",
         "needed_for": "everything", "fix": "Install from https://www.python.org/downloads/ "
         "(Windows: tick 'Add python.exe to PATH'); macOS: `brew install python@3.12` also works"},
        {"name": "Internet access", "ok": online, "found": "online" if online else "offline",
         "needed_for": "first setup (packages + Chromium download)", "fix": "Connect, then re-run"},
        {"name": "Free disk >= 1 GB", "ok": free_gb >= 1, "found": f"{free_gb} GB",
         "needed_for": "Chromium + per-project environments", "fix": "Free up space"},
        {"name": "Node.js 18+ (Playwright MCP)", "ok": _parse(node) >= MIN_NODE["playwright"] and npx,
         "found": node or "not found", "needed_for": "only Claude-driven tracing",
         "fix": "https://nodejs.org (LTS) or `brew install node`", "optional": True},
        {"name": "Node.js 20.19+ (Chrome DevTools MCP)", "ok": _parse(node) >= MIN_NODE["chrome-devtools"] and npx,
         "found": node or "not found", "needed_for": "only if using Chrome DevTools MCP",
         "fix": "https://nodejs.org (LTS)", "optional": True},
        {"name": "Workspace", "ok": reg is not None, "found": str(root) if reg else f"{root} (not created)",
         "needed_for": "all projects live here", "fix": "python3 bootstrap.py --install"},
        {"name": "Workspace MCP config", "ok": (root / ".mcp.json").exists(),
         "found": ".mcp.json present" if (root / ".mcp.json").exists() else "none",
         "needed_for": "only Claude-driven tracing", "fix": "python3 bootstrap.py --install --mcp playwright",
         "optional": True},
    ]
    required_ok = all(i["ok"] for i in items if not i.get("optional"))
    return {"os": f"{platform.system()} {platform.release()}", "python_for_projects": py_cmd,
            "root": str(root), "ready": required_ok, "items": items}


def print_report(rep: dict) -> None:
    print(f"\nBrowser Automation: prerequisites ({rep['os']})\n")
    for i in rep["items"]:
        mark = "OK " if i["ok"] else ("-- " if i.get("optional") else "XX ")
        print(f"  [{mark}] {i['name']:38s} {i['found']}")
        if not i["ok"]:
            print(f"         needed for: {i['needed_for']}\n         fix: {i['fix']}")
    print(f"\n  Workspace root: {rep['root']}")
    print("  READY" if rep["ready"] else "  NOT READY: fix the [XX] items above")
    print("  ([--] = optional, only for Claude-driven tracing)\n")


def install(root: Path, mcp: str, prefetch: bool, py_cmd: str) -> None:
    for sub in ("projects", "outputs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    reg = load_registry(root)
    reg["root"] = str(root)
    save_registry(root, reg)
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Browser Automation workspace\n\n"
            "Managed by the `browser-automation` Claude skill. Don't move files around by hand.\n\n"
            "- `projects/<name>/`: one folder per automation (double-click `Run Automation` inside)\n"
            "- `outputs/<name>/`: every spreadsheet each automation produced\n"
            "- `workspace.json`: list of projects\n"
            "- `.mcp.json`: lets Claude drive a browser when opened in this folder\n", encoding="utf-8")
    if mcp:
        path = root / ".mcp.json"
        cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"mcpServers": {}}
        cfg.setdefault("mcpServers", {})[mcp] = MCP_SERVERS[mcp]
        path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    # Remember the root so every later session (and script) finds the same workspace,
    # unless the environment variable is what chose it.
    if not os.environ.get("BROWSER_AUTOMATION_HOME"):
        POINTER.write_text(json.dumps({"root": str(root)}) + "\n", encoding="utf-8")
    if prefetch:
        venv = root / ".tools" / "venv"
        if not venv.exists():
            subprocess.run(py_cmd.split() + ["-m", "venv", str(venv)], check=True)
        py = venv / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")
        subprocess.run([str(py), "-m", "pip", "install", "-q", "-r", str(TEMPLATE / "requirements.txt")], check=True)
        subprocess.run([str(py), "-m", "playwright", "install", "chromium"], check=True)
    print(f"Workspace ready at {root}"
          + (f"  (MCP: {mcp} -> {root / '.mcp.json'})" if mcp else "")
          + ("  (Chromium downloaded)" if prefetch else ""))


def status(root: Path) -> None:
    reg = load_registry(root)
    projects = sorted((root / "projects").glob("*/settings.json")) if (root / "projects").exists() else []
    print(f"Workspace: {root}   projects: {len(projects)}")
    for s in projects:
        proj = s.parent
        db = proj / "data" / "leads.db"
        last = "never run"
        if db.exists():
            row = sqlite3.connect(str(db)).execute(
                "SELECT started_at, status, new_leads, errors FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                last = f"last run {row[0]} {row[1]} (new {row[2]}, problems {row[3]})"
        sites = [p.stem for p in (proj / "scraper" / "sites").glob("*.py") if not p.stem.startswith("_")]
        print(f"  - {proj.name:28s} sites: {', '.join(sites) or '(none yet)'}   {last}")
    known = {p["slug"] for p in reg.get("projects", [])}
    stray = [p.parent.name for p in projects if p.parent.name not in known]
    if stray:
        print(f"  (not in workspace.json: {', '.join(stray)})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--root", default="")
    ap.add_argument("--mcp", choices=sorted(MCP_SERVERS), default="")
    ap.add_argument("--prefetch", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = resolve_root(a.root)

    if a.status:
        status(root)
        return
    rep = check(root)
    if a.install:
        if not rep["python_for_projects"]:
            sys.exit("Python 3.9+ not found; install it first (see the report).")
        install(root, a.mcp, a.prefetch, rep["python_for_projects"])
        rep = check(root)
    if a.json:
        print(json.dumps(rep, indent=2))
    else:
        print_report(rep)


if __name__ == "__main__":
    main()
