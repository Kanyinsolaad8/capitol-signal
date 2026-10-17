"""Bills each member sponsored or co-sponsored (official GovInfo BILLSTATUS bulk data), linked to traded tickers."""
import io, re, zipfile, datetime as dt, pathlib
import xml.etree.ElementTree as ET
import requests

TYPES = {"hr": "H.R.", "s": "S.", "hjres": "H.J.Res.", "sjres": "S.J.Res.", "hres": "H.Res.", "sres": "S.Res."}
URL = "https://www.govinfo.gov/bulkdata/BILLSTATUS/{c}/{t}/BILLSTATUS-{c}-{t}.zip"

# Congress.gov policy areas -> Yahoo sectors they mainly bear on (editable rule set)
POLICY_SECTORS = {
    "Armed Forces and National Security": {"Industrials", "Technology"},
    "Health": {"Healthcare"},
    "Finance and Financial Sector": {"Financial Services", "Real Estate"},
    "Taxation": set(),  # too broad to link on its own
    "Energy": {"Energy", "Utilities"},
    "Environmental Protection": {"Energy", "Utilities", "Basic Materials"},
    "Science, Technology, Communications": {"Technology", "Communication Services"},
    "Transportation and Public Works": {"Industrials"},
    "Agriculture and Food": {"Consumer Defensive", "Basic Materials"},
    "Commerce": {"Consumer Cyclical", "Consumer Defensive", "Technology", "Communication Services"},
    "Foreign Trade and International Finance": {"Industrials", "Technology", "Basic Materials"},
    "Housing and Community Development": {"Real Estate", "Financial Services"},
    "Public Lands and Natural Resources": {"Energy", "Basic Materials"},
    "Water Resources Development": {"Utilities", "Industrials"},
}
# Words in a bill's TITLE -> Yahoo industries the bill bears on directly (editable rule set).
# Title-only on purpose: subject tags are too broad (e.g. a farm cybersecurity bill tagged "cybersecurity").
TITLE_INDUSTRIES = {  # pattern: (industries, strength) — 2 counts toward the score, 1 is shown as context only
    r"semiconductor|chips act|microchip": ({"Semiconductors", "Semiconductor Equipment & Materials"}, 2),
    r"prescription drug|drug pric|pharmacy benefit|pharmaceutical": ({
        "Drug Manufacturers - General", "Drug Manufacturers - Specialty & Generic", "Biotechnology",
        "Healthcare Plans", "Pharmaceutical Retailers"}, 2),
    r"medicare advantage": ({"Healthcare Plans"}, 2),
    r"medical device": ({"Medical Devices", "Medical Instruments & Supplies"}, 2),
    r"(?<!west )\bbank(s|ing)?\b": ({"Banks - Diversified", "Banks - Regional"}, 2),
    r"^national defense authorization act for fiscal year|munitions|shipbuilding|missile|defense industrial|fighter jet|military aircraft":
        ({"Aerospace & Defense"}, 2),
    r"aviation|aircraft": ({"Airlines", "Aerospace & Defense"}, 2),
    r"airline": ({"Airlines"}, 2),
    r"\boil\b|natural gas|gas pipeline|oil pipeline|\blng\b": ({"Oil & Gas Integrated", "Oil & Gas E&P",
        "Oil & Gas Midstream", "Oil & Gas Refining & Marketing"}, 2),
    r"nuclear (energy|power|reactor|fuel)": ({"Utilities - Regulated Electric", "Uranium", "Utilities - Renewable"}, 2),
    r"electric grid|power grid|transmission line": ({"Utilities - Regulated Electric", "Utilities - Diversified"}, 2),
    r"broadband|spectrum|telecommunication": ({"Telecom Services", "Communication Equipment"}, 2),
    r"railroad|\brail\b": ({"Railroads"}, 2),
    # broad themes: shown for context, not scored
    r"artificial intelligence|\bai\b": ({"Semiconductors", "Software - Infrastructure", "Software - Application",
                                         "Internet Content & Information"}, 1),
    r"data cent(er|re)s?": ({"Semiconductors", "Software - Infrastructure", "REIT - Specialty",
                             "Utilities - Regulated Electric"}, 1),
    r"digital asset|stablecoin|crypto": ({"Capital Markets", "Financial Data & Stock Exchanges"}, 1),
    r"social media|online platform": ({"Internet Content & Information"}, 1),
}
COMMON = {"public", "service", "services", "general", "american", "america", "first", "national", "united", "energy",
          "health", "financial", "global", "systems", "solutions", "industries", "enterprise", "enterprises",
          "partners", "bank", "trust", "capital", "power", "electric", "new", "york", "southern", "pacific",
          "western", "eastern", "northern", "data", "life", "care", "home", "community", "federal", "security",
          "insurance", "realty", "resources", "brands", "foods", "water", "motors", "mobile", "digital", "best"}


def _bill(root):
    b = root.find("bill")
    t = (b.findtext("type") or "").lower()
    num = b.findtext("number")
    pa = b.findtext("policyArea/name")
    subjects = [s.findtext("name") for s in b.findall("subjects/legislativeSubjects/item")]
    title = b.findtext("title")
    sponsors = [s.findtext("bioguideId") for s in b.findall("sponsors/item")]
    cos = [(c.findtext("bioguideId"), c.findtext("sponsorshipDate")) for c in b.findall("cosponsors/item")
           if c.findtext("sponsorshipWithdrawnDate") is None]
    return {
        "id": f"{TYPES.get(t, t.upper())} {num}", "title": title,
        "introduced": b.findtext("introducedDate"), "policy_area": pa, "subjects": subjects,
        "latest_action": b.findtext("latestAction/text"), "latest_date": b.findtext("latestAction/actionDate"),
        "url": b.findtext("legislationUrl") or
               f"https://www.congress.gov/bill/{b.findtext('congress')}th-congress/{t}/{num}",
        "support": [(s, "Sponsor", b.findtext("introducedDate")) for s in sponsors] +
                   [(c, "Co-sponsor", d) for c, d in cos],
    }


def congress_for(d: dt.date):
    """119th Congress = 2025-2026; a new Congress starts each odd year."""
    return (d.year - 1789) // 2 + 1


def load(congresses, cache_dir=None, max_age_hours=30):
    bills = []
    for congress in congresses:
        bills += _load_one(congress, cache_dir, max_age_hours)
    return bills


def _load_one(congress, cache_dir, max_age_hours):
    import time
    bills = []
    for t in TYPES:
        p = pathlib.Path(cache_dir) / f"{congress}-{t}.zip" if cache_dir else None
        if p and p.exists() and time.time() - p.stat().st_mtime < max_age_hours * 3600:
            raw = p.read_bytes()
        else:
            r = requests.get(URL.format(c=congress, t=t), timeout=600)
            if r.status_code == 404:  # a brand-new Congress may have no bills of this type yet
                continue
            r.raise_for_status()
            raw = r.content
            if p:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(raw)
        z = zipfile.ZipFile(io.BytesIO(raw))
        for name in z.namelist():
            try:
                bills.append(_bill(ET.fromstring(z.read(name))))
            except Exception:
                continue
    return bills


def by_member(bills):
    idx = {}
    for b in bills:
        for bio, role, date in b["support"]:
            idx.setdefault(bio, []).append((b, role, date))
    return idx


STOP = {"of", "and", "the", "inc", "corp", "corporation", "company", "co", "ltd", "plc", "holdings", "group", "the", "class", "common",
        "stock", "ordinary", "shares", "nv", "sa", "ag", "se", "lp", "llc", "incorporated", "technologies", "international"}


def company_key(name):
    """'Lockheed Martin Corporation' -> 'lockheed martin' (used for exact phrase match in bill titles)."""
    words = [w for w in re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split() if w not in STOP]
    key = " ".join(words[:2])
    if len(key) < 5 or all(w in COMMON for w in key.split()):
        return None  # too generic to match in free text ("public service")
    return key


def links(member_bills, trade_date, sector, industry, company, window=120):
    """Bills the member backed within 'window' days of the trade that touch this company or its sector."""
    td = dt.date.fromisoformat(trade_date)
    ckey = company_key(company)
    out = []
    for b, role, date in member_bills:
        try:
            d = dt.date.fromisoformat((date or b["introduced"])[:10])
        except ValueError:
            continue
        if abs((d - td).days) > window:
            continue
        title = (b["title"] or "").lower()
        subj = " ".join(b["subjects"]).lower()
        why, strength = None, 0
        sector_ok = sector in POLICY_SECTORS.get(b["policy_area"], set())
        # one-word names ("Strategy", "Target", "Crane") are ordinary words too: also require the bill's
        # policy area to fit the company's sector
        if ckey and re.search(rf"\b{re.escape(ckey)}\b", title) and (" " in ckey or sector_ok):
            why, strength = "Names the company", 3
        else:
            hit = [st for k, (inds, st) in TITLE_INDUSTRIES.items() if industry in inds and re.search(k, title)]
            if hit:
                why, strength = f"Title concerns {industry}", max(hit)
        if not why and sector and sector_ok:
            why, strength = f"Policy area: {b['policy_area']}", 1
        if why:
            out.append({"bill": b["id"], "title": b["title"], "role": role, "date": d.isoformat(),
                        "days_from_trade": (d - td).days, "why": why, "url": b["url"],
                        "status": b["latest_action"], "strength": strength})
    out.sort(key=lambda x: (-x["strength"], abs(x["days_from_trade"])))
    return out
