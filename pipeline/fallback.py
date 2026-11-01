"""When filings can't be read (or on price-only runs), reuse the filings data already published in the app,
so the £500 test and the app still update with today's prices. Writes data/data.json."""
import json, os, re, sys, pathlib, datetime as dt
import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
url = os.environ.get("SITE_URL")
page = requests.get(url, timeout=60)
page.raise_for_status()
d = json.loads(re.search(r'<script type="application/json" id="data">(.*?)</script>', page.text, re.S).group(1))
d["filings_generated"] = d.get("filings_generated") or d["generated"]
d["generated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
d["filings_failed"] = sys.argv[1] == "failed" if len(sys.argv) > 1 else False
(ROOT / "data").mkdir(exist_ok=True)
(ROOT / "data" / "data.json").write_text(json.dumps(d))
print(f"Reusing filings read on {d['filings_generated']}")
