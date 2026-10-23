"""Every .py file in this folder (not starting with "_") is one website.

Site module contract
--------------------
NAME          short id, e.g. "acme_directory" (defaults to the file name)
START_URL     where a search starts
KEY_FIELDS    fields that make a lead unique, e.g. ["company", "url"]
COLUMNS       column order for the spreadsheet (optional)
USES_KEYWORDS True (default) = run once per keyword in keywords.txt;
              False = run once per day with keyword "" (e.g. a "latest" page)
NEEDS_LOGIN   True if the site needs a saved login (see `run.py --login NAME`)
LOGIN_URL     page to open for `--login` (defaults to START_URL)

def scrape(s, keyword):   generator that yields one dict per lead
def is_logged_in(s):      optional; return False to stop with a clear message
"""
from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType
from typing import Dict


def discover() -> Dict[str, ModuleType]:
    sites = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{__name__}.{info.name}")
        if not hasattr(mod, "scrape"):
            continue
        mod.NAME = getattr(mod, "NAME", info.name)
        mod.KEY_FIELDS = getattr(mod, "KEY_FIELDS", ["url"])
        mod.COLUMNS = getattr(mod, "COLUMNS", [])
        mod.USES_KEYWORDS = getattr(mod, "USES_KEYWORDS", True)
        mod.NEEDS_LOGIN = getattr(mod, "NEEDS_LOGIN", False)
        mod.LOGIN_URL = getattr(mod, "LOGIN_URL", getattr(mod, "START_URL", ""))
        sites[mod.NAME] = mod
    return sites
