---
name: browser-automation
description: "Browser automation skill: sets itself up (checks prerequisites, creates an isolated workspace and per-project Python environments), traces websites (Claude drives a browser through the Playwright or Chrome DevTools MCP, or a person clicks through a recording browser and labels things), and turns each trace into a small self-contained automation that searches sites for keywords, remembers results in SQLite so only new items are flagged, and writes Excel/CSV with a double-click launcher for non-technical users. Every project lives in one workspace folder with its own project skill. Use when someone wants to: automate a website, scrape sites for leads or listings, search sites for keywords daily, trace or record a website flow, set up the browser automation workspace or MCP, schedule a run, deliver results, fix an automation that broke, or update this skill."
---

# Browser Automation

Version 2.0.0 (2026-10-23). Changes are recorded in `CHANGELOG.md`.

Turns "every day I search these sites for these keywords and copy the results"
into a double-click automation that produces a spreadsheet. Typical use: lead
generation, monitoring listings or tenders, collecting public directory data.

Two parts per site:
1. **Trace**: learn exactly how the site works. Claude drives a real browser
   through a browser MCP, or the user clicks through a recording browser, or a
   mix. The trace (`traces/<site>/trace.json`) is the evidence every selector
   and decision is based on.
2. **Automate**: a short site module in a self-contained project (own `.venv`,
   SQLite history, keywords, outputs, and its own project skill).

The person using the result is usually **not technical**. They touch
`keywords.txt`, a double-click launcher, and the spreadsheet. Nothing else.

| Path in this skill | What it is |
|---|---|
| `SKILL.md` | This file: setup, workspace, outputs, workflow, self-maintenance |
| `CHANGELOG.md` | What changed in this skill and why |
| `scripts/bootstrap.py` | Checks prerequisites; creates the workspace; MCP config; status |
| `scripts/new_project.py` | Creates a project inside the workspace (+ its project skill) |
| `scripts/project_skill.md.tmpl` | Template for each project's own skill |
| `template/` | The project scaffold: runner, helpers, recorder, launchers |
| `references/tracing.md` | How to trace in each mode; MCP tool mapping; trace format |
| `references/building.md` | Turning a trace into a site module; test loop |
| `references/nuances.md` | Problems real sites cause, and the fixes |

---

## 0. On load: setup check (always first)

`<skill_dir>` below means the folder this SKILL.md is in (usually
`~/.claude/skills/browser-automation`).

1. Run the check. It's read-only and safe:
   ```bash
   python3 "<skill_dir>/scripts/bootstrap.py"        # Windows: py -3 ...
   ```
2. If it ends with **READY** and the workspace exists, say so in one line and
   go on to the user's request.
3. Otherwise show the user the checklist it printed (what is OK, missing,
   optional) in plain words, then fix what you can:

   | Missing | What Claude does |
   |---|---|
   | Workspace | Run it directly: `bootstrap.py --install --prefetch` (default root `~/BrowserAutomation`; pass `--root <path>` only if the user named a location). `--prefetch` builds a tooling env and downloads Chromium once, so the first project is quick |
   | Browser MCP (user wants Claude to drive) | If Node is OK: `bootstrap.py --install --mcp playwright`. This writes `<root>/.mcp.json`; tell the user to **start a new session in the workspace folder** and approve the server |
   | Node.js (optional) | **Ask first.** macOS with Homebrew: `brew install node`; Windows: `winget install OpenJS.NodeJS.LTS`; otherwise <https://nodejs.org>. Without Node, recorder mode still works |
   | Python 3.9+ | **Ask first.** macOS: `brew install python@3.12` or python.org; Windows: python.org installer with "Add python.exe to PATH" ticked |
   | Internet / disk | Tell the user; nothing to install |

   Never use `sudo`, never change system Python, never install globally with pip.
   Python packages only go into `.venv` folders inside the workspace.
4. Re-run `bootstrap.py`; confirm **READY**.

### Prerequisites

| Need | Version | Needed for | Check |
|---|---|---|---|
| Python | 3.9+ | Everything | `python3 --version` / `py -3 --version` |
| Internet, ~1 GB free disk | n/a | First setup: packages + Chromium | n/a |
| Node.js | 18+ (Playwright MCP), 20.19+ / 22.12+ (Chrome DevTools MCP) | Only Claude-driven tracing | `node --version` |
| A browser MCP | see below | Only Claude-driven tracing | `browser_navigate` or `navigate_page` tool exists in the session |

Installed per project, in its own `.venv` (never globally): `playwright`, `openpyxl`,
and the Chromium browser (one shared download cache).
The end user of a finished automation needs only Python: no Node, no MCP, no Claude.

### MCP sources (for Claude-driven tracing)

Recorder mode needs no MCP. For Claude to drive the browser, use one of these.
`references/tracing.md` maps the tools of both.

| | Playwright MCP (recommended) | Chrome DevTools MCP (alternative) |
|---|---|---|
| Maintainer | Microsoft | Google Chrome team |
| Source | <https://github.com/microsoft/playwright-mcp> | <https://github.com/ChromeDevTools/chrome-devtools-mcp> |
| npm package | `@playwright/mcp` | `chrome-devtools-mcp` |
| Runs as | `npx @playwright/mcp@latest` | `npx chrome-devtools-mcp@latest` |
| Node | 18+ | 20.19+ / 22.12+, plus Chrome installed |
| Key tools | `browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_fill_form`, `browser_select_option`, `browser_press_key`, `browser_wait_for`, `browser_evaluate`, `browser_network_requests`, `browser_network_request`, `browser_take_screenshot`, `browser_tabs`, `browser_close` | `new_page`, `navigate_page`, `take_snapshot`, `click`, `fill`, `fill_form`, `press_key`, `wait_for`, `evaluate_script`, `list_network_requests`, `get_network_request`, `take_screenshot`, `list_pages`, `close_page` |

How to add one (pick one):
1. **Workspace file (recommended; desktop app and CLI)**: `bootstrap.py --install --mcp playwright`
   writes `<root>/.mcp.json`, and every new project gets a copy. Open Claude in the
   workspace or a project folder, start a new session, approve the server.
   Manual equivalent:
   ```json
   { "mcpServers": { "playwright": { "command": "npx",
       "args": ["@playwright/mcp@latest", "--output-dir", "./mcp-output", "--save-session"] } } }
   ```
2. **Claude Code CLI, all folders**:
   `claude mcp add --scope user playwright -- npx @playwright/mcp@latest --output-dir ./mcp-output --save-session`
   (check: `claude mcp list` or `/mcp`).
3. **Claude Desktop chat app**: the same `"playwright"` block under `"mcpServers"` in
   `~/Library/Application Support/Claude/claude_desktop_config.json`
   (Windows `%APPDATA%\Claude\claude_desktop_config.json`), then restart.

Useful Playwright MCP flags: `--headless` (no window; default is headed, better for
captcha/OTP hand-over), `--isolated` (in-memory profile), `--storage-state <file>`
(start logged in, e.g. a project's `state/<site>.json`), `--secrets <dotenv file>`
(type secrets without them passing through the conversation), `--output-dir`,
`--save-session`, `--caps vision,pdf,devtools`.

---

## 1. Workspace: where everything lives

```
~/BrowserAutomation/                 <- workspace root (bootstrap.py --install)
├── workspace.json                   registry of projects (name, slug, purpose, paths)
├── README.md                        explains the folder to humans
├── .mcp.json                        browser MCP config (optional; copied into projects)
├── .tools/venv/                     shared tooling env (from --prefetch)
├── projects/
│   └── <project-slug>/              ONE folder per use case (see below)
└── outputs/
    └── <project-slug>/              copy of every spreadsheet that project produced
```

The root is found in this order: `$BROWSER_AUTOMATION_HOME` →
`~/.browser-automation.json` (written by `--install`) → `~/BrowserAutomation`.

**Rules**
- **Projects live only in `<root>/projects/<slug>/`.** Never create a project on the
  Desktop, in Downloads, in a code repo or the current folder. If the user wants
  everything somewhere else, move the *workspace root* (`bootstrap.py --install --root <path>`);
  the layout inside stays identical.
- **Create projects only with `new_project.py`.** It copies the template, registers
  the project, creates its outputs folder, its project skill and (with `--setup`) its `.venv`:
  ```bash
  python3 "<skill_dir>/scripts/new_project.py" "Solar Installer Leads" --purpose "Daily list of new solar installers in Pune for the sales team" --setup
  ```
- **One project per use case** (one audience, one spreadsheet). Several sites that
  feed the same spreadsheet are several site modules in one project.
- **Slugs are permanent.** The registry, the scheduler entry, the outputs folder
  and the project skill all use the path. To rename, create a new project, move
  `scraper/sites/*`, `traces/`, `data/leads.db` and `keywords.txt` across, re-run
  `schedule_daily.py` there, and remove the old folder only after the user confirms.
- **Projects made before the workspace existed** (any folder with `run.py` +
  `scraper/sites/`): offer to move them in the same way.
- `bootstrap.py --status` lists every project, its sites and its last run.

**Inside a project**

| Path | What it is | Who edits it |
|---|---|---|
| `.claude/skills/<slug>/SKILL.md` | This project's own skill (section 2) | Claude, after every change |
| `keywords.txt` | Search terms, one per line | the user |
| `settings.json` | Pages, pauses, output, site-specific blocks | Claude (user may tweak) |
| `scraper/sites/<site>.py` | One module per website | Claude |
| `scraper/session.py`, `runner.py`, `db.py`, `export.py` | Shared engine (from the template) | only when upgrading the template |
| `traces/<site>/` | `trace.json`, page snapshots | recorder / Claude |
| `data/leads.db` | History: what is new, runs, errors, per-site memory | the program; **never delete** |
| `state/<site>.json` | Saved logins | the program; never share |
| `output/` | Spreadsheets (section 3) | the program |
| `logs/` | Run logs, failure screenshots + HTML | the program |
| `.venv/` | This project's isolated Python environment | launcher / `--setup` |
| `Run Automation.command` / `.bat`, `README.txt` | What the user double-clicks / reads | n/a |
| `tools/` | `record_trace.py`, `summarize_trace.py`, `schedule_daily.py` | n/a |

## 2. Project skills: each project has its own

Every project has `.claude/skills/<slug>/SKILL.md` (generated from
`scripts/project_skill.md.tmpl`). It holds **only facts about that project**:
purpose, the sites table (module, URL, extraction pattern, trace, quirks), inputs,
outputs, how to run it, schedule, and a change log. The general method stays here.

- **Claude opened in the project folder**: it loads automatically.
- **Claude opened anywhere else** (workspace root, another folder): read
  `projects/<slug>/.claude/skills/<slug>/SKILL.md` before touching that project.
- **Update it every time** a site is added/fixed, keywords/filters/output change, or
  a schedule is set: edit the relevant section and add a change-log row
  (date and what changed). An out-of-date project skill is a bug.
- Never put secrets, cookies or personal data in it.

## 3. Outputs: where and how

| What | Where | Notes |
|---|---|---|
| Each run's spreadsheet | `<project>/output/<slug>_<YYYY-MM-DD>.xlsx` | Never overwritten: a second run that day gets `_<HHMMSS>` |
| The newest run | `<project>/output/latest.xlsx` | What the user opens; replaced each run |
| A copy of every run | `<root>/outputs/<slug>/` | One place to find all results |
| Test runs | `<project>/output/*_DRY-RUN.xlsx` | Never copied, never `latest` |
| Logs and failure evidence | `<project>/logs/` | Never in `output/` |

- **Format**: Excel by default with sheets *New this run*, *All leads*, *Run history*,
  and *Problems* (only when something failed). `settings.json → output.format` can be
  `csv` (`_new.csv` + `_all.csv`, UTF-8 with BOM so Excel opens accents) or `json`.
- **Columns**: use the same field names across sites (`title`, `company`, `contact`,
  `phone`, `email`, `location`, `posted`, `url`) so they line up; set the order in
  `output.columns`; `only_listed_columns: true` drops the rest.
- **Extra destinations**: add folders to `output.copy_to`. A Google Drive / OneDrive /
  Dropbox *synced folder* is the way to deliver automatically, because scheduled
  runs happen without Claude.
- **Delivering through connectors** (only when Claude is in the loop and the user
  asks): upload `latest.xlsx` with a Google Drive connector, write rows into a Google
  Sheet, add rows to a Notion database, or draft an email with the file. Confirm the
  destination with the user first; never send or share without their OK.
- Code never writes results anywhere else. Change destinations in `settings.json`.

---

## 4. Workflow for a new automation

### Step 1: Gather inputs (one message)

| Input | Example |
|---|---|
| Websites (search page URLs) | `https://directory.example.com/search` |
| Keywords | `solar installer`, `EPC contractor` |
| What one result is; which fields | company, contact, phone, city, link |
| Login? Captcha/OTP? | "needs login, OTP to my phone" |
| Output | Excel (default), CSV, column order, extra destination |
| How often | daily at 9am (default: by hand) |
| Project name + purpose | "Solar Installer Leads: new installers in Pune for sales" |

Then create the project with `new_project.py ... --setup` (section 1), and fill
in the project skill's *Purpose*.

### Step 2: Trace each site

| Mode | Use when |
|---|---|
| **A. Claude drives** (browser MCP) | Public search pages, simple logins. Fastest |
| **B. Person drives** (recorder) | The person knows the site, or it has captcha/OTP, odd widgets, many steps. No MCP needed |
| **C. Guided** | Claude drives and hands over for login/captcha, or writes the person a click-by-click checklist for the recorder |

Details in `references/tracing.md`. The trace must capture: how a search starts,
the **repeating result container** and each field in it, how to get more results,
whether a **JSON API** carries the data, what "no results" looks like, popups/login
walls/rate limits, and whether fields need a click or detail page.
Recorder: `.venv/bin/python tools/record_trace.py --name <site> --url "<url>"`
(the person labels things with **Ctrl+Shift+N** and a click). Summarize with
`.venv/bin/python tools/summarize_trace.py traces/<site>/trace.json`.

### Step 3: Build the site module

Copy `scraper/sites/_template.py` to `scraper/sites/<site>.py` and fill it in from
the trace (`references/building.md`). Use the shared helpers on `s` (`goto`,
`fill`, `extract_items`, `paginate`, `scroll_to_load`, `capture_json`,
`already_have`, `state_get/set`). Add the site's row to the project skill.

### Step 4: Test until the output is right

```bash
.venv/bin/python run.py --site <site> --keyword "<kw>" --dry-run --headed
```
Check: row count equals what the site shows; no empty columns; absolute links;
pagination really advanced; a nonsense keyword gives 0 rows and no error. Then a
real run, then a second real run that must report `new: 0`.

### Step 5: Hand it over

- Keywords in `keywords.txt`; output options in `settings.json`.
- Logins: `.venv/bin/python run.py --login <site>` with the user.
- Schedule if wanted: `.venv/bin/python tools/schedule_daily.py --time 09:00`
  (`--remove` undoes it). Record it in the project skill's *Schedule*.
- Tell the user: double-click `Run Automation.command` / `.bat`; results in
  `output/latest.xlsx` and `<root>/outputs/<slug>/`.
- Sharing a project: zip it **without** `.venv/`, `data/`, `state/`, `logs/`, `output/`.

### Step 6: When a site breaks later

1. Read the *Problems* sheet and the screenshot + HTML in `logs/failures/<date>/`.
2. Compare with `traces/<site>/trace.json` and the project skill's notes.
3. Re-trace just the broken part if unclear (`traces/<site>/trace_<date>.json`).
4. Patch, `--dry-run`, real run. Update the project skill (quirks + change log).

---

## 5. Maintaining this skill (Claude may update it)

This skill improves as it meets real sites. Claude edits it, carefully:

| Learned something... | Goes to |
|---|---|
| About one site or one project | That project's skill, never here |
| About websites in general (a new trap and its fix) | `references/nuances.md` |
| A new reusable helper in the template | `template/scraper/session.py` + the mapping table in `references/building.md` |
| A tracing technique / MCP change | `references/tracing.md` |
| Setup, workspace, output or workflow rules | this `SKILL.md` |

**How to change it**
1. Edit the files in place (this folder; usually `~/.claude/skills/browser-automation`).
   Keep the structure and headings; change only what is needed.
2. Add an entry to `CHANGELOG.md` (date, what changed, why) and bump the
   `Version` line at the top of this file (patch: wording/fix; minor: new
   capability; major: changed layout or rules).
3. Template changes don't reach existing projects by themselves. To upgrade a
   project: diff the changed template files against the project's copies, copy
   them over, run a `--dry-run`, and note the upgrade in that project's skill.
4. Tell the user, in a line or two, what changed in the skill.

**Syncing the master copy through connectors.** If the user keeps the
authoritative copy somewhere shared, record it in `<root>/workspace.json` as
`"skill_source": {"type": "github" | "google_drive" | "notion", "location": "<repo/path, file id or page url>"}`.
- **Before editing**: fetch the latest from that source with the matching
  connector or tool (GitHub: `gh`/`git pull`; Google Drive: search/read the file;
  Notion: read the page) and compare it with the installed copy; if they differ,
  show the user and ask which wins.
- **After editing**: publish back only after the user confirms (GitHub: commit +
  push or a PR; Google Drive: update the file or upload a new zip; Notion: update
  the page), then confirm where it went.
- **Packaging**: zip this folder as `browser-automation-skill.zip` (skip
  `__pycache__`). Never include projects, `.venv`, data or saved logins.

---

## Rules

- **Workspace only.** Projects in `<root>/projects/<slug>/`, created by `new_project.py`.
- **Project skill current.** Update it with every change to the project.
- **Outputs per section 3.** Never invent other output locations in code.
- **No guessed selectors.** Every selector, URL and API pattern comes from the
  trace, a snapshot, or something observed live.
- **Prefer data over pixels.** If a JSON call carries the results, use `capture_json`.
- **Keep it small.** Site modules describe the site; shared behaviour lives once in
  `scraper/session.py`. No frameworks, servers, queues or new config languages.
- **State in SQLite** (`data/leads.db`), never in side files.
- **No passwords in code, traces or skills.** Use `run.py --login` (saved session).
- **Captchas and OTPs are for the human.** Hand over, save the session, reuse it.
- **Be polite and lawful.** Keep pauses and page limits modest, one browser at a
  time. Check the site's terms/robots.txt and tell the user if a site forbids
  automated access. Personal data in results is subject to privacy/marketing rules.
- **Isolated installs only.** Packages go into `.venv` folders; ask before
  installing system software; never `sudo`.
