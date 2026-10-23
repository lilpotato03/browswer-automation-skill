"""Create a new project inside the workspace (never anywhere else).

  python3 new_project.py "Solar Installer Leads" --purpose "Daily list of ..." [--setup]

Creates <root>/projects/<slug>/ from the template, with:
  - its own project-confined skill   .claude/skills/<slug>/SKILL.md
  - settings that copy every output   to <root>/outputs/<slug>/
  - the workspace's .mcp.json         (if there is one)
  - --setup: its own .venv with Playwright + openpyxl, and Chromium downloaded
and registers it in <root>/workspace.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _workspace import TEMPLATE, load_registry, resolve_root, save_registry, slugify  # noqa: E402
from bootstrap import find_python  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="human-friendly project name, e.g. 'Solar Installer Leads'")
    ap.add_argument("--purpose", default="", help="one or two sentences: what it collects and for whom")
    ap.add_argument("--slug", default="", help="folder name (default: from the name)")
    ap.add_argument("--root", default="", help="workspace root (default: resolved workspace)")
    ap.add_argument("--setup", action="store_true", help="also build .venv and download Chromium")
    a = ap.parse_args()

    root = resolve_root(a.root)
    if not (root / "workspace.json").exists():
        sys.exit(f"No workspace at {root}. Run:  python3 bootstrap.py --install")
    slug = slugify(a.slug or a.name)
    dest = root / "projects" / slug
    if dest.exists():
        sys.exit(f"{dest} already exists. Pick another name, or work in the existing project.")

    shutil.copytree(TEMPLATE, dest, ignore=shutil.ignore_patterns("__pycache__", ".venv", "*.pyc"))
    for sub in ("traces", "output", "logs", "state", "data"):
        (dest / sub).mkdir(exist_ok=True)
    outputs = root / "outputs" / slug
    outputs.mkdir(parents=True, exist_ok=True)
    now = datetime.now()

    # Project-confined skill
    skill_src = Path(__file__).resolve().parent / "project_skill.md.tmpl"
    skill_dst = dest / ".claude" / "skills" / slug / "SKILL.md"
    skill_dst.parent.mkdir(parents=True, exist_ok=True)
    text = skill_src.read_text(encoding="utf-8")
    for k, v in {"{{slug}}": slug, "{{name}}": a.name, "{{project_dir}}": str(dest),
                 "{{outputs_dir}}": str(outputs), "{{created_date}}": now.strftime("%Y-%m-%d"),
                 "{{purpose}}": a.purpose or "_(describe what this project collects and for whom)_"}.items():
        text = text.replace(k, v)
    skill_dst.write_text(text, encoding="utf-8")

    settings_path = dest / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["project_name"] = a.name
    settings["project_slug"] = slug
    settings["output"]["copy_to"] = [str(outputs)]
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

    if (root / ".mcp.json").exists():
        shutil.copy2(root / ".mcp.json", dest / ".mcp.json")

    launcher = dest / "Run Automation.command"
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    reg = load_registry(root)
    reg["projects"] = [p for p in reg.get("projects", []) if p["slug"] != slug] + [{
        "slug": slug, "name": a.name, "purpose": a.purpose, "path": str(dest),
        "outputs": str(outputs), "created": now.isoformat(timespec="seconds")}]
    save_registry(root, reg)
    print(f"Created {dest}")
    print(f"  project skill: {skill_dst}")
    print(f"  outputs copy:  {outputs}")

    if a.setup:
        py_cmd, _ = find_python()
        subprocess.run(py_cmd.split() + ["-m", "venv", str(dest / ".venv")], check=True)
        py = dest / ".venv" / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")
        subprocess.run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
        subprocess.run([str(py), "-m", "pip", "install", "-q", "-r", str(dest / "requirements.txt")], check=True)
        subprocess.run([str(py), "-m", "playwright", "install", "chromium"], check=True)
        # Same marker the launcher writes, so its first double-click skips setup.
        digest = hashlib.sha1((dest / "requirements.txt").read_bytes()).hexdigest()
        (dest / ".venv" / ".ready").write_text(digest + "\n", encoding="utf-8")
        print(f"  environment:   {dest / '.venv'} (ready)")


if __name__ == "__main__":
    main()
