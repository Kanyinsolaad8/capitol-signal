"""Push alerts to your phone through ntfy (https://ntfy.sh): free, no account.

Needs the secret channel name in the NTFY_TOPIC environment variable (stored as a GitHub secret).
Sends nothing when there is nothing to say, so your phone stays quiet on most days.

Usage: python notify.py moves | weekly | failed
"""
import json, os, sys, pathlib
import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
EVENTS = ROOT / "state" / "events.json"
NAMES = {"copy": "Copy", "filtered": "Filtered", "sp500": "S&P 500"}


def send(title, message, tags=(), priority=3):
    topic = os.environ.get("NTFY_TOPIC")
    payload = {"topic": topic, "title": title, "message": message, "tags": list(tags), "priority": priority}
    site = os.environ.get("SITE_URL")
    if site:
        payload["click"] = site
    if not topic:
        print("NTFY_TOPIC not set; would have sent:\n", json.dumps(payload, indent=1, ensure_ascii=False))
        return
    requests.post("https://ntfy.sh/", data=json.dumps(payload).encode("utf-8"), timeout=30).raise_for_status()


def summary_line(ev):
    l, start = ev["latest"], ev["start_gbp"]
    return " · ".join(f"{NAMES[k]} £{l[k]:.2f} ({(l[k] / start - 1) * 100:+.1f}%)" for k in ("filtered", "copy", "sp500"))


def main(mode):
    if mode == "failed":
        send("Capitol Signal: today's update failed", "The daily run hit an error. The app still shows the last "
             "good data. Open the GitHub Actions page for details.", tags=["warning"], priority=4)
        return
    ev = json.loads(EVENTS.read_text())
    if mode == "moves":
        try:
            senate_alert()
        except Exception as e:
            print("Senate alert not sent:", e)
        moves = ev["events"]
        if not moves:
            return
        lines = []
        for m in moves:
            who = NAMES[m["strategy"]]
            if m["action"] == "BUY":
                lines.append(f"{who}: BUY {m['ticker']} (£{m['gbp']:.2f}). {m['reason']}")
            else:
                lines.append(f"{who}: SELL {m['ticker']} ({m['pnl_pct']:+.1f}%). {m['reason']}")
        lines.append("")
        lines.append(summary_line(ev))
        send(f"Capitol Signal: {len(moves)} paper move{'s' if len(moves) > 1 else ''}", "\n".join(lines),
             tags=["chart_with_upwards_trend"])
    elif mode == "weekly":
        down = senate_state().get("down_since")
        note = f"\nSenate filings paused since {down} (site blocking automated access)." if down else ""
        send("Capitol Signal: weekly £500 test", f"Since {ev['start_date']}:\n{summary_line(ev)}{note}",
             tags=["calendar"])


def senate_state():
    p = ROOT / "state" / "senate-last.json"
    return json.loads(p.read_text()) if p.exists() else {}


def senate_alert():
    """One alert on the first day the Senate site blocks us, one when it is back. Quiet in between."""
    import datetime as dt
    s, today = senate_state(), dt.date.today().isoformat()
    if s.get("down_since") == today:
        send("Capitol Signal: Senate filings paused", "The Senate website is refusing automated access. The app "
             "keeps running on House filings and the last saved Senate data, and will pick the Senate up again "
             "when it allows access.", tags=["warning"])
    elif s.get("recovered") == today:
        send("Capitol Signal: Senate filings back", "The Senate website is answering again; Senate filings are "
             "up to date.", tags=["white_check_mark"])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "moves")
