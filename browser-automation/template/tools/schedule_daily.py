"""Make the scraper run by itself every day.

  python tools/schedule_daily.py --time 09:00         turn on (daily at 9am)
  python tools/schedule_daily.py --remove             turn off
  python tools/schedule_daily.py --time 09:00 --dry-run   show what would be done

macOS: a LaunchAgent (runs when you are logged in; a missed run happens on wake).
Windows: a Task Scheduler task. Linux: prints the crontab line to add.
"""
from __future__ import annotations

import argparse
import platform
import re
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
SLUG = re.sub(r"[^a-z0-9]+", "-", ROOT.name.lower()).strip("-") or "browser-automation"
LABEL = f"local.browserautomation.{SLUG}"


def mac_plist(hour: int, minute: int) -> str:
    launcher = escape(str(ROOT / "Run Automation.command"))
    root, log = escape(str(ROOT)), escape(str(ROOT / "logs" / "scheduled.log"))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>{launcher}</string><string>--scheduled</string>
  </array>
  <key>WorkingDirectory</key><string>{root}</string>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>{minute}</integer>
  </dict>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--time", default="09:00", help="HH:MM, 24-hour clock")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    hour, minute = (int(x) for x in a.time.split(":"))
    system = platform.system()

    if system == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        cmds = [["launchctl", "unload", str(plist)]]
        if not a.remove:
            cmds.append(["launchctl", "load", str(plist)])
        if a.dry_run:
            print(f"would write {plist}:\n{mac_plist(hour, minute)}")
            print("would run:", *[" ".join(c) for c in cmds], sep="\n  ")
            return
        if plist.exists():
            subprocess.run(cmds[0], capture_output=True)
        if a.remove:
            plist.unlink(missing_ok=True)
            print("Daily run turned off.")
            return
        (ROOT / "logs").mkdir(exist_ok=True)
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(mac_plist(hour, minute), encoding="utf-8")
        subprocess.run(cmds[1], check=True)
        print(f"Daily run turned on at {a.time}. Output goes to the output/ folder.")

    elif system == "Windows":
        name = f"BrowserAutomation {ROOT.name}"
        if a.remove:
            cmd = ["schtasks", "/Delete", "/F", "/TN", name]
        else:
            launcher = ROOT / "Run Automation.bat"
            cmd = ["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", f"{hour:02d}:{minute:02d}",
                   "/TN", name, "/TR", f'"{launcher}" --scheduled']
        if a.dry_run:
            print("would run:", subprocess.list2cmdline(cmd))
            return
        subprocess.run(cmd, check=True)
        print("Daily run turned off." if a.remove else f"Daily run turned on at {a.time}.")

    else:
        line = f'{minute} {hour} * * * cd "{ROOT}" && .venv/bin/python run.py --scheduled >> logs/scheduled.log 2>&1'
        print("Add this line with `crontab -e`:\n" + line)


if __name__ == "__main__":
    main()
