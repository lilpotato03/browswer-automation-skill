"""The daily run: every site x every keyword -> SQLite -> spreadsheet."""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .db import DB
from .export import export
from .session import Session, StopScraping
from .sites import discover

log = logging.getLogger("scraper")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def load_settings(root: Path) -> dict:
    return json.loads((root / "settings.json").read_text(encoding="utf-8"))


def load_keywords(root: Path) -> List[str]:
    path = root / "keywords.txt"
    if not path.exists():
        return []
    lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines()]
    return [l for l in lines if l and not l.startswith("#")]


def setup_logging(root: Path) -> Path:
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    path = logs / f"run_{datetime.now():%Y-%m-%d}.log"
    fmt = logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S")
    file_h = logging.FileHandler(path, encoding="utf-8")
    file_h.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    log.handlers[:] = [file_h, console]
    log.setLevel(logging.INFO)
    return path


def _clean(row: dict) -> dict:
    """Spreadsheet-safe values: trimmed strings/numbers, no nested objects."""
    out = {}
    for k, v in row.items():
        if v is None:
            v = ""
        elif isinstance(v, (list, tuple)):
            v = ", ".join(str(x) for x in v)
        elif isinstance(v, dict):
            v = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, str):
            v = re.sub(r"\s+", " ", v).strip()
        out[str(k)] = v
    return out


def _save_failure(page, root: Path, site: str, keyword: str) -> str:
    folder = root / "logs" / "failures" / datetime.now().strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{site}_{keyword or 'all'}_{datetime.now():%H%M%S}")
    try:
        page.screenshot(path=str(folder / f"{stem}.png"), full_page=True)
        (folder / f"{stem}.html").write_text(page.content(), encoding="utf-8")
    except Exception:  # noqa: BLE001 - page may already be gone
        return ""
    return str((folder / f"{stem}.png").relative_to(root))


def _prune_failures(root: Path, keep_days: int) -> None:
    base = root / "logs" / "failures"
    if not base.exists():
        return
    cutoff = time.time() - keep_days * 86400
    for d in base.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)


def run(root: Path, site_filter: Optional[List[str]] = None,
        keyword_filter: Optional[List[str]] = None, headed: bool = False,
        dry_run: bool = False) -> dict:
    settings = load_settings(root)
    setup_logging(root)
    all_sites = discover()
    enabled = settings.get("enabled_sites") or list(all_sites)
    names = [n for n in enabled if n in all_sites and (not site_filter or n in site_filter)]
    unknown = [n for n in (site_filter or []) if n not in all_sites]
    if unknown:
        raise SystemExit(f"Unknown site(s): {', '.join(unknown)}. Known: {', '.join(all_sites)}")
    keywords = keyword_filter or load_keywords(root)
    if not names:
        raise SystemExit("No sites to run. Add a site module under scraper/sites/.")

    # A dry run writes to a throwaway DB so test runs never pollute "new leads".
    if dry_run:  # test output stays in the project: no latest.xlsx, no copies
        out = settings.setdefault("output", {})
        out["file_name"] = out.get("file_name", "{project}_{date}") + "_DRY-RUN"
        out["copy_to"], out["write_latest"] = [], False
    db = DB(root / "data" / ("dry_run.db" if dry_run else "leads.db"))
    run_id = db.start_run(names, keywords)
    log.info("Run #%s  sites=%s  keywords=%s%s", run_id, names, keywords,
             "  (dry run)" if dry_run else "")

    from playwright.sync_api import sync_playwright

    totals: Dict[str, dict] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not headed and settings.get("headless", True),
            args=["--disable-blink-features=AutomationControlled"],
        )
        for name in names:
            mod = all_sites[name]
            totals[name] = {"new": 0, "seen": 0, "errors": 0}
            state_file = root / "state" / f"{name}.json"
            if mod.NEEDS_LOGIN and not state_file.exists():
                msg = f"Not logged in. Run:  run.py --login {name}"
                log.info("[%s] %s", name, msg)
                db.log_error(run_id, name, "", msg)
                totals[name]["errors"] += 1
                continue

            context = browser.new_context(
                user_agent=USER_AGENT, viewport={"width": 1366, "height": 900},
                locale="en-US", accept_downloads=True,
                storage_state=str(state_file) if state_file.exists() else None,
            )
            context.set_default_timeout(int(settings.get("page_timeout_seconds", 30)) * 1000)
            page = context.new_page()
            s = Session(page, name, settings, db, run_id)

            if hasattr(mod, "is_logged_in") and not mod.is_logged_in(s):
                msg = f"Saved login expired. Run:  run.py --login {name}"
                log.info("[%s] %s", name, msg)
                db.log_error(run_id, name, "", msg, _save_failure(page, root, name, "login"))
                totals[name]["errors"] += 1
                context.close()
                continue

            for kw in (keywords if mod.USES_KEYWORDS else [""]):
                found = 0
                try:
                    for row in mod.scrape(s, kw):
                        row = _clean(row)
                        is_new = db.upsert_lead(run_id, name, kw, row, mod.KEY_FIELDS)
                        totals[name]["new" if is_new else "seen"] += 1
                        found += 1
                except StopScraping as stop:
                    log.info("[%s] '%s' stopped: %s", name, kw, stop)
                except Exception as exc:  # noqa: BLE001 - one bad keyword must not kill the run
                    shot = _save_failure(page, root, name, kw)
                    log.info("[%s] '%s' FAILED after %s rows: %s", name, kw, found, exc)
                    log.debug(traceback.format_exc())
                    db.log_error(run_id, name, kw, f"{type(exc).__name__}: {exc}", shot)
                    totals[name]["errors"] += 1
                log.info("[%s] '%s': %s rows", name, kw or "(no keyword)", found)
                s.pause()

            if mod.NEEDS_LOGIN:
                context.storage_state(path=str(state_file))  # keep the session fresh
            context.close()
        browser.close()

    any_ok = any(t["new"] + t["seen"] for t in totals.values())
    any_err = any(t["errors"] for t in totals.values())
    status = "ok" if not any_err else ("partial" if any_ok else "failed")
    db.finish_run(run_id, status)
    files = export(db, run_id, settings, all_sites, root)
    _prune_failures(root, int(settings.get("keep_failure_screenshots_days", 14)))

    log.info("-" * 60)
    for name, t in totals.items():
        log.info("%-24s new: %-5s already known: %-5s problems: %s",
                 name, t["new"], t["seen"], t["errors"])
    log.info("Status: %s   Spreadsheet: %s", status.upper(), ", ".join(str(f) for f in files))
    return {"run_id": run_id, "status": status, "totals": totals, "files": files}


def login(root: Path, site_name: str) -> None:
    """Open a visible browser, let the person log in by hand, save the session."""
    sites = discover()
    if site_name not in sites:
        raise SystemExit(f"Unknown site: {site_name}. Known: {', '.join(sites)}")
    mod = sites[site_name]
    state_file = root / "state" / f"{site_name}.json"
    state_file.parent.mkdir(exist_ok=True)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent=USER_AGENT, no_viewport=True,
            storage_state=str(state_file) if state_file.exists() else None)
        page = context.new_page()
        page.goto(mod.LOGIN_URL)
        print("\n  Log in inside the browser window (solve any captcha / OTP).")
        input("  When you can see you are logged in, come back here and press Enter... ")
        context.storage_state(path=str(state_file))
        browser.close()
    print(f"  Saved login for {site_name}. Daily runs will reuse it.")
