"""House of Representatives Periodic Transaction Reports (official Clerk source)."""
import io, re, zipfile, datetime as dt, xml.etree.ElementTree as ET
import requests, pdfplumber

BASE = "https://disclosures-clerk.house.gov/public_disc"
UA = {"User-Agent": "capitol-signal personal research tool"}

DATE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
OWNERS = {"SP": "Spouse", "JT": "Joint", "DC": "Dependent child"}


def list_ptrs(year: int, since: dt.date):
    z = requests.get(f"{BASE}/financial-pdfs/{year}FD.zip", headers=UA, timeout=60)
    if z.status_code == 404:  # early January: the new year's index isn't published yet
        return []
    z.raise_for_status()
    xml = zipfile.ZipFile(io.BytesIO(z.content)).read(f"{year}FD.xml")
    out = []
    for m in ET.fromstring(xml):
        if m.findtext("FilingType") != "P":
            continue
        doc = m.findtext("DocID")
        fdate = dt.datetime.strptime(m.findtext("FilingDate"), "%m/%d/%Y").date()
        if fdate < since:
            continue
        out.append({
            "doc_id": doc,
            "name": f"{m.findtext('First')} {m.findtext('Last')}".strip(),
            "first": m.findtext("First"), "last": m.findtext("Last"),
            "state_dst": m.findtext("StateDst"),
            "filing_date": fdate.isoformat(),
            "electronic": doc.startswith("2"),  # 9xxxxxx = scanned paper filing
            "url": f"{BASE}/ptr-pdfs/{year}/{doc}.pdf",
        })
    return out


def _columns(words):
    """Find column x-positions from the table header on the first page."""
    head = {}
    for w in words:  # first occurrence only; footer text repeats some words
        head.setdefault(w["text"], w)
    if "Transaction" not in head or "Amount" not in head:
        return None
    return {"owner": head.get("Owner", {"x0": 60})["x0"], "asset": head["Asset"]["x0"],
            "type": head["Transaction"]["x0"], "date": head["Transaction"]["x1"] + 2,
            "amount": head["Amount"]["x0"], "cap": head.get("Cap.", {"x0": 9e9})["x0"],
            "top": head["Transaction"]["top"]}


def _rows(words, col):
    """Return (orphan lines continuing the previous page's last row, [(anchor_line, row_lines)])."""
    words = [w for w in words if w["top"] > col["top"] + 15]
    lines = {}
    for w in words:
        lines.setdefault(round(w["top"]), []).append(w)
    tops = sorted(lines)
    anchors = [t for t in tops
               if any(abs(w["x0"] - col["type"]) < 6 and w["text"] in ("P", "S", "E") for w in lines[t])
               and sum(bool(DATE.match(w["text"])) for w in lines[t]) >= 2]

    def block(start, stop, include_first):
        """Row lines up to the first "Label : value" line; the rest (status, description) is the tail."""
        out, tail, ended = [], [], False
        for t2 in tops:
            if t2 < start or t2 >= stop:
                continue
            if not ended and (t2 > start or not include_first) and any(":" in w["text"] for w in lines[t2]):
                ended = True  # "Filing Status : New" / "Subholding Of :" lines end the row
            (tail if ended else out).append(lines[t2])
        tail_text = " ".join(w["text"] for ln in tail for w in ln)
        return out, tail_text

    orphans, orphan_tail = block(-1, anchors[0] if anchors else 9e9, False)
    rows = [(lines[t], *block(t, anchors[i + 1] if i + 1 < len(anchors) else 9e9, True))
            for i, t in enumerate(anchors)]
    return (orphans, orphan_tail), rows


def parse_ptr(filing: dict):
    r = requests.get(filing["url"], headers=UA, timeout=60)
    r.raise_for_status()
    rows, col = [], None
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            col = _columns(words) or (col and {**col, "top": -100})
            if not col:
                continue
            (orphans, orphan_tail), page_rows = _rows(words, col)
            if rows and (orphans or orphan_tail):  # row split across a page break
                f0, b0, t0, c0 = rows[-1]
                rows[-1] = (f0, b0 + orphans, t0 + " " + orphan_tail, c0)
            rows += [(first, block, tail, col) for first, block, tail in page_rows]
    trades = []
    for first, block, tail, col in rows:
        allw = [w for ln in block for w in ln]
        asset = " ".join(w["text"] for w in allw if col["asset"] - 3 <= w["x0"] < col["type"] - 3)
        amount = " ".join(w["text"] for w in allw if col["amount"] - 3 <= w["x0"] < col["cap"] - 3)
        owner = [w["text"] for w in first if col["owner"] - 3 <= w["x0"] < col["asset"] - 3]
        typ = " ".join(w["text"] for w in first if col["type"] - 3 <= w["x0"] < col["date"])
        dates = [w["text"] for w in first if DATE.match(w["text"])]
        tick = re.findall(r"\(([A-Z][A-Z0-9.\-]{0,6})\)", asset)
        atype = re.findall(r"\[([A-Z]{2})\]", asset)
        if not tick or not atype or atype[-1] not in ("ST", "OP"):
            continue
        tx = typ.replace(" ", "")
        trades.append({
            "chamber": "House",
            "member": filing["name"], "first": filing["first"], "last": filing["last"],
            "state": filing["state_dst"][:2], "district": filing["state_dst"],
            "owner": OWNERS.get(owner[0] if owner else "", "Self"),
            "ticker": tick[-1].replace(".", "-"),
            "asset": re.sub(r"\s*\[[A-Z]{2}\]", "", asset).strip(),
            "asset_type": "Stock" if atype[-1] == "ST" else "Option",
            "option_type": None if atype[-1] == "ST" else
                ("Put" if re.search(r"\bputs?\b", asset + " " + tail, re.I) else
                 "Call" if re.search(r"\bcalls?\b", asset + " " + tail, re.I) else "Option"),
            "status": "Deleted" if re.search(r"\bDeleted\b", tail) else "Amended" if re.search(r"\bAmended\b", tail) else "New",
            "amended": bool(re.search(r"\bAmended\b", tail)), "old_news": False,
            "type": {"P": "Buy", "S": "Sell", "S(partial)": "Sell (partial)", "E": "Exchange"}.get(tx, tx),
            "trade_date": dt.datetime.strptime(dates[0], "%m/%d/%Y").date().isoformat(),
            "filing_date": filing["filing_date"],
            "amount": amount,
            "source": filing["url"],
        })
    return trades
