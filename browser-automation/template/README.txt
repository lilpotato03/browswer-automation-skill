BROWSER AUTOMATION - HOW TO USE
===============================

It searches your websites for your keywords and puts the results in a
spreadsheet. Results it has found before are remembered, so the
"New this run" tab only shows what is new.

RUN IT
  Mac:      double-click  "Run Automation.command"
            (first time only: right-click it > Open > Open, because macOS
             is cautious about downloaded files)
  Windows:  double-click  "Run Automation.bat"

  The first run takes a few minutes to set itself up. After that a run
  takes as long as the searches do. The spreadsheet opens when it finishes.

YOUR RESULTS
  output/latest.xlsx               always the newest run (open this one)
  output/<project>_<date>.xlsx     one file per run, kept forever
  ...and a copy of every file in the workspace's outputs/<project>/ folder.

  Tabs in each spreadsheet:
    New this run  - found for the first time in this run
    All leads     - everything ever found, with first/last seen dates
    Run history   - one line per run
    Problems      - only appears if a site had trouble (screenshot path included)

CHANGE THE KEYWORDS
  Open keywords.txt, put one keyword per line, save. That's it.

OTHER SETTINGS (settings.json)
  headless                    true = run invisibly, false = show the browser
  max_pages_per_keyword       how many result pages to read per search
  output.format               "xlsx" (Excel), "csv" or "json"
  output.columns              column order, e.g. ["company", "phone", "url"]

IF A SITE STOPS WORKING
  Websites change. Open the Problems tab: it names the site and points to a
  screenshot in logs/failures/. Open Claude in this folder and say
  "site X stopped working, fix it". The traces/ folder and this project's
  own skill (.claude/skills/) tell it how the site worked when it was built.

DON'T DELETE
  data/leads.db   your history (this is how it knows what is new)
  state/          saved logins
