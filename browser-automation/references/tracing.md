# Tracing a site

Goal: a `traces/<site>/trace.json` that holds enough evidence to write the
site module without guessing. All three modes produce the same file format
(see the end of this document).

Keep a running **SITE NOTES** block in the conversation while tracing:

```
SITE NOTES: <site_name>
=======================
Search page: https://...
Search starts by: form (input[name=q] + Enter) | button | URL ?q=<kw>&page=<n>
Result container: div.result-card   (N per page, matches what the page shows)
Fields inside the container:
  company  -> h3.title                 "Acme Solar Pvt Ltd"
  city     -> .meta .location          "Pune"
  url      -> a.title-link@href
More results: a[rel=next] | page numbers | "Load more" button | infinite scroll | URL page=
Data API: GET /api/search?q=... -> JSON .results[] (fields: name, city, slug)
No results looks like: text "No matches found" in .empty-state
Popups: cookie banner "Reject all"
Login: none | form at /login (captcha: yes/no, OTP: yes/no)
Detail page needed for: phone (click "Show number" on detail page)
Rate limits seen: none
```

---

## Mode A: Claude drives (browser MCP)

Works with the Playwright MCP or the Chrome DevTools MCP (setup and sources
are in SKILL.md → Setup). Steps below name the Playwright tool; use the
right-hand column if only Chrome DevTools MCP is connected.

| Job | Playwright MCP | Chrome DevTools MCP |
|---|---|---|
| Open a page | `browser_navigate` | `new_page` / `navigate_page` |
| Read the page (refs/uids) | `browser_snapshot` | `take_snapshot` |
| Click / type / choose | `browser_click`, `browser_type`, `browser_fill_form`, `browser_select_option` | `click`, `fill`, `fill_form` |
| Keys / wait | `browser_press_key`, `browser_wait_for` | `press_key`, `wait_for` |
| Run JS in the page | `browser_evaluate` (pass the element ref) | `evaluate_script` (pass the element uid in `args`) |
| List network calls | `browser_network_requests` | `list_network_requests` (filter `resourceTypes: ["document","xhr","fetch"]`) |
| One call's body | `browser_network_request` | `get_network_request` (by `reqid`) |
| Screenshot | `browser_take_screenshot` | `take_screenshot` |
| Tabs / close | `browser_tabs`, `browser_close` | `list_pages`, `select_page`, `close_page` |

Practical tips:
- On list pages the snapshot is huge (every card, every link). Take it once to find
  the controls, then probe the structure with JS (A4), which returns a few lines.
- Filter the network list to `document`, `xhr` and `fetch`; the rest is images,
  fonts and trackers.
- Sites with snapshot text that differs from the HTML (truncated titles, values only
  in attributes or class names) are common. Always confirm fields with a JS
  extraction over **all** cards, counting empty values per field.

### A1. Open the search page
1. `browser_navigate` to the search URL.
2. `browser_snapshot`: find the search box, filters, submit button, cookie banner.
   Dismiss the banner (prefer "Reject all" / "Necessary only") and note its button.
3. `browser_take_screenshot` for a visual reference.

### A2. Get stable selectors
Snapshot refs (`ref=e12`) only live for this session, so convert every element
you'll use into a CSS selector with `browser_evaluate` on that element:

```javascript
(el) => {
  const tag = el.tagName.toLowerCase();
  const uniq = s => document.querySelectorAll(s).length === 1;
  if (el.id && !/^\d/.test(el.id)) return '#' + el.id;
  for (const a of ['name','data-testid','aria-label','placeholder','title']) {
    const v = el.getAttribute(a); if (v && uniq(`${tag}[${a}="${v}"]`)) return `${tag}[${a}="${v}"]`;
  }
  const cls = [...el.classList].slice(0,3).map(c => '.' + CSS.escape(c)).join('');
  return cls && uniq(tag + cls) ? tag + cls : null;   // null => pick a better anchor
}
```

### A3. Run a real search
Use one of the user's real keywords.
1. Type it into the search box and submit the way a person would (Enter or the button).
2. `browser_wait_for` the results, then `browser_snapshot`.
3. Check the URL with `browser_evaluate` → `() => location.href`. If the keyword is
   in the URL (`?q=solar`), the search is just a URL, and the module can skip the form.
4. `browser_network_requests`: look for an XHR/fetch whose URL or body contains
   the keyword and returns JSON. That is the data API. Record method, URL
   pattern and the JSON path to the result list.

### A4. Find the repeating result container
Run this with `browser_evaluate` to list repeating structures that look like results:

```javascript
() => {
  const groups = {};
  for (const el of document.querySelectorAll('body *')) {
    if (!el.parentElement || el.children.length < 2) continue;
    const cls = [...el.classList].filter(c => !/\d{3,}|active|selected/.test(c)).slice(0,2);
    if (!cls.length) continue;
    const key = el.tagName.toLowerCase() + cls.map(c => '.' + CSS.escape(c)).join('');
    (groups[key] ||= []).push(el);
  }
  return Object.entries(groups)
    .filter(([k, els]) => els.length >= 3 && (els[0].innerText || '').trim().length > 20)
    .map(([k, els]) => ({ css: k, count: els.length,
      sample: (els[0].innerText || '').trim().replace(/\s+/g, ' ').slice(0, 160) }))
    .sort((a, b) => b.count - a.count).slice(0, 12);
}
```

Pick the container whose `count` matches the number of results the page shows
and whose `sample` reads like one lead. Then get each field **relative to the container**:

```javascript
() => {
  const card = document.querySelector('<container css>');
  return { html: card.outerHTML.slice(0, 3000) };
}
```

Read the HTML and choose field selectors scoped inside the card. Confirm with
an extraction over **all** cards, counting empties per field (a field that is
empty on some cards needs a better selector):

```javascript
() => {
  const rows = [...document.querySelectorAll('<container css>')].map(c => ({
    company: c.querySelector('h3')?.innerText.trim(),
    url: c.querySelector('a')?.href,
  }));
  const empty = Object.fromEntries(Object.keys(rows[0] || {}).map(k => [k, rows.filter(r => !r[k]).length]));
  return { total: rows.length, empty, sample: rows.slice(0, 3) };
}
```

### A5. More results, no results, details
- Click "next" / the page-2 link / "load more", or scroll. Note which works
  and how the URL changes. Check the last page (is "next" missing or disabled?).
- Search for nonsense (`zzqxv`) and record what "no results" looks like.
- If wanted fields (phone, email) are not on the card, open one result and
  trace how to reach them (detail URL pattern, "show number" click, a JSON call).

### A6. Login / captcha / OTP: hand over
If the site needs a login:
- Ask the user for credentials only if there is no captcha/OTP and they are
  happy to share them. Otherwise, or if anything blocks you, say what you see
  and ask the user to complete that step in the MCP browser window (it is
  headed by default). Then continue tracing.
- The scraper itself will reuse a saved session (`run.py --login <site>`),
  so the trace only needs to record *that* login is required, the login URL,
  and how to tell you're logged in (a selector or text that only shows when logged in).

### A7. Write the trace
Write `traces/<site_name>/trace.json` in the format below with
`"source": "mcp"`: the steps you performed as `events`, the key elements as
`notes` (with `outer_html` of one result card), and the relevant data API calls
from `browser_network_requests` as `network` entries. Then `browser_close`.

---

## Mode B: the person drives (recorder)

Run inside the project. The window stays open until the person closes it, so
start it in the background and wait:

```bash
cd "<project>" && .venv/bin/python tools/record_trace.py --name <site_name> --url "<search page url>"
```

(Add `--use-login` to start already logged in with a session saved by `run.py --login`.)

Tell the person, in plain words:

```
A browser window will open with a small REC badge in the corner.
1. Do your search exactly like you normally would.
2. On the FIRST result, point at each thing you want and press Ctrl+Shift+N.
   A panel opens: click the matching label (company, phone, location,
   link, next page ...) or type your own and click OK.
3. Press Ctrl+Shift+S once on the results page (saves a copy of the page).
4. Go to page 2 of the results.
5. Close the browser window when you're done.
(Ctrl+Shift+M just marks something as important without a name.)
```

The person presses Ctrl, not Cmd, on a Mac too.

Afterwards, run `tools/summarize_trace.py` on the trace. Named elements come
with their ancestor chain; the summary shows the **repeating container**
(the nearest ancestor with 3+ look-alike siblings), which is your result card.
Snapshots in `traces/<site>/snapshots/` let you test selectors offline:
open one with the MCP (`browser_navigate` to `file://...`) or read the HTML.

## Mode C: guided (mix)

- **Claude drives, human unblocks**: run Mode A; when you reach a captcha,
  OTP, consent screen or an unclear choice ("which of these are leads?"), stop,
  say exactly what to do in the browser window, wait, continue.
- **Human drives, Claude directs**: before launching the recorder, look at the site
  yourself (Mode A, A1–A3) and write the person a numbered checklist specific to
  that site ("type the keyword in the box labelled *Search suppliers*, choose
  *Maharashtra* in the State dropdown, press Search, name the company and phone of
  the first result..."). Then launch the recorder.

---

## Trace format (`browser-automation-trace/1`)

```json
{
  "format": "browser-automation-trace/1",
  "source": "recorder | mcp",
  "start_url": "https://...",
  "recorded_at": "2026-10-23T09:00:00+00:00",
  "events": [
    {"type": "navigate", "url": "...", "timestamp": 0},
    {"type": "fill", "css": "input[name=q]", "value": "solar", "input_type": "search", "url": "..."},
    {"type": "enter", "css": "input[name=q]"},
    {"type": "click", "css": "a[rel=next]", "text": "Next", "triggered_network": [14]},
    {"type": "select", "css": "#state", "value": "MH", "option_text": "Maharashtra"},
    {"type": "popup", "url": "..."}
  ],
  "notes": [
    {"kind": "named", "note": "company name", "css": "h3.title", "text": "Acme Solar",
     "url": "...", "outer_html": "<h3 ...>",
     "ancestors": [{"depth": 1, "css": "...", "shape_css": "div.result-card",
                    "siblings_same_shape": 20, "page_count_same_shape": 20}]}
  ],
  "snapshots": [{"file": "snapshots/page_01.html", "url": "...", "title": "..."}],
  "network": [
    {"id": 14, "method": "GET", "url": ".../api/search?q=solar&page=2",
     "resource_type": "fetch", "body": null,
     "response": {"status": 200, "content_type": "application/json", "body": "{...}"}}
  ],
  "downloads": [],
  "console": []
}
```

`triggered_network` lists the network ids fired within 2.5 s after an action,
which is how you tie "clicked Search" to "called /api/search".
