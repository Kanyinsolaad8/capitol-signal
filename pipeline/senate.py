"""Senate Periodic Transaction Reports (official eFD source)."""
import re, datetime as dt, html
import requests

BASE = "https://efdsearch.senate.gov"
TICKER_IN_NAME = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)")


class SenateError(RuntimeError):
    pass


def session():
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 capitol-signal personal research tool"
    home = s.get(f"{BASE}/search/home/", timeout=60)
    home.raise_for_status()
    tok = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', home.text)
    if not tok:
        raise SenateError("Senate eFD agreement page changed: no CSRF token")
    s.post(f"{BASE}/search/home/", data={"prohibition_agreement": "1", "csrfmiddlewaretoken": tok.group(1)},
           headers={"Referer": f"{BASE}/search/home/"}, timeout=60).raise_for_status()
    return s


def list_ptrs(s, since: dt.date):
    """All PTRs submitted since 'since'. Every filing is kept (a senator can file two separate reports on
    the same day); amended trades supersede earlier versions at trade level in build.py."""
    rows, start = [], 0
    while True:
        csrf = s.cookies.get("csrftoken")
        d = {"start": str(start), "length": "100", "report_types": "[11]", "filer_types": "[]",
             "submitted_start_date": since.strftime("%m/%d/%Y 00:00:00"), "submitted_end_date": "",
             "candidate_state": "", "senator_state": "", "office_id": "", "first_name": "",
             "last_name": "", "csrfmiddlewaretoken": csrf}
        r = s.post(f"{BASE}/search/report/data/", data=d, timeout=60,
                   headers={"Referer": f"{BASE}/search/", "X-CSRFToken": csrf})
        r.raise_for_status()
        j = r.json()
        for first, last, _full, link, fdate in j["data"]:
            href = re.search(r'href="([^"]+)"', link).group(1)
            text = html.unescape(re.sub(r"<[^>]+>", "", link))
            rep = re.search(r"for (\d{2}/\d{2}/\d{4})", text)
            amd = re.search(r"Amendment (\d+)", text)
            submitted = dt.datetime.strptime(fdate, "%m/%d/%Y").date()
            report_date = dt.datetime.strptime(rep.group(1), "%m/%d/%Y").date() if rep else submitted
            rows.append({
                "first": first.strip(), "last": last.strip(),
                "name": f"{first.strip()} {last.strip()}",
                "report_date": report_date.isoformat(),
                "filing_date": submitted.isoformat(),
                "amendment": int(amd.group(1)) if amd else 0,
                "electronic": "/ptr/" in href,  # /paper/ = scanned image
                "url": BASE + href,
            })
        start += 100
        if start >= j["recordsTotal"]:
            break
    for f in rows:
        f["amended"] = f["amendment"] > 0
        # an amendment of a report first filed before the window is a correction to old news
        f["old_news"] = f["amended"] and f["report_date"] < since.isoformat()
    return rows


def _cells(row_html):
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)
    return [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", c)).split()) for c in cells]


def parse_ptr(s, filing: dict):
    page = s.get(filing["url"], timeout=60, headers={"Referer": f"{BASE}/search/"}).text
    body = re.search(r"<tbody>(.*?)</tbody>", page, re.S)
    if not body:  # session expired or layout changed: fail loudly, never return a silent zero
        raise SenateError(f"No transaction table at {filing['url']}")
    trades = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(1), re.S):
        c = _cells(row)
        # columns: #, Transaction Date, Owner, Ticker, Asset Name, Asset Type, Type, Amount, Comment
        if len(c) < 8:
            continue
        ticker, asset, atype, tx = c[3], c[4], c[5], c[6]
        if atype not in ("Stock", "Stock Option"):
            continue
        if ticker in ("--", ""):  # ADRs often leave the ticker cell empty and put it in the name
            m = TICKER_IN_NAME.findall(asset)
            if not m:
                continue
            ticker = m[-1]
        option = None
        if atype == "Stock Option":
            option = "Put" if re.search(r"\bput\b", asset, re.I) else "Call" if re.search(r"\bcall\b", asset, re.I) else "Option"
        trades.append({
            "chamber": "Senate", "member": filing["name"], "first": filing["first"], "last": filing["last"],
            "state": None, "district": None,
            "owner": c[2],
            "ticker": ticker.replace(".", "-"),
            "asset": re.sub(r"\s*Option Type:.*$", "", asset).strip(),
            "asset_type": "Stock" if atype == "Stock" else "Option",
            "option_type": option,
            "type": {"Purchase": "Buy", "Sale (Full)": "Sell", "Sale (Partial)": "Sell (partial)",
                     "Exchange": "Exchange"}.get(tx, tx),
            "trade_date": dt.datetime.strptime(c[1], "%m/%d/%Y").date().isoformat(),
            "filing_date": filing["filing_date"],
            "amended": filing["amended"], "old_news": filing["old_news"], "amendment": filing["amendment"],
            "amount": c[7],
            "source": filing["url"],
        })
    return trades
