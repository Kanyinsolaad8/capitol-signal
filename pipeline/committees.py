"""Who sits on which committee (public unitedstates/congress-legislators dataset)."""
import re, unicodedata
import requests, yaml

SRC = "https://unitedstates.github.io/congress-legislators/"

# Which committees oversee which Yahoo sectors/industries. Matched on committee-name keywords.
# This is a transparent, editable rule set, not a claim that anyone acted on inside information.
SECTOR_COMMITTEES = {
    "Technology": ["Science, Space, and Technology", "Commerce, Science, and Transportation", "Energy and Commerce",
                   "Intelligence"],
    "Communication Services": ["Energy and Commerce", "Commerce, Science, and Transportation"],
    "Healthcare": ["Energy and Commerce", "Health, Education, Labor, and Pensions", "Ways and Means", "Finance"],
    "Financial Services": ["Financial Services", "Banking, Housing, and Urban Affairs"],
    "Energy": ["Energy and Commerce", "Energy and Natural Resources", "Natural Resources",
               "Environment and Public Works"],
    "Utilities": ["Energy and Commerce", "Energy and Natural Resources"],
    "Industrials": ["Transportation and Infrastructure", "Commerce, Science, and Transportation"],
    "Consumer Cyclical": ["Energy and Commerce", "Commerce, Science, and Transportation"],
    "Consumer Defensive": ["Agriculture", "Energy and Commerce"],
    "Real Estate": ["Financial Services", "Banking, Housing, and Urban Affairs"],
    "Basic Materials": ["Natural Resources", "Energy and Natural Resources"],
}
INDUSTRY_COMMITTEES = {  # narrower links that add to the sector list
    "Aerospace & Defense": ["Armed Services", "Appropriations", "Intelligence", "Homeland Security"],
    "Semiconductors": ["Armed Services", "Foreign Affairs", "Foreign Relations",
                       "Select Committee on the Strategic Competition"],
    "Semiconductor Equipment & Materials": ["Armed Services", "Foreign Affairs", "Foreign Relations",
                                            "Select Committee on the Strategic Competition"],
    "Software - Infrastructure": ["Homeland Security"],
    "Information Technology Services": ["Armed Services", "Homeland Security", "Oversight"],
    "Agricultural Inputs": ["Agriculture"],
    "Farm & Heavy Construction Machinery": ["Agriculture"],
    "Railroads": ["Transportation and Infrastructure"],
    "Airlines": ["Transportation and Infrastructure"],
    "Oil & Gas Midstream": ["Transportation and Infrastructure"],
}


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r",?\s+(jr|sr|ii|iii|iv)\.?$", "", s.strip())
    return re.sub(r"[^a-z ]", "", s).strip()


def load():
    get = lambda f: yaml.safe_load(requests.get(SRC + f, timeout=60).content.decode("utf-8"))
    people, membership, comms = get("legislators-current.yaml"), \
        get("committee-membership-current.yaml"), get("committees-current.yaml")
    names = {}
    for c in comms:
        names[c["thomas_id"]] = c["name"]
        for sc in c.get("subcommittees", []):
            names[c["thomas_id"] + sc["thomas_id"]] = f'{c["name"]}: {sc["name"]}'
    by_bio = {}
    for cid, members in membership.items():
        for m in members:
            by_bio.setdefault(m["bioguide"], []).append(
                {"committee": names.get(cid, cid), "role": m.get("title", "Member"), "sub": len(cid) > 4})
    roster = []
    for p in people:
        t = p["terms"][-1]
        roster.append({
            "bioguide": p["id"]["bioguide"],
            "first": _norm(p["name"]["first"]), "nick": _norm(p["name"].get("nickname", "")),
            "last": _norm(p["name"]["last"]), "official": p["name"].get("official_full"),
            "search_name": f'{p["name"].get("nickname") or p["name"]["first"]} {p["name"]["last"]}',
            "chamber": "House" if t["type"] == "rep" else "Senate",
            "state": t["state"], "party": t.get("party"),
            "committees": by_bio.get(p["id"]["bioguide"], []),
        })
    return roster


def match(roster, chamber, first, last, state=None):
    last_n, first_n = _norm(last), _norm(first)
    cands = [r for r in roster if r["chamber"] == chamber and
             (r["last"] == last_n or r["last"] in last_n.split() or last_n.endswith(r["last"])
              or last_n in r["last"].split())]
    if state:
        cands = [r for r in cands if r["state"] == state] or cands
    if len(cands) > 1:
        toks = set(first_n.split())
        cands = [r for r in cands if toks & {r["first"], r["nick"]}] or cands
    return cands[0] if len(cands) == 1 else None


def relevant(committees, sector, industry):
    keys = INDUSTRY_COMMITTEES.get(industry, []) + SECTOR_COMMITTEES.get(sector, [])
    # match on the full committee only; subcommittee names ("...Artificial Intelligence") cause false hits
    return sorted({c["committee"] for c in committees
                   if not c["sub"] and any(k.lower() in c["committee"].lower() for k in keys)})
