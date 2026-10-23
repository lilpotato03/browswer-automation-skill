"""Run this browser automation project.

  run.py                              all sites, all keywords (the daily run)
  run.py --site NAME --keyword "x"    one site / one keyword (testing)
  run.py --headed                     show the browser while it works
  run.py --dry-run                    test without touching the real lead history
  run.py --login NAME                 log in by hand once; the session is saved
  run.py --list                       show configured sites and keywords
  run.py --export-only                rebuild the spreadsheet from the last run
"""
from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from scraper import runner  # noqa: E402


def open_file(path: Path) -> None:
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", str(path)])
        elif platform.system() == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:  # noqa: BLE001 - opening the file is a convenience
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Browser automation runner")
    ap.add_argument("--site", action="append", help="only this site (repeatable)")
    ap.add_argument("--keyword", action="append", help="only this keyword (repeatable)")
    ap.add_argument("--headed", action="store_true", help="show the browser")
    ap.add_argument("--dry-run", action="store_true", help="use a throwaway database")
    ap.add_argument("--login", metavar="SITE", help="log in by hand and save the session")
    ap.add_argument("--list", action="store_true", help="list sites and keywords")
    ap.add_argument("--export-only", action="store_true", help="re-export the last run")
    ap.add_argument("--scheduled", action="store_true", help="unattended run (don't open files)")
    args = ap.parse_args()

    if args.login:
        runner.login(ROOT, args.login)
        return 0

    if args.list:
        from scraper.sites import discover
        for name, mod in discover().items():
            print(f"  {name:24s} {getattr(mod, 'START_URL', '')}"
                  f"{'  (needs login)' if mod.NEEDS_LOGIN else ''}")
        print("  keywords:", ", ".join(runner.load_keywords(ROOT)) or "(none)")
        return 0

    if args.export_only:
        from scraper.db import DB
        from scraper.export import export
        from scraper.sites import discover
        db = DB(ROOT / "data" / "leads.db")
        files = export(db, db.last_run_id() or 0, runner.load_settings(ROOT), discover(), ROOT)
        print("Wrote:", ", ".join(str(f) for f in files))
        return 0

    result = runner.run(ROOT, args.site, args.keyword, headed=args.headed, dry_run=args.dry_run)
    settings = runner.load_settings(ROOT)
    if not args.scheduled and settings.get("output", {}).get("open_when_done", True):
        for f in result["files"]:
            open_file(f)
    return 0 if result["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
