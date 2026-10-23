"""Condense a trace.json into a short, readable brief for building a site module.

  python tools/summarize_trace.py traces/acme_directory/trace.json

Prints: the step-by-step flow, everything the person marked/named (with the
likely repeating result container), page snapshots, and the API calls most
likely to carry the search results (keyword found in the URL/body).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import unquote_plus

STATIC_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", ".woff", ".woff2", ".ico")
# Trackers often echo the page URL (and so the keyword) in their payloads.
NOISE_HOSTS = ("google-analytics", "googletagmanager", "doubleclick", "facebook", "hotjar",
               "segment.", "sentry", "clarity.ms", "newrelic", "intercom", "linkedin.com/px",
               "demdex.net", "google.com/rmkt", "google.com/gmp", "google.com/ccm",
               "google.com/pagead", "googleadservices", "tealium", "/li/track", "bat.bing",
               "adsrvr", "scorecardresearch", "omtrdc", "litms/api", "ingraphs")
STATIC_CTYPES = ("text/css", "javascript", "image/", "font/")


def likely_container(ancestors: list) -> dict:
    """The nearest ancestor that repeats 3+ times on the page = one result card."""
    for a in ancestors or []:
        if a.get("depth", 0) > 0 and a.get("siblings_same_shape", 0) >= 3:
            return a
    return {}


def main(path: str) -> None:
    t = json.loads(Path(path).read_text(encoding="utf-8"))
    typed = [e.get("value", "") for e in t.get("events", [])
             if e.get("type") == "fill" and e.get("value") not in ("", "[REDACTED]")]

    print(f"TRACE  {path}")
    print(f"start: {t.get('start_url')}   source: {t.get('source')}   at: {t.get('recorded_at')}\n")

    print("FLOW")
    for i, e in enumerate(t.get("events", []), 1):
        kind = e.get("type")
        if kind == "navigate":
            print(f"{i:3d}. navigate  {e.get('url')}")
        elif kind == "fill":
            print(f"{i:3d}. type      {e.get('css')}  =  {e.get('value')!r}")
        elif kind == "check":
            print(f"{i:3d}. check     {e.get('css')}  [{e.get('label') or e.get('value')}]"
                  f"{'' if e.get('checked', True) else '  (unchecked)'}")
        elif kind == "select":
            print(f"{i:3d}. choose    {e.get('css')}  =  {e.get('option_text')!r}")
        elif kind == "enter":
            print(f"{i:3d}. Enter     in {e.get('css')}")
        elif kind == "click":
            net = f"   -> {len(e.get('triggered_network', []))} requests" if e.get("triggered_network") else ""
            print(f"{i:3d}. click     {e.get('css')}  [{e.get('text', '')[:50]}]{net}")
        else:
            print(f"{i:3d}. {kind:9s} {e.get('url', '')}")

    notes = t.get("notes", [])
    if notes:
        print("\nMARKED / NAMED ELEMENTS")
        for n in notes:
            label = n.get("note") or "(marked)"
            print(f"  - {label!r}: {n.get('css')}   text={n.get('text', '')[:60]!r}")
            if n.get("container_css"):
                print(f"      inside result card {n['container_css']!r}:  field selector "
                      f"{n.get('field_css') or '(none unique - pick one from outer_html)'}")
            c = likely_container(n.get("ancestors"))
            if c and not n.get("container_css"):
                print(f"      repeating container: {c['shape_css']}  "
                      f"({c['siblings_same_shape']} siblings, {c['page_count_same_shape']} on page)")
            print(f"      page: {n.get('url')}")

    if t.get("snapshots"):
        print("\nPAGE SNAPSHOTS (open these to design selectors offline)")
        for s in t["snapshots"]:
            print(f"  - {s['file']}   {s.get('url')}")

    print("\nCANDIDATE DATA CALLS (xhr/fetch, grouped by endpoint)")
    groups = {}
    for n in t.get("network", []):
        url = n.get("url", "")
        if n.get("resource_type") not in ("xhr", "fetch") or n.get("method") == "HEAD" \
                or url.split("?")[0].lower().endswith(STATIC_EXT):
            continue
        if any(c in ((n.get("response") or {}).get("content_type") or "") for c in STATIC_CTYPES):
            continue
        if any(h in url for h in NOISE_HOSTS):
            continue
        groups.setdefault((n["method"], url.split("?")[0]), []).append(n)
    if not groups:
        print("  (none - results are rendered in the HTML; use selectors)")
    def keyword_hits(calls):
        return [n for n in calls if any(
            k.lower() in unquote_plus(n.get("url", "") + " " + (n.get("body") or "")).lower()
            for k in typed if k)]

    for (method, endpoint), calls in sorted(groups.items(), key=lambda g: not keyword_hits(g[1])):
        hits = keyword_hits(calls)
        print(f"  {method:5s} {endpoint[:100]}   x{len(calls)} call(s)"
              + ("   ** carries the typed keyword -> probably THE search call" if hits else ""))
        for n in (hits[-1:] or calls[-1:]):
            resp = n.get("response") or {}
            print(f"        #{n['id']} status {resp.get('status', '---')}  {resp.get('content_type', '')[:40]}")
            print(f"        full url: {n['url'][:220]}")
            if n.get("body"):
                print(f"        request body: {n['body'][:300]}")
            if resp.get("body"):
                b = resp["body"]
                print(f"        response: {len(b)} chars: {b[:300].replace(chr(10), ' ')}")
        if len(calls) > 3 and hits and len(hits) < len(calls):
            print("        note: fired repeatedly while typing (search-as-you-type)")

    if t.get("downloads"):
        print("\nDOWNLOADS")
        for d in t["downloads"]:
            print(f"  - {d['suggested_filename']}  {d['url']}")
    errs = [c for c in t.get("console", []) if c.get("type") == "error"]
    if errs:
        print(f"\nCONSOLE ERRORS: {len(errs)} (first: {errs[0]['text'][:150]})")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
