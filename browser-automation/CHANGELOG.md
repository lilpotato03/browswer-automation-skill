# Changelog

## 2.0.0 (2026-10-23)
- Renamed to **browser-automation** (was lead-scraper); launchers are now `Run Automation.command` / `.bat`.
- On-load setup: `scripts/bootstrap.py` checks prerequisites, creates the workspace
  (`~/BrowserAutomation` by default), optionally writes the workspace `.mcp.json`
  and prefetches Chromium; `--status` lists projects.
- Workspace rule: projects only in `<root>/projects/<slug>/`, created by `new_project.py`,
  registered in `workspace.json`.
- Every project gets its own project-confined skill (`.claude/skills/<slug>/SKILL.md`).
- Output rules: `<slug>_<date>.xlsx` per run (never overwritten), `latest.xlsx`,
  copies to `<root>/outputs/<slug>/`, dry runs stay local; `output.copy_to` for more.
- Self-maintenance section: where learnings go, versioning, syncing the master copy
  through GitHub / Google Drive / Notion connectors.
- Example projects removed from the package.

## 1.x (2026-10-23)
- Recorder: click-to-label panel (typing into the injected box failed on real keyboards),
  per-card selectors validated across all cards, overlay-link and icon-click handling,
  radio/checkbox events, tracker filtering in the summary.
- `scroll_to_load` handles "See more" buttons; `capture_json(match=...)` for search-as-you-type.
- Setup docs: prerequisites, Playwright MCP and Chrome DevTools MCP sources and tool mapping.
