"""Decide what this run does, so every scheduled run can repair a missed one.

  full    read new filings and market data, then trade (the first successful run of each weekday)
  mark    today's full run already happened: just refresh prices for the holdings and the app
  weekly  Sunday summary (prices only)

Usage: python plan.py <event> <schedule> <manual mode>   ->  prints "mode=..." for GITHUB_OUTPUT
"""
import json, sys, pathlib, datetime as dt

ROOT = pathlib.Path(__file__).resolve().parent.parent
event, schedule, manual = (sys.argv[1:] + ["", "", ""])[:3]
state = ROOT / "state" / "portfolio.json"
last_full = json.loads(state.read_text()).get("last_full_run") if state.exists() else None
today = dt.date.today()

if event == "workflow_dispatch" and manual in ("weekly", "mark", "full"):
    mode = manual
elif schedule == "0 9 * * 0" or today.weekday() >= 5:
    mode = "weekly"
elif last_full == today.isoformat():
    mode = "mark"
else:
    mode = "full"
print(f"mode={mode}")
