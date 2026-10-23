"""Reusable browser helpers handed to every site module as `s`.

Site modules should only describe *what* is on the page (selectors, field names).
The *how* (waiting, retrying, pagination, scrolling, catching API responses,
polite pauses) lives here once, so every new site reuses the same tested code.
"""
from __future__ import annotations

import logging
import random
import re
import time
from typing import Any, Callable, Dict, Iterator, List, Optional
from urllib.parse import unquote_plus, urljoin

log = logging.getLogger("scraper")

# Buttons that close cookie banners / newsletter popups. Privacy-friendly
# choices are tried first.
POPUP_BUTTON_TEXTS = [
    "Reject all", "Reject All", "Decline", "Only necessary", "Necessary only",
    "Use necessary cookies only", "No thanks", "No, thanks", "Not now",
    "Close", "Got it", "Dismiss", "×", "✕",
]

# JS that turns a list of repeating elements into rows in one round-trip.
# Field spec:   "css"        -> innerText of first match inside the item
#               "css@attr"   -> attribute of first match inside the item
#               "@attr"      -> attribute of the item itself
#               "" or "."    -> innerText of the item itself
_EXTRACT_JS = r"""
(items, fields) => items.map(item => {
  const row = {};
  for (const [name, spec] of Object.entries(fields)) {
    let css = spec, attr = null;
    const at = spec.lastIndexOf('@');
    if (at !== -1) { css = spec.slice(0, at); attr = spec.slice(at + 1); }
    css = css.trim();
    const el = (css === '' || css === '.') ? item : item.querySelector(css);
    if (!el) { row[name] = ''; continue; }
    let v;
    if (attr === 'href' || attr === 'src') v = el[attr] || el.getAttribute(attr) || '';
    else if (attr) v = el.getAttribute(attr) || '';
    else v = (el.innerText || el.textContent || '');
    row[name] = String(v).replace(/\s+/g, ' ').trim();
  }
  return row;
})
"""


class StopScraping(Exception):
    """Raise from a site module to stop the current keyword cleanly (not an error)."""


class Session:
    def __init__(self, page, site_name: str, settings: dict, db, run_id: int):
        self.page = page
        self.site = site_name
        self.settings = settings
        self.db = db
        self.run_id = run_id
        self.max_pages = int(settings.get("max_pages_per_keyword", 5))
        self._pause_range = settings.get("pause_between_pages_seconds", [1.5, 3.5])

    # ── basics ──────────────────────────────────────────────────────────────
    def log(self, msg: str) -> None:
        log.info("[%s] %s", self.site, msg)

    def pause(self, seconds: Optional[float] = None) -> None:
        """Polite random pause. Always pause between pages: it keeps you unblocked."""
        lo, hi = self._pause_range
        self.page.wait_for_timeout(int(1000 * (seconds if seconds is not None else random.uniform(lo, hi))))

    def goto(self, url: str, wait_for: Optional[str] = None, retries: int = 2) -> None:
        """Open a URL; retry on timeouts/network hiccups; optionally wait for a selector."""
        last = None
        for attempt in range(retries + 1):
            try:
                self.page.goto(url, wait_until="domcontentloaded")
                self.dismiss_popups()
                if wait_for:
                    self.page.wait_for_selector(wait_for, state="visible")
                return
            except Exception as exc:  # noqa: BLE001 - retry anything transient
                last = exc
                self.log(f"goto failed (attempt {attempt + 1}): {exc}")
                time.sleep(2 * (attempt + 1))
        raise last  # type: ignore[misc]

    def absolute(self, url: str) -> str:
        return urljoin(self.page.url, url) if url else ""

    def exists(self, css: str) -> bool:
        return self.page.locator(css).count() > 0

    def text(self, css: str, default: str = "") -> str:
        loc = self.page.locator(css)
        return re.sub(r"\s+", " ", loc.first.inner_text()).strip() if loc.count() else default

    # ── interacting ─────────────────────────────────────────────────────────
    def dismiss_popups(self, timeout_ms: int = 1500) -> None:
        """Best effort: close cookie banners and overlays if one is showing."""
        for label in POPUP_BUTTON_TEXTS:
            btn = self.page.get_by_role("button", name=label, exact=True)
            try:
                if btn.count() and btn.first.is_visible():
                    btn.first.click(timeout=timeout_ms)
                    self.page.wait_for_timeout(300)
                    return
            except Exception:  # noqa: BLE001 - popups are optional
                pass

    def fill(self, css: str, value: str, submit: bool = False) -> None:
        box = self.page.locator(css).first
        box.wait_for(state="visible")
        box.fill("")
        box.fill(value)
        if submit:
            box.press("Enter")
            self.wait_until_settled()

    def click(self, css: str, settle: bool = True) -> None:
        self.page.locator(css).first.click()
        if settle:
            self.wait_until_settled()

    def select(self, css: str, label: str) -> None:
        self.page.locator(css).first.select_option(label=label)

    def wait_until_settled(self, timeout_ms: int = 15000) -> None:
        """Wait for the page to finish loading after a click/submit."""
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:  # noqa: BLE001 - busy sites never go fully idle
            self.page.wait_for_timeout(1500)

    # ── reading lists of results ────────────────────────────────────────────
    def extract_items(self, item_css: str, fields: Dict[str, str],
                      wait: bool = True, timeout_ms: int = 15000) -> List[dict]:
        """Read every repeating result (card / row) into a dict of named fields."""
        if wait:
            try:
                self.page.wait_for_selector(item_css, timeout=timeout_ms)
            except Exception:  # noqa: BLE001 - zero results is a valid outcome
                return []
        rows = self.page.eval_on_selector_all(item_css, _EXTRACT_JS, fields)
        return [r for r in rows if any(v for v in r.values())]

    def extract_table(self, table_css: str) -> List[dict]:
        """Read an HTML <table> using its header row as field names."""
        return self.page.evaluate(
            """(css) => {
                const t = document.querySelector(css); if (!t) return [];
                let heads = [...t.querySelectorAll('thead th')].map(th => th.innerText.trim());
                let rows = [...t.querySelectorAll('tbody tr')];
                if (!heads.length && rows.length) {
                    heads = [...rows[0].children].map(c => c.innerText.trim()); rows = rows.slice(1);
                }
                return rows.map(r => Object.fromEntries([...r.children].map((c, i) =>
                    [heads[i] || ('col_' + (i + 1)), c.innerText.replace(/\\s+/g, ' ').trim()])));
            }""",
            table_css,
        )

    def paginate(self, next_css: str, max_pages: Optional[int] = None) -> Iterator[int]:
        """Yield 1, 2, 3 ... clicking the 'next' control between pages.

        Usage:  for page_no in s.paginate("li.next a"):  rows = s.extract_items(...)
        Stops when the next control is missing/disabled or max_pages is reached.
        """
        limit = max_pages or self.max_pages
        page_no = 1
        while True:
            yield page_no
            if page_no >= limit:
                return
            nxt = self.page.locator(next_css)
            if not nxt.count() or not nxt.first.is_visible():
                return
            disabled = nxt.first.get_attribute("disabled") is not None or \
                "disabled" in (nxt.first.get_attribute("class") or "") or \
                nxt.first.get_attribute("aria-disabled") == "true"
            if disabled:
                return
            self.pause()
            nxt.first.click()
            self.wait_until_settled()
            page_no += 1

    def scroll_to_load(self, item_css: str, max_rounds: int = 10,
                       more_button: Optional[str] = None, max_items: Optional[int] = None) -> int:
        """For infinite-scroll pages: scroll until no new items appear.

        more_button: a "Load more / See more" button some sites show after a few
                     scrolls; it is clicked whenever it is visible.
        max_items:   stop once this many items are on the page.
        """
        count = self.page.locator(item_css).count()
        stalls = 0
        for _ in range(max_rounds):
            if max_items and count >= max_items:
                break
            self.page.mouse.wheel(0, 20000)
            self.page.wait_for_timeout(1500)
            if more_button:
                btn = self.page.locator(more_button)
                if btn.count() and btn.first.is_visible():
                    btn.first.click()
                    self.page.wait_for_timeout(1500)
            new = self.page.locator(item_css).count()
            stalls = stalls + 1 if new == count else 0
            if stalls >= 2:  # one slow load is normal; two in a row = the end
                break
            count = new
        return count

    # ── catching the site's own data API ────────────────────────────────────
    def capture_json(self, url_contains: str, trigger: Callable[[], Any],
                     match: Optional[str] = None, timeout_ms: int = 20000) -> Any:
        """Run `trigger` (e.g. submit the search) and return the JSON the page fetched.

        Many sites load results from an API. Reading that JSON is far more robust
        than reading the HTML. Find `url_contains` in the trace's API calls.
        `match`: only accept a call whose URL or request body contains this text
        (search-as-you-type sites fire one call per keystroke; pass the keyword).
        """
        def wanted(resp) -> bool:
            if url_contains not in resp.url or resp.status >= 400:
                return False
            if not match:
                return True
            haystack = unquote_plus(resp.url + " " + (resp.request.post_data or ""))
            return match.lower() in haystack.lower()

        with self.page.expect_response(wanted, timeout=timeout_ms) as resp_info:
            trigger()
        return resp_info.value.json()

    # ── per-site memory (persists in SQLite between runs) ───────────────────
    def already_have(self, row: dict, key_fields: List[str]) -> bool:
        """True if this lead was saved on an earlier run.

        Use it to skip slow work (opening a detail page for phone/email) for
        leads you already have.
        """
        from .db import make_lead_key
        key = make_lead_key(self.site, row, key_fields)
        hit = self.db.conn.execute(
            "SELECT 1 FROM leads WHERE lead_key = ? AND first_run_id < ?", (key, self.run_id)
        ).fetchone()
        return hit is not None

    def state_get(self, key: str, default: Any = None) -> Any:
        return self.db.state_get(self.site, key, default)

    def state_set(self, key: str, value: Any) -> None:
        self.db.state_set(self.site, key, value)
