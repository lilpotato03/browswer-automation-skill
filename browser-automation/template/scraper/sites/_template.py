"""TEMPLATE: copy to <site_name>.py and fill in from the trace.

Pick ONE of the two patterns below and delete the other.
Every selector here should come from the trace (recorded css / annotations /
saved page snapshots), never guessed.
"""
from urllib.parse import quote_plus

NAME = "example_site"
START_URL = "https://www.example.com/search"
KEY_FIELDS = ["url"]                       # what makes a lead unique
COLUMNS = ["title", "company", "location", "url", "posted"]
USES_KEYWORDS = True
NEEDS_LOGIN = False

# Pattern A selectors (HTML results)
SEARCH_BOX = "input[name='q']"
RESULT_ITEM = "div.result-card"            # the repeating card / row
FIELDS = {
    "title": "h2",
    "company": ".company",
    "location": ".location",
    "url": "a@href",                       # css@attr reads an attribute
    "posted": "time@datetime",
}
NEXT_PAGE = "a[rel='next']"


def scrape(s, keyword):
    # ── Pattern A: type into the search box, read the cards, click "next" ──
    s.goto(START_URL)
    s.fill(SEARCH_BOX, keyword, submit=True)
    for page_no in s.paginate(NEXT_PAGE):
        rows = s.extract_items(RESULT_ITEM, FIELDS)
        s.log(f"'{keyword}' page {page_no}: {len(rows)} results")
        for row in rows:
            yield row

    # ── Pattern B: the page calls a JSON API; read that instead ─────────────
    # (use when the trace shows an API call whose URL/body contains the keyword)
    # s.goto(START_URL)
    # data = s.capture_json("/api/search", lambda: s.fill(SEARCH_BOX, keyword, submit=True))
    # for item in data["results"]:
    #     yield {"title": item["title"], "company": item["org"], "url": item["link"]}

    # Tip: if the search is just a URL (e.g. /search?q=...), skip the form:
    # s.goto(f"{START_URL}?q={quote_plus(keyword)}")
