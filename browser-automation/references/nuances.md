# Nuances: what real sites do to scrapers

Read this when building or fixing a module. Each item: the symptom, then the fix.

## Page loading and timing
- **Results arrive after the page "loads"** (rendered by JavaScript). Symptom: 0 rows
  but the screenshot shows results. Fix: `extract_items` already waits for the
  container selector, so make sure the selector is the *result card*, not a wrapper
  that exists before results arrive.
- **`networkidle` never happens** on sites with chat widgets/analytics polling.
  `wait_until_settled` falls back after 15 s, so if every step is slow, wait for a
  specific selector instead.
- **Search fires while typing** (debounced autocomplete). Pressing Enter may pick the
  first autocomplete suggestion instead. Fix: click the Search button, or press
  `Escape` before Enter, or use the URL form if one exists.
- **Stale results flash**: after clicking "next", the old cards are still there for a
  moment. If pages repeat, wait for something that changes (page number indicator,
  first card's text differing from before) before extracting.

- **Changing a filter/sort changes the endpoint.** If you build a URL or API call
  with different parameters than the traced ones (e.g. `sort=byDate` instead of the
  traced `sort=byPopularity`), re-check the network with those exact parameters.
  The endpoint name, request body or response shape can differ. Symptom:
  `capture_json` times out although the page shows results.

## Popups and overlays
- Cookie banners, newsletter modals, "allow notifications", chat bubbles covering
  buttons. `s.goto` calls `dismiss_popups()`; add the site's specific button to the
  module if it isn't caught (`s.click("#onetrust-reject-all-handler", settle=False)`).
- An overlay that intercepts clicks shows up as a timeout on `click`. The failure
  screenshot will show it.

## Structure
- **iframes**: results inside an iframe aren't visible to `page.locator`. Use
  `s.page.frame_locator("iframe#results").locator(...)`, or open the iframe's
  `src` URL directly.
- **New tabs**: links with `target=_blank` open a new page; use
  `context.expect_page()` or just read the `href` and `goto` it.
- **Shadow DOM** (web components): Playwright CSS pierces open shadow roots, but
  `querySelector` inside `extract_items` does not. Use locators per card for those fields.
- **Same class for different things** (`.meta` holds both city and date). Use
  position within the card only as a last resort; prefer a label next to it
  (`get_by_text("Location").locator("xpath=following-sibling::*[1]")`).

## Pagination
- "Next" exists but is disabled on the last page: `paginate` checks `disabled`,
  `aria-disabled` and a `disabled` class. Check the trace for the site's marker.
- Page numbers only, no "next": compute the next page link (`a:has-text("2")`) or use
  the URL `page=` param.
- Infinite scroll with a cap ("showing first 100"): you only get the cap. Narrow
  the search with filters instead of scrolling forever.
- Some sites return the **same results for every page** when you go past the end.
  Stop when a page's first row equals the previous page's first row.

## Data quality
- Relative links: `@href` returns absolute URLs; for text you build yourself use `s.absolute()`.
- Obfuscated emails (`name [at] domain [dot] com`), or emails/phones only revealed
  by clicking "Show": click per card or on the detail page; normalise text in Python.
- Whitespace/newlines inside fields are collapsed automatically.
- Dates like "3 days ago": store as-is plus the scrape date; don't put them in KEY_FIELDS.
- The same lead listed under several keywords is stored once; its `Keyword(s)`
  column accumulates every keyword it matched.

## Blocking and politeness
- Signs: HTTP 403/429, "unusual traffic", "verify you are human", empty pages only
  when headless. Fixes, in order: slow down (`pause_between_pages_seconds`), fewer
  pages, run headed (`"headless": false`), run less often. Never solve or bypass
  captchas automatically. If a site actively blocks automation, tell the user.
- One browser, one site at a time. Never parallelise against the same site.
- Respect the site's terms and robots.txt. Tell the user plainly if a site
  prohibits scraping.

## Sessions and login
- Saved sessions expire (hours to weeks). `is_logged_in` + a clear "run --login"
  message is the whole strategy; don't build auto re-login around captchas/OTPs.
- Logging in from a new device can trigger an email/OTP check. Do the `--login`
  on the machine that will run the scraper.

## Runtime environment
- Excel locks the file it has open (Windows). The exporter falls back to a
  timestamped filename. Tell the user to close yesterday's file anyway.
- macOS blocks double-clicking downloaded `.command` files the first time:
  right-click → Open → Open. Windows SmartScreen: "More info" → "Run anyway".
- A scheduled run on a sleeping laptop runs when it wakes (macOS); on Windows,
  tick "Run task as soon as possible after a scheduled start is missed" in Task
  Scheduler if needed.
- The project folder can be moved; the scheduler entry can't (re-run
  `schedule_daily.py` after moving).
