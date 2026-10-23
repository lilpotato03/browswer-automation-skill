# Building a site module from a trace

A site module is a Python file in `scraper/sites/` that says *what* is on the
site. The shared `Session` (`s`) does the browser work. Typical size: 30–60 lines.

## 1. Pick the extraction strategy (first one that fits wins)

| Trace shows | Strategy | Helper |
|---|---|---|
| JSON call whose URL/body contains the keyword, holding the results | Read the API response | `s.capture_json(url_part, trigger)` |
| Keyword appears in the page URL (`?q=solar`) | Skip the form; build the URL | `s.goto(f"...?q={quote_plus(kw)}")` |
| Repeating cards/rows in HTML | Read cards | `s.extract_items(container, fields)` |
| A real `<table>` | Read the table | `s.extract_table(css)` |

API responses don't break when the site restyles, and they usually carry
extra fields (ids, dates, full addresses). Use them whenever the trace has one.

If the API can be called directly (plain GET, no special tokens), you can page
through it with `s.page.request.get(url).json()`. It shares the browser's cookies.
If it needs tokens the page computes, let the page make the call and catch it
with `capture_json`.

## 2. Map the trace to code

| Trace | Code |
|---|---|
| `fill` on the search box, then `enter` | `s.fill(css, keyword, submit=True)` |
| `fill`, then `click` on a Search button | `s.fill(css, keyword)` then `s.click(button_css)` |
| `select` | `s.select(css, option_text)` |
| click "next" between result pages | `for page_no in s.paginate(next_css): ...` |
| "Load more" button and/or results grow on scroll | `s.scroll_to_load(container_css, more_button=btn_css, max_items=N)` then extract once |
| URL has `page=2` | loop pages with `s.goto(url.format(page=n))` |
| note "company name" with container `div.card` | `FIELDS = {"company": "<css inside card>"}`, `RESULT_ITEM = "div.card"` |
| popup / new tab opened by a click | `with s.page.context.expect_page() as p: s.click(css)` then `p.value` |
| detail page needed for phone/email | see §5 |

### Field spec syntax (`extract_items`)
- `"h3.title"`: text of the first match inside the card
- `"a.title@href"`: an attribute (`href`/`src` come back as absolute URLs)
- `"@data-id"`: an attribute on the card itself
- `"."`: the whole card's text

Selectors must be **relative to the container**, so one card's field can
never be read from a neighbouring card.

## 3. Choose KEY_FIELDS carefully

`KEY_FIELDS` decides what counts as "the same lead" across runs and keywords.
- Best: the site's own id or the lead's detail URL (`["url"]`).
- Otherwise: a combination that doesn't change day to day (`["company", "city"]`).
- Never include things that change (posted "2 hours ago", view counts, prices).

## 4. Selector robustness checklist

Prefer, in order: `id` → `name` / `data-*` / `aria-label` → short class chains
inside the card → text (`s.page.get_by_text`) → never `nth-child` chains.
Avoid classes that look generated (`css-1x9ab3`, `sc-dkPtRN`). Check that the
container count equals the number of results the page shows.

## 5. Detail pages (phone, email, full address)

Opening every result is slow and the riskiest part for getting blocked. Only
do it for leads you don't already have:

```python
for row in s.extract_items(RESULT_ITEM, FIELDS):
    if not s.already_have(row, KEY_FIELDS):
        detail = s.page.context.new_page()
        detail.goto(row["url"], wait_until="domcontentloaded")
        row["phone"] = (detail.locator(".phone").first.inner_text()
                        if detail.locator(".phone").count() else "")
        detail.close()
        s.pause()
    yield row
```

Known leads keep the phone saved earlier, because blank values never overwrite
stored data.

## 6. Per-site memory

`s.state_set("last_seen_id", 18234)` / `s.state_get("last_seen_id", 0)` persist
in SQLite. Use it to stop paging early once you reach results you saw
yesterday (sites sorted newest-first):

```python
def scrape(s, keyword):
    seen_before = s.state_get(f"newest:{keyword}")
    first = True
    s.goto(f"{START_URL}?q={quote_plus(keyword)}&sort=newest")
    for page_no in s.paginate(NEXT):
        for row in s.extract_items(ITEM, FIELDS):
            if first:
                s.state_set(f"newest:{keyword}", row["url"])
                first = False
            if row["url"] == seen_before:
                return          # reached yesterday's results: stop paging
            yield row
```

To stop a keyword early from deeper inside helper code, raise
`StopScraping("reason")` (`from scraper.session import StopScraping`). The
runner logs it as a clean stop, not an error.

## 7. Login sites

```python
NEEDS_LOGIN = True
LOGIN_URL = "https://site.example/login"

def is_logged_in(s):
    s.goto("https://site.example/dashboard")
    return s.exists("a[href*='logout']")
```

The user runs `run.py --login <site>` once: a visible browser opens, they log in
(captcha/OTP included), press Enter in the terminal, and the session is saved
to `state/<site>.json`. Daily runs reuse it and refresh it. When it expires,
`is_logged_in` fails and the run reports "Saved login expired" in the Problems
sheet; they run `--login` again. Don't automate the password typing unless
the user explicitly wants it. If they do, read it from `secrets.env` (git-ignored),
never from the module.

## 8. Output format

Where files go and how they are named is fixed by SKILL.md section 3 (per-run
file, `latest.xlsx`, copy to `<root>/outputs/<slug>/`). Per project, customise
in `settings.json → output`:
- `columns`: preferred column order (unknown columns are appended after)
- `only_listed_columns`: true = drop everything not listed
- `format`: `xlsx` | `csv` | `json`
- `copy_to`: extra folders that get a copy (e.g. a synced Drive/OneDrive folder)
- `file_name`: `{project}_{date}` (keep the pattern; it keeps runs sortable)

Multiple sites in one project should use the **same field names** for the same
meaning (`company`, `contact`, `phone`, `email`, `location`, `url`, `posted`), so
their rows line up in one sheet. A special layout (a CRM import CSV) is a small
function in `scraper/export.py`. Don't build a plugin system.

## 9. Test loop

```bash
.venv/bin/python run.py --site <site> --keyword "<kw>" --dry-run --headed
```

Check in the spreadsheet:
1. Row count equals what the site shows for that keyword (across the pages you allowed).
2. No empty columns (an empty column means a selector that matches nothing).
3. URLs are absolute and open the right lead.
4. Page 2+ rows differ from page 1 (pagination really advanced).
5. A nonsense keyword gives 0 rows and **no error**.

Then: real run, and immediately a second real run. The second must report
`new: 0` for that site. If not, the KEY_FIELDS include something that changes.
