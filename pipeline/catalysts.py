"""Recent news that moves a stock, at market and government level.

Market level (Yahoo Finance): analyst upgrades/downgrades and price-target changes, earnings-estimate
revisions, next earnings date.
Government level: new US federal contract money (USAspending.gov) and recent SEC 8-K filings about major
company events (SEC EDGAR). All free, official or widely used sources; no keys.
"""
import re, datetime as dt, time
import requests
import yfinance as yf

import os
# The SEC asks every automated user to identify themselves with a contact email. Set SEC_CONTACT (a GitHub
# secret) to switch SEC company events on; without it they are skipped rather than sent with a fake address.
SEC_CONTACT = os.environ.get("SEC_CONTACT")
SEC_UA = {"User-Agent": f"CapitolSignal personal research {SEC_CONTACT}"} if SEC_CONTACT else None
USASPENDING = "https://api.usaspending.gov/api/v2/search/spending_by_transaction/"
EIGHT_K_ITEMS = {  # the 8-K items a reader most often cares about
    "1.01": "Signed a major agreement", "1.02": "Ended a major agreement", "1.03": "Bankruptcy",
    "2.01": "Completed an acquisition or sale", "2.02": "Results announced", "2.05": "Restructuring costs",
    "2.06": "Impairment", "3.01": "Listing problem", "4.01": "Changed auditor",
    "4.02": "Past results no longer reliable", "5.02": "Director or officer change",
    "7.01": "Regulation FD disclosure", "8.01": "Other events",
}
RED_FLAGS = {"1.03", "2.06", "3.01", "4.01", "4.02"}


def analyst_actions(ticker, days=30):
    """Upgrades/downgrades and target changes in the last `days` days."""
    try:
        ud = yf.Ticker(ticker).upgrades_downgrades
    except Exception:
        return None
    if ud is None or ud.empty:
        return {"up": 0, "down": 0, "items": []}
    since = dt.datetime.now() - dt.timedelta(days=days)
    ud = ud[ud.index >= since]
    up = down = 0
    items = []
    for when, r in ud.iterrows():
        act = str(r.get("Action") or "").lower()
        cur, prior = r.get("currentPriceTarget") or 0, r.get("priorPriceTarget") or 0
        tgt_dir = "raised" if cur and prior and cur > prior else "cut" if cur and prior and cur < prior else None
        is_up = act == "up" or (act in ("main", "reit") and tgt_dir == "raised")
        is_down = act == "down" or (act in ("main", "reit") and tgt_dir == "cut")
        up += is_up
        down += is_down
        items.append({"date": when.date().isoformat(), "firm": r.get("Firm"), "action": act,
                      "from": r.get("FromGrade") or None, "to": r.get("ToGrade") or None,
                      "target": round(float(cur), 2) if cur else None,
                      "prior_target": round(float(prior), 2) if prior else None,
                      "direction": "up" if is_up else "down" if is_down else "neutral"})
    return {"up": int(up), "down": int(down), "items": items[:8]}


def estimate_revisions(ticker):
    """Analysts raising vs cutting earnings estimates in the last 30 days (this year and next)."""
    try:
        er = yf.Ticker(ticker).eps_revisions
    except Exception:
        return None
    if er is None or er.empty:
        return None
    rows = [r for r in ("0y", "+1y") if r in er.index]
    up = int(sum(er.loc[r].get("upLast30days") or 0 for r in rows))
    down = int(sum(er.loc[r].get("downLast30days") or 0 for r in rows))
    return {"up": up, "down": down}


def next_earnings(ticker):
    try:
        cal = yf.Ticker(ticker).calendar or {}
    except Exception:
        return None
    dates = cal.get("Earnings Date") or []
    future = sorted(d for d in dates if d >= dt.date.today())
    return future[0].isoformat() if future else None


SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "company", "llc", "ltd", "lp", "plc", "the", "federal",
            "government", "services", "service", "solutions", "technologies", "technology", "holdings", "group",
            "international", "usa", "us", "america", "of", "and", "bank", "national", "association", "systems",
            "nv", "sa", "ag", "na", "defense", "public", "sector", "com", "web", "business", "network", "networks",
            "communications", "health", "healthcare", "operations", "enterprises", "enterprise", "global", "north",
            "american", "americas", "industries", "products", "laboratories", "labs", "mission", "support"}
# Where a company contracts under a different name than its stock listing
ALIASES = {"GOOGL": ["google"], "GOOG": ["google"], "AMZN": ["amazon"], "IBM": ["international business machines"],
           "MRK": ["merck sharp", "merck"], "META": ["meta platforms"], "BRK-B": ["berkshire hathaway"],
           "T": ["at t", "att"], "JNJ": ["johnson johnson", "janssen"], "LLY": ["eli lilly"],
           "RTX": ["raytheon", "rtx"], "GD": ["general dynamics"], "NOC": ["northrop grumman"],
           "LMT": ["lockheed martin"], "LHX": ["l3harris", "harris"], "BA": ["boeing"], "HII": ["huntington ingalls"],
           "BAH": ["booz allen hamilton"], "LDOS": ["leidos"], "SAIC": ["science applications international"],
           "BWXT": ["bwx", "bwxt"], "KBR": ["kbr"], "CACI": ["caci"], "OLN": ["olin"], "CDW": ["cdw"],
           "AEP": ["american electric power"], "WDC": ["western digital"], "FFIV": ["f5"], "ON": ["on semiconductor"],
           "FSLR": ["first solar"]}
# first words too common to identify a company on their own
GENERIC = {"apple", "compass", "life", "target", "crane", "strategy", "waters", "graham", "first", "general",
           "american", "national", "united", "public", "global", "new", "best", "southern", "pacific", "western"}
STOPWORDS = {"inc", "corp", "corporation", "company", "co", "ltd", "plc", "holdings", "group", "the", "class",
             "common", "stock", "ordinary", "shares", "nv", "sa", "ag", "se", "lp", "llc", "incorporated", "of", "and"}


def contract_names(ticker, company_name):
    """Names to look a listed company up by in federal spending records."""
    if ticker in ALIASES:
        return ALIASES[ticker]
    words = [w for w in re.sub(r"[^a-z0-9 ]", " ", (company_name or "").lower()).split() if w not in STOPWORDS]
    if not words or len(words[0]) < 3 or words[0] in GENERIC:
        return []
    return [" ".join(words[:2]) if len(words) > 1 and words[1] not in SUFFIXES else words[0]]


def is_company(recipient, name):
    """'THE BOEING COMPANY' is Boeing; 'AIRPORT CHEVRON AND TOWING' is not Chevron, 'PALO ALTO PERFUSION
    SERVICES' is not Palo Alto Networks. The name must start with the company's name and add only
    corporate words."""
    words = re.sub(r"[^a-z0-9 ]", " ", (recipient or "").lower()).split()
    while words and words[0] == "the":
        words = words[1:]
    key = name.split()
    return words[:len(key)] == key and all(w in SUFFIXES for w in words[len(key):])


def federal_contracts(ticker, company_name, days=90):
    """New US federal contract obligations to the company in the last `days` days (largest 100 actions)."""
    names = contract_names(ticker, company_name)
    if not names:
        return None
    body = {"filters": {"recipient_search_text": names, "award_type_codes": ["A", "B", "C", "D"],
                        "time_period": [{"start_date": (dt.date.today() - dt.timedelta(days=days)).isoformat(),
                                         "end_date": dt.date.today().isoformat()}]},
            "fields": ["Award ID", "Recipient Name", "Action Date", "Transaction Amount", "Awarding Agency",
                       "Transaction Description", "generated_internal_id"],
            "limit": 100, "sort": "Transaction Amount", "order": "desc"}
    for attempt in range(3):
        try:
            r = requests.post(USASPENDING, json=body, timeout=60)
            if r.status_code == 200:
                break
        except requests.RequestException:
            pass
        time.sleep(5 * (attempt + 1))
    else:
        return None
    rows = [x for x in r.json().get("results", [])
            if (x.get("Transaction Amount") or 0) > 0 and any(is_company(x.get("Recipient Name"), n) for n in names)]
    total = sum(x["Transaction Amount"] for x in rows)
    return {"total": round(total, 2), "count": len(rows), "searched": names, "items": [{
        "date": x["Action Date"], "amount": x["Transaction Amount"], "agency": x["Awarding Agency"],
        "recipient": x["Recipient Name"], "what": (x.get("Transaction Description") or "")[:140],
        "url": f"https://www.usaspending.gov/award/{x['generated_internal_id']}"} for x in rows[:5]]}


_CIK = None


def _cik(ticker):
    global _CIK
    if not SEC_UA:
        return None
    if _CIK is None:
        try:
            j = requests.get("https://www.sec.gov/files/company_tickers.json", headers=SEC_UA, timeout=60).json()
            _CIK = {v["ticker"].replace(".", "-"): v["cik_str"] for v in j.values()}
        except Exception:
            _CIK = {}
    return _CIK.get(ticker)


def sec_events(ticker, days=45):
    """8-K filings (major company events) in the last `days` days."""
    cik = _cik(ticker)
    if not cik:
        return None
    try:
        time.sleep(0.15)  # SEC asks for fewer than 10 requests a second
        d = requests.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=SEC_UA, timeout=60).json()
    except Exception:
        return None
    r, out = d["filings"]["recent"], []
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    for i, form in enumerate(r["form"]):
        if form != "8-K" or r["filingDate"][i] < since:
            continue
        items = [x for x in (r["items"][i] or "").split(",") if x]
        acc = r["accessionNumber"][i].replace("-", "")
        out.append({"date": r["filingDate"][i], "items": [EIGHT_K_ITEMS.get(x, f"Item {x}") for x in items if x != "9.01"],
                    "red_flag": any(x in RED_FLAGS for x in items),
                    "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{r['primaryDocument'][i]}"})
    return out[:6]


def gather(ticker, company_name):
    """company_name is the listed name, or None for funds (no contracts lookup)."""
    return {"analysts": analyst_actions(ticker), "estimates": estimate_revisions(ticker),
            "earnings_date": next_earnings(ticker),
            "contracts": federal_contracts(ticker, company_name) if company_name else None,
            "sec": sec_events(ticker)}


def gather_all(items, workers=None):
    """items: {ticker: company name or None}. Runs gently in parallel."""
    import os
    from concurrent.futures import ThreadPoolExecutor
    workers = workers or (3 if os.environ.get("CI") else 5)
    with ThreadPoolExecutor(workers) as ex:
        return dict(zip(items, ex.map(lambda t: gather(t, items[t]), items)))
