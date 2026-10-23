"""Record a website flow while a person clicks through it.

Opens a visible Chromium window. The person does the search exactly as they
would by hand; everything is captured to traces/<name>/trace.json:

  - clicks, typing, dropdowns, Enter presses, page navigations, new tabs
  - every network call (API JSON bodies kept, static files skipped)
  - console errors, downloads
  - marks and notes the person adds with hotkeys:
        Ctrl+Shift+M   mark the hovered element as important
        Ctrl+Shift+N   label the hovered element: click "company", "phone",
                       "next page" ... in the panel, or type a custom label
        Ctrl+Shift+S   save a snapshot of the whole page (HTML) for offline work
  - for every mark/note: the element's ancestor chain, so the repeating
    "result card" around it can be found later

Passwords and auth headers are redacted before saving.
Stop by closing the browser window (or Ctrl+C in the terminal).

  python tools/record_trace.py --name acme_directory --url https://acme.example/search
"""
from __future__ import annotations

import argparse
import json
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent

# ── Injected into every page and frame before the site's own scripts ────────
INJECT_JS = r"""
(function () {
  if (window.__recInstalled) return; window.__recInstalled = true;
  const OWN = el => !!(el && el.closest && el.closest('[id^="__rec_"]'));

  function bestSelector(el) {
    if (!el || el === document || el === window) return 'body';
    if (el === document.documentElement || el === document.body) return el.tagName.toLowerCase();
    if (el.id && !/^\d/.test(el.id) && !/[\s:[\](){}|,>~+]/.test(el.id)) return '#' + el.id;
    const tag = el.tagName.toLowerCase();
    const uniq = s => { try { return document.querySelectorAll(s).length === 1; } catch (e) { return false; } };
    for (const a of ['name', 'data-testid', 'data-test', 'aria-label', 'placeholder', 'title', 'href', 'rel']) {
      const v = el.getAttribute(a);
      if (v) { const s = `${tag}[${a}="${v.replace(/"/g, '\\"')}"]`; if (uniq(s)) return s; }
    }
    if (tag === 'input' && el.type && !['text', 'search'].includes(el.type)) {
      const s = `input[type="${el.type}"]`; if (uniq(s)) return s;
    }
    if (el.classList && el.classList.length) {
      const s = tag + [...el.classList].slice(0, 3).map(c => '.' + CSS.escape(c)).join('');
      if (uniq(s)) return s;
    }
    const path = n => {
      if (!n || n === document.body || n === document.documentElement) return '';
      const p = n.parentElement; if (!p) return n.tagName.toLowerCase();
      const part = n.tagName.toLowerCase() + ':nth-child(' + ([...p.children].indexOf(n) + 1) + ')';
      const pp = path(p); return pp ? pp + ' > ' + part : part;
    };
    return path(el);
  }

  // Class-based selector that matches the element AND its look-alike siblings.
  function shapeSelector(el) {
    const tag = el.tagName.toLowerCase();
    const cls = [...(el.classList || [])].filter(c => !/\d{3,}|active|selected|hover/i.test(c));
    return cls.length ? tag + cls.slice(0, 2).map(c => '.' + CSS.escape(c)).join('') : tag;
  }

  function ancestors(el) {
    const out = []; let n = el; let depth = 0;
    while (n && n !== document.body && depth < 8) {
      const p = n.parentElement;
      const shape = shapeSelector(n);
      const same = p ? [...p.children].filter(c => { try { return c.matches(shape); } catch (e) { return false; } }).length : 1;
      let pageCount = 0; try { pageCount = document.querySelectorAll(shape).length; } catch (e) {}
      out.push({ depth, tag: n.tagName.toLowerCase(), css: bestSelector(n), shape_css: shape,
                 siblings_same_shape: same, page_count_same_shape: pageCount });
      n = p; depth++;
    }
    return out;
  }

  // The nearest ancestor that repeats (3+ look-alike siblings) is one result
  // card; give a selector for the element that works *inside* that card.
  function inContainer(el) {
    let n = el.parentElement;
    while (n && n !== document.body) {
      const p = n.parentElement, shape = shapeSelector(n);
      const same = p ? [...p.children].filter(c => { try { return c.matches(shape); } catch (e) { return false; } }).length : 1;
      const cardLike = shape.includes('.') || /^(li|tr|article)$/.test(n.tagName.toLowerCase());
      if (same >= 3 && cardLike && (n.innerText || '').trim().length > 20) {
        // A field selector must pick exactly this element inside its card AND
        // exist in most look-alike cards (so a value like a[title="X"] is rejected).
        let peers = []; try { peers = [...document.querySelectorAll(shape)]; } catch (e) {}
        const good = (t, node, card) => { try {
          return card.querySelector(t) === node &&
            peers.filter(c => c.querySelector(t)).length >= Math.max(2, Math.ceil(peers.length * 0.8));
        } catch (e) { return false; } };
        const candidates = node => {
          const tag = node.tagName.toLowerCase(), sh = shapeSelector(node), out = [];
          if (sh.includes('.')) out.push(sh);
          if (node.parentElement && node.parentElement !== n) out.push(`${shapeSelector(node.parentElement)} > ${sh}`);
          for (const a of ['itemprop', 'data-testid', 'name', 'rel']) {
            const v = node.getAttribute(a); if (v) out.push(`${tag}[${a}="${v.replace(/"/g, '\\"')}"]`);
          }
          if (!sh.includes('.')) out.push(sh);  // a bare tag is the last resort
          return out;
        };
        for (const t of candidates(el)) if (good(t, el, n)) return { container_css: shape, field_css: t };
        // Nothing works on the element itself (e.g. an <em> inside a link):
        // fall back to the closest ancestor inside the card that does.
        for (let up = el.parentElement; up && up !== n; up = up.parentElement)
          for (const t of candidates(up)) if (good(t, up, n)) return { container_css: shape, field_css: t, field_is_ancestor: true };
        return { container_css: shape, field_css: null };
      }
      n = p;
    }
    return {};
  }

  function attrs(el) {
    const a = {};
    for (const at of (el.attributes || [])) if (!['class', 'style'].includes(at.name)) a[at.name] = at.value.slice(0, 150);
    return a;
  }

  function info(el, extra) {
    const r = el.getBoundingClientRect();
    return Object.assign({
      css: bestSelector(el), tag: el.tagName ? el.tagName.toLowerCase() : '',
      text: (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 200),
      name: el.name || '', id: el.id || '', input_type: el.type || '',
      href: el.href || '', url: location.href,
      box: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }
    }, extra || {});
  }
  const send = (fn, data) => { try { if (typeof window[fn] === 'function') window[fn](data); } catch (e) {} };

  // ── hover overlay + help badge ──
  let box, tip, hovered = null;
  function ui() {
    if (box || !document.body) return;
    box = document.createElement('div'); box.id = '__rec_box';
    box.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483646;border:2px solid #00c8ff;background:rgba(0,200,255,.12);display:none';
    tip = document.createElement('div'); tip.id = '__rec_tip';
    tip.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;background:#111;color:#7CFC00;font:11px monospace;padding:4px 7px;border-radius:4px;max-width:420px;display:none';
    if (window.top === window) {
      const badge = document.createElement('div'); badge.id = '__rec_badge';
      badge.style.cssText = 'position:fixed;right:10px;bottom:10px;z-index:2147483647;background:rgba(17,17,17,.88);color:#fff;font:12px system-ui;padding:8px 10px;border-radius:8px;line-height:1.5;pointer-events:none';
      badge.innerHTML = '<b style="color:#ff5c5c">● REC</b><br>Ctrl+Shift+M mark<br>Ctrl+Shift+N label it<br>Ctrl+Shift+S snapshot page<br>Close window to finish';
      document.body.appendChild(badge);
    }
    document.body.append(box, tip);
  }
  // Sites often lay an invisible full-card link over the content; prefer the
  // smallest element under the cursor that has its own visible text.
  const area = x => { const r = x.getBoundingClientRect(); return r.width * r.height; };
  function underCursor(x, y) {
    const stack = document.elementsFromPoint(x, y).filter(n => !OWN(n));
    const texty = stack.filter(n => n !== document.body && n !== document.documentElement &&
      (n.innerText || '').trim() && area(n) > 0);
    return texty.length ? texty.reduce((a, b) => (area(a) <= area(b) ? a : b)) : stack[0];
  }
  document.addEventListener('mousemove', e => {
    ui(); const el = underCursor(e.clientX, e.clientY);
    if (!el || OWN(el) || !box) return;
    hovered = el; const r = el.getBoundingClientRect();
    Object.assign(box.style, { display: 'block', left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px' });
    tip.textContent = bestSelector(el);
    Object.assign(tip.style, { display: 'block', left: (e.clientX + 12) + 'px', top: (e.clientY + 12) + 'px' });
  }, true);

  // ── interactions ──
  document.addEventListener('click', e => {
    if (!e.target || !e.target.tagName || OWN(e.target)) return;
    // An icon inside a button/link: record the control, not the <svg>.
    const el = (e.target.closest && e.target.closest('a,button,[role=button],label,summary,input,select')) || e.target;
    const tag = el.tagName.toLowerCase();
    if (tag === 'select' || tag === 'textarea') return;
    if (tag === 'input' && /^(radio|checkbox)$/.test(el.type)) return;  // logged as 'check' on change
    if (tag === 'input' && /^(text|password|email|search|tel|url|number|date|time|datetime-local|month|week)$/.test(el.type || 'text')) return;
    send('__recEvent', info(el, { type: 'click' }));
  }, true);
  document.addEventListener('change', e => {
    const el = e.target; if (!el || !el.tagName || OWN(el)) return;
    const tag = el.tagName.toLowerCase();
    if (tag === 'select' && el.selectedIndex >= 0) {
      const o = el.options[el.selectedIndex];
      send('__recEvent', info(el, { type: 'select', value: o.value, option_text: o.text.trim() }));
    } else if (tag === 'input' && /^(radio|checkbox)$/.test(el.type)) {
      const lab = (el.id && document.querySelector(`label[for="${el.id}"]`)) || el.closest('label');
      send('__recEvent', info(el, { type: 'check', value: el.value, checked: el.checked,
        label: lab ? lab.innerText.trim().replace(/\s+/g, ' ') : '' }));
    } else if (tag === 'input' || tag === 'textarea') {
      send('__recEvent', info(el, { type: 'fill', value: el.value }));
    }
  }, true);
  const typing = new WeakMap();
  document.addEventListener('input', e => {
    const el = e.target; if (!el || OWN(el) || !/^(INPUT|TEXTAREA)$/.test(el.tagName)) return;
    if (/^(radio|checkbox|submit|button)$/.test(el.type)) return;
    clearTimeout(typing.get(el));
    typing.set(el, setTimeout(() => el.value && send('__recEvent', info(el, { type: 'fill', value: el.value, source: 'typing' })), 1500));
  }, true);

  // ── hotkeys + Enter ──
  // Labelling panel. One click on a common label is enough (typing into an
  // injected box is unreliable on some machines/sites); custom text is optional.
  const LABELS = ['title', 'company', 'contact name', 'phone', 'email', 'price', 'location',
                  'date', 'link', 'next page', 'search box', 'result card'];
  let dialog = null;
  function askName(el) {
    if (dialog) dialog.remove();
    dialog = document.createElement('div'); dialog.id = '__rec_dialog';
    dialog.style.cssText = 'position:fixed;top:20%;left:50%;transform:translateX(-50%);z-index:2147483647;background:#1e1e1e;border:2px solid #00c8ff;padding:16px;border-radius:8px;width:420px;font:13px system-ui;color:#fff;box-shadow:0 8px 30px rgba(0,0,0,.5)';
    // Keep the panel's events away from the site's own scripts.
    for (const t of ['keydown', 'keypress', 'keyup', 'input', 'click', 'mousedown', 'mouseup', 'pointerdown', 'pointerup'])
      dialog.addEventListener(t, ev => ev.stopPropagation());
    const close = () => { if (dialog) { dialog.remove(); dialog = null; } };
    const save = name => {
      name = (name || '').trim(); if (!name) return;
      send('__recNote', info(el, Object.assign({ kind: 'named', note: name, attributes: attrs(el), ancestors: ancestors(el), outer_html: el.outerHTML.slice(0, 3000) }, inContainer(el))));
      close(); flash(el, '#00c8ff');
    };
    const btnCss = 'margin:3px;padding:6px 10px;border-radius:14px;border:1px solid #00c8ff;background:#123;color:#fff;font:13px system-ui;cursor:pointer';
    const head = document.createElement('div');
    head.style.cssText = 'margin-bottom:10px';
    head.innerHTML = '<b>What is this?</b> Click a label (or type your own below).<br><small style="color:#aaa">Hovered: <code style="color:#7CFC00">' + (el.innerText || el.tagName).trim().slice(0, 50).replace(/</g, '&lt;') + '</code></small>';
    const chips = document.createElement('div');
    for (const label of LABELS) {
      const b = document.createElement('button'); b.type = 'button'; b.textContent = label; b.style.cssText = btnCss;
      b.addEventListener('click', ev => { ev.preventDefault(); save(label); });
      chips.appendChild(b);
    }
    const row = document.createElement('div'); row.style.cssText = 'display:flex;gap:6px;margin-top:10px';
    const inp = document.createElement('input'); inp.id = '__rec_input'; inp.placeholder = 'custom label (optional)';
    inp.style.cssText = 'flex:1;padding:6px;font:13px system-ui;color:#000;background:#fff;border:1px solid #888;border-radius:4px';
    inp.addEventListener('keydown', ev => { if (ev.key === 'Enter') save(inp.value); if (ev.key === 'Escape') close(); });
    const ok = document.createElement('button'); ok.type = 'button'; ok.textContent = 'OK'; ok.style.cssText = btnCss;
    ok.addEventListener('click', ev => { ev.preventDefault(); save(inp.value); });
    const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Cancel'; cancel.style.cssText = btnCss + ';border-color:#777;background:#333';
    cancel.addEventListener('click', ev => { ev.preventDefault(); close(); });
    row.append(inp, ok, cancel);
    dialog.append(head, chips, row); document.body.appendChild(dialog);
    setTimeout(() => inp.focus(), 0);
  }
  function flash(el, color) { const o = el.style.outline; el.style.outline = '3px solid ' + color; setTimeout(() => { el.style.outline = o; }, 600); }
  document.addEventListener('keydown', e => {
    if (OWN(e.target)) return;
    if (e.ctrlKey && e.shiftKey) {
      const k = e.key.toLowerCase();
      if (k === 'm' && hovered) { e.preventDefault(); send('__recNote', info(hovered, Object.assign({ kind: 'marked', attributes: attrs(hovered), ancestors: ancestors(hovered), outer_html: hovered.outerHTML.slice(0, 3000) }, inContainer(hovered)))); flash(hovered, '#00ff66'); return; }
      if (k === 'n' && hovered) { e.preventDefault(); askName(hovered); return; }
      if (k === 's') { e.preventDefault(); send('__recSnapshot', { url: location.href, title: document.title, html: document.documentElement.outerHTML }); flash(document.body, '#ffcc00'); return; }
    }
    if (e.key === 'Enter' && e.target && /^(INPUT|TEXTAREA)$/.test(e.target.tagName)) send('__recEvent', info(e.target, { type: 'enter' }));
  }, true);
})();
"""

STATIC_TYPES = ("image/", "font/", "audio/", "video/", "text/css", "javascript")
SECRET_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key", "x-auth-token"}
SECRET_BODY = re.compile(r'((?:pass(?:word|wd)?|pwd|otp|pin|token|secret)["\']?\s*[=:]\s*["\']?)[^&"\'\s,}]+', re.I)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _redact_headers(h: dict) -> dict:
    return {k: ("[REDACTED]" if k.lower() in SECRET_HEADERS else v) for k, v in h.items()}


class TraceRecorder:
    """Attach to a Playwright BrowserContext and collect everything into a trace."""

    def __init__(self, context, out_dir: Path, start_url: str = "", verbose: bool = True):
        self.context = context
        self.out_dir = out_dir
        self.start_url = start_url
        self.verbose = verbose
        self.events: list = []
        self.notes: list = []
        self.snapshots: list = []
        self.network: dict = {}
        self.console: list = []
        self.downloads: list = []
        (out_dir / "snapshots").mkdir(parents=True, exist_ok=True)
        context.expose_binding("__recEvent", self._on_event)
        context.expose_binding("__recNote", self._on_note)
        context.expose_binding("__recSnapshot", self._on_snapshot)
        context.add_init_script(INJECT_JS)
        context.on("page", self.attach_page)

    def _say(self, msg: str) -> None:
        if self.verbose:
            print("  " + msg, flush=True)

    # ── browser -> python callbacks ─────────────────────────────────────────
    def _on_event(self, _src, data: dict) -> None:
        data["timestamp"] = _now_ms()
        if data.get("type") == "fill" and (data.get("input_type") == "password"
                                           or re.search(r"pass|pwd|otp|pin", data.get("name", ""), re.I)):
            data["value"] = "[REDACTED]"
        self.events.append(data)
        detail = data.get("value") if data.get("type") == "fill" else (data.get("text") or data.get("css"))
        self._say(f"{data.get('type', '?').upper():9s} {str(detail)[:70]}")

    def _on_note(self, _src, data: dict) -> None:
        data["timestamp"] = _now_ms()
        self.notes.append(data)
        label = data.get("note") or data.get("css")
        self._say(f"{'NAMED' if data.get('kind') == 'named' else 'MARKED':9s} {str(label)[:70]}")

    def _on_snapshot(self, _src, data: dict) -> None:
        n = len(self.snapshots) + 1
        path = self.out_dir / "snapshots" / f"page_{n:02d}.html"
        path.write_text(data.get("html", ""), encoding="utf-8")
        self.snapshots.append({"file": f"snapshots/{path.name}", "url": data.get("url"),
                               "title": data.get("title"), "timestamp": _now_ms()})
        self._say(f"SNAPSHOT  {path.name}  ({data.get('url', '')[:60]})")

    # ── page-level listeners ────────────────────────────────────────────────
    def attach_page(self, page) -> None:
        def on_nav(frame):
            if frame == page.main_frame:
                self.events.append({"type": "navigate", "url": frame.url, "timestamp": _now_ms()})
                self._say(f"NAVIGATE  {frame.url[:80]}")

        def on_request(req):
            entry = {"id": len(self.network) + 1, "method": req.method, "url": req.url,
                     "resource_type": req.resource_type, "timestamp": _now_ms(),
                     "headers": _redact_headers(dict(req.headers)), "response": None}
            try:
                if req.method in ("POST", "PUT", "PATCH") and req.post_data:
                    entry["body"] = SECRET_BODY.sub(r"\1[REDACTED]", req.post_data[:20000])
            except Exception:  # noqa: BLE001 - binary bodies
                pass
            self.network[id(req)] = entry

        def on_response(resp):
            entry = self.network.get(id(resp.request))
            if entry is None:
                return
            ctype = resp.headers.get("content-type", "")
            entry["response"] = {"status": resp.status, "content_type": ctype,
                                 "headers": _redact_headers(dict(resp.headers)),
                                 "timestamp": _now_ms(), "body": None}
            if any(t in ctype for t in ("json", "xml", "text/plain")) and \
                    not any(t in ctype for t in STATIC_TYPES):
                try:
                    body = resp.body()
                    if len(body) < 200_000:
                        entry["response"]["body"] = body.decode("utf-8", "replace")
                except Exception:  # noqa: BLE001 - body may be gone after navigation
                    pass

        def on_console(msg):
            if msg.type in ("error", "warning"):
                self.console.append({"type": msg.type, "text": msg.text[:500], "timestamp": _now_ms()})

        def on_download(dl):
            self.downloads.append({"url": dl.url, "suggested_filename": dl.suggested_filename,
                                   "timestamp": _now_ms()})
            self._say(f"DOWNLOAD  {dl.suggested_filename}")

        def on_popup(p):
            self.events.append({"type": "popup", "url": p.url, "timestamp": _now_ms()})

        page.on("framenavigated", on_nav)
        page.on("request", on_request)
        page.on("response", on_response)
        page.on("console", on_console)
        page.on("download", on_download)
        page.on("popup", on_popup)

    # ── output ──────────────────────────────────────────────────────────────
    def build(self) -> dict:
        events = _dedupe(self.events)
        net = sorted(self.network.values(), key=lambda n: n["timestamp"])
        for ev in events:  # which network calls did each action trigger?
            ts = ev["timestamp"]
            ev["triggered_network"] = [n["id"] for n in net
                                       if ts < n["timestamp"] <= ts + 2500
                                       and n["resource_type"] in ("xhr", "fetch", "document")]
        return {
            "format": "browser-automation-trace/1",
            "source": "recorder",
            "start_url": self.start_url,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "events": events,
            "notes": self.notes,
            "snapshots": self.snapshots,
            "network": net,
            "downloads": self.downloads,
            "console": self.console,
        }

    def save(self) -> Path:
        trace = self.build()
        path = self.out_dir / "trace.json"
        path.write_text(json.dumps(trace, indent=2, ensure_ascii=False), encoding="utf-8")
        return path


def _dedupe(events: list) -> list:
    """Keep the last value typed into each field; drop repeated navigations."""
    out, fill_at, last_nav = [], {}, None
    for ev in events:
        if ev.get("type") == "fill":
            key = (ev.get("css"), ev.get("url"))
            if key in fill_at and fill_at[key] == len(out) - 1:
                out[fill_at[key]] = ev
                continue
            fill_at[key] = len(out)
        if ev.get("type") == "navigate":
            if ev.get("url") == last_nav:
                continue
            last_nav = ev.get("url")
        out.append(ev)
    return out


def record(name: str, url: str, storage_state: Optional[str] = None) -> Path:
    from playwright.sync_api import sync_playwright

    out_dir = ROOT / "traces" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    stop = {"flag": False}

    def on_sigint(*_):
        if stop["flag"]:
            sys.exit(1)
        stop["flag"] = True
        print("\n  Stopping, saving trace...")

    signal.signal(signal.SIGINT, on_sigint)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, args=[
            "--start-maximized", "--disable-blink-features=AutomationControlled"])
        context = browser.new_context(no_viewport=True, accept_downloads=True,
                                      storage_state=storage_state)
        rec = TraceRecorder(context, out_dir, url)
        page = context.new_page()
        if url:
            try:
                page.goto(url, wait_until="domcontentloaded")
            except Exception as exc:  # noqa: BLE001
                print(f"  Could not open {url}: {exc}")
        print("\n  RECORDING. Do the search in the browser as you normally would.")
        print("  Hover something + Ctrl+Shift+N, then click a label (company, phone, next page...).")
        print("  Close the browser window when done.\n")
        # page.wait_for_timeout pumps Playwright's event loop; time.sleep would
        # block it and the browser->python callbacks would never be delivered.
        while not stop["flag"]:
            try:
                pages = context.pages
                if not browser.is_connected() or not pages:
                    break
                pages[-1].wait_for_timeout(250)
            except Exception:  # noqa: BLE001 - window closed mid-wait
                if not browser.is_connected():
                    break
        path = rec.save()
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass
    t = json.loads(path.read_text(encoding="utf-8"))
    print(f"\n  Saved {path}")
    print(f"  {len(t['events'])} actions, {len(t['notes'])} marks/notes, "
          f"{len(t['snapshots'])} snapshots, {len(t['network'])} network calls")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="site name, used as the trace folder")
    ap.add_argument("--url", default="", help="page to open first")
    ap.add_argument("--use-login", action="store_true",
                    help="start already logged in, using state/<name>.json")
    a = ap.parse_args()
    state = ROOT / "state" / f"{a.name}.json"
    record(a.name, a.url, str(state) if a.use_login and state.exists() else None)


if __name__ == "__main__":
    main()
