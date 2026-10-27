"""Run the full pipeline: official filings -> committee and bill context -> Yahoo validation -> data/data.json.

Fails loudly (non-zero exit) when inputs look broken, so an unattended run never publishes a silently wrong page.
"""
import json, re, sys, time, statistics, datetime as dt, pathlib, collections
from concurrent.futures import ThreadPoolExecutor
import house, senate, committees, market, bills, press, catalysts

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 90
OUT = pathlib.Path(__file__).resolve().parent.parent / "data"
OUT.mkdir(exist_ok=True)
today = dt.date.today()
since = today - dt.timedelta(days=DAYS)
log = lambda *a: print(*a, file=sys.stderr, flush=True)


class PipelineError(RuntimeError):
    pass


def ingest():
    trades, skipped, failed, attempted = [], [], [], 0
    for year in sorted({since.year, today.year}):
        filings = house.list_ptrs(year, since)
        skipped += [{"chamber": "House", "member": f["name"], "filing_date": f["filing_date"], "source": f["url"]}
                    for f in filings if not f["electronic"]]
        elec = [f for f in filings if f["electronic"]]
        attempted += len(elec)
        log(f"House {year}: {len(elec)} electronic filings, {len(filings) - len(elec)} scanned (skipped)")
        with ThreadPoolExecutor(6) as ex:
            for f, res in zip(elec, ex.map(lambda f: _safe(house.parse_ptr, f), elec)):
                if res is None:
                    failed.append(f["url"])
                else:
                    trades += res
    s_trades, s_skipped, s_failed, s_attempted, status = ingest_senate()
    trades += s_trades
    skipped += s_skipped
    failed += s_failed
    attempted += s_attempted
    if not trades:
        raise PipelineError("No trades parsed from either chamber")
    if attempted and len(failed) / attempted > 0.25:
        raise PipelineError(f"{len(failed)} of {attempted} filings failed to parse")
    return trades, skipped, failed, {"senate": status}


SENATE_CACHE = OUT.parent / "state" / "senate-last.json"


def _senate_from_site():
    """First run with no saved copy: take the Senate rows from the app as last published, so a Senate outage
    doesn't wipe them. Only reads our own GitHub Pages site."""
    import os, requests
    url = os.environ.get("SITE_URL")
    if not url:
        return {}
    try:
        page = requests.get(url, timeout=60).text
        d = json.loads(re.search(r'<script type="application/json" id="data">(.*?)</script>', page, re.S).group(1))
    except Exception as e:
        log("Could not read the published app for saved Senate data:", repr(e))
        return {}
    keep = ("chamber", "member", "first", "last", "state", "district", "owner", "ticker", "asset", "asset_type",
            "option_type", "type", "trade_date", "filing_date", "amended", "old_news", "amendment", "amount",
            "source", "status")
    return {"date": d["generated"][:10],
            "trades": [{k: t.get(k) for k in keep} for t in d["trades"] if t["chamber"] == "Senate"],
            "skipped": [s for s in d.get("skipped_paper_filings", []) if s["chamber"] == "Senate"]}


def ingest_senate():
    """The Senate site sometimes refuses automated visitors (its firewall began blocking them in October 2026).
    When it does, the run carries on with House filings plus the last good Senate data, instead of failing,
    and the app says so. state/senate-last.json keeps that last good copy and the outage dates."""
    cache = json.loads(SENATE_CACHE.read_text()) if SENATE_CACHE.exists() else {}
    err = None
    if not cache:
        cache = _senate_from_site()
    for attempt in range(3):
        try:
            s = senate.session()
            filings = senate.list_ptrs(s, since)
            break
        except Exception as e:
            err = e
            log(f"Senate site unavailable (attempt {attempt + 1} of 3): {e!r}")
            time.sleep(15 * (attempt + 1))
    else:
        down_since = cache.get("down_since") or today.isoformat()
        cache.update({"down_since": down_since, "recovered": None})
        SENATE_CACHE.parent.mkdir(exist_ok=True)
        SENATE_CACHE.write_text(json.dumps(cache))
        old = [t for t in cache.get("trades", []) if t["filing_date"] >= since.isoformat()]
        old_skipped = [f for f in cache.get("skipped", []) if f["filing_date"] >= since.isoformat()]
        log(f"Senate: using {len(old)} trades saved on {cache.get('date')} (site down since {down_since})")
        return old, old_skipped, [], 0, {"ok": False, "last_good": cache.get("date"), "down_since": down_since,
                                         "error": type(err).__name__ + ": " + str(err)[:200]}
    skipped = [{"chamber": "Senate", "member": f["name"], "filing_date": f["filing_date"], "source": f["url"]}
               for f in filings if not f["electronic"]]
    elec = [f for f in filings if f["electronic"]]
    log(f"Senate: {len(elec)} electronic filings ({sum(f['amended'] for f in elec)} amendments)")
    trades, failed = [], []
    for f in elec:
        res = _safe(senate.parse_ptr, s, f)
        if res is None:
            failed.append(f["url"])
        else:
            trades += res
    was_down = cache.get("down_since") or cache.get("recovered") == today.isoformat()
    SENATE_CACHE.parent.mkdir(exist_ok=True)
    SENATE_CACHE.write_text(json.dumps({"date": today.isoformat(), "trades": trades, "skipped": skipped,
                                        "down_since": None, "recovered": today.isoformat() if was_down else None}))
    return trades, skipped, failed, len(elec), {"ok": True, "last_good": today.isoformat()}


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        log("  parse failed:", a[-1].get("url"), repr(e))
        return None


def reconcile(trades):
    """Apply corrections across filings.
    - "Deleted" rows retract a trade: drop it and every earlier copy of it.
    - The same trade reported in two filings (an original and its amendment) is kept once, from the
      latest filing. Rows inside one filing are never merged: they can be separate accounts.
    - Buys from a filing listing 50+ purchases are a portfolio set-up or managed account, not a
      considered bet: shown, not scored."""
    key = lambda t: (t["member"], t["ticker"], t["type"], t["trade_date"], t["amount"], t["owner"])
    deleted = {key(t) for t in trades if t.get("status") == "Deleted"}
    trades = [t for t in trades if key(t) not in deleted]
    # newest wins: later filing date, then a higher amendment number (House amended rows rank as 1)
    rank = lambda t: (t["filing_date"], t.get("amendment") or (1 if t.get("amended") else 0), t["source"])
    sources = collections.defaultdict(set)
    for t in trades:
        sources[key(t)].add(rank(t))
    keep_src = {k: max(v)[2] for k, v in sources.items()}
    before = len(trades)
    trades = [t for t in trades if keep_src[key(t)] == t["source"]]
    buys_per_filing = collections.Counter(t["source"] for t in trades if t["type"] == "Buy")
    for t in trades:
        t["bulk"] = buys_per_filing[t["source"]] >= 50
    log(f"Reconcile: {len(deleted)} retracted trades, {before - len(trades)} superseded duplicates removed, "
        f"{sum(t['bulk'] for t in trades)} rows from bulk filings")
    return trades


def _fund_fallback():
    """Most recent earlier Yahoo snapshot (up to 7 days old), used only for tickers Yahoo misses today."""
    for d in range(1, 8):
        p = OUT / f"fund-{today - dt.timedelta(days=d)}.json"
        if p.exists():
            return json.loads(p.read_text()), (today - dt.timedelta(days=d)).isoformat()
    return {}, None


def _median(xs):
    xs = [x for x in xs if x and x > 0]  # negative forward P/E means expected losses: not comparable
    return statistics.median(xs) if len(xs) >= 3 else None


def main():
    cache = OUT / f"raw-{today}-{DAYS}.json"
    if cache.exists():
        trades, skipped, failed, sources = json.loads(cache.read_text())
    else:
        trades, skipped, failed, sources = ingest()
        cache.write_text(json.dumps([trades, skipped, failed, sources]))
    log(f"{len(trades)} stock/option transactions parsed ({len(failed)} filings failed)")

    for t in trades:
        t["ticker"] = t["ticker"].replace("--", "").strip()
        amt = re.search(r"Over \$[\d,]+|\$[\d,]+(?:\s*-\s*\$[\d,]+)?", t["amount"] or "")
        t["amount"] = re.sub(r"\s*-\s*", " - ", amt.group(0)) if amt else t["amount"]
        t.setdefault("asset", None)
        t.setdefault("option_type", None)
        t.setdefault("amended", False)
        t.setdefault("old_news", False)
        t.setdefault("status", "Amended" if t["amended"] else "New")
        if t["chamber"] == "House" and t["amended"] and t["trade_date"] < since.isoformat():
            t["old_news"] = True  # an amended row about a trade from before the window
    trades = reconcile(trades)

    roster = committees.load()
    members = {m["bioguide"]: m for m in roster}
    for t in trades:
        m = committees.match(roster, t["chamber"], t["first"], t["last"], t["state"])
        t["bioguide"] = m and m["bioguide"]
        t["party"] = m and m["party"]
        t["state"] = t["state"] or (m and m["state"])
        t["display"] = (m and m["official"]) or t["member"]
        t["lag_days"] = (dt.date.fromisoformat(t["filing_date"]) - dt.date.fromisoformat(t["trade_date"])).days
        t["late"] = t["lag_days"] > 45 and not t["amended"]  # STOCK Act: disclose within 45 days
        # stock trades feed the scores; options, corrections to old reports and bulk filings are shown only
        t["signal"] = t["asset_type"] == "Stock" and not t["old_news"] and not t["bulk"]
        t["not_scored"] = (None if t["signal"] else "Option" if t["asset_type"] != "Stock" else
                           "Correction to an old report" if t["old_news"] else "Bulk filing (50+ buys)")

    tickers = sorted({t["ticker"] for t in trades})
    log(f"Fetching Yahoo fundamentals for {len(tickers)} tickers…")
    fcache = OUT / f"fund-{today}.json"
    fund = json.loads(fcache.read_text()) if fcache.exists() else {}
    fund.update(market.fetch_all([t for t in tickers if not fund.get(t)]))
    fallback, fallback_date = _fund_fallback()
    stale = [t for t in tickers if not fund.get(t) and fallback.get(t)]
    for t in stale:
        fund[t] = {**fallback[t], "stale_since": fallback_date}
    if stale:
        log(f"Yahoo missed {len(stale)} tickers today; using {fallback_date} data for them")
    coverage = sum(1 for t in tickers if fund.get(t)) / len(tickers)
    if coverage < 0.8:
        raise PipelineError(f"Yahoo returned data for only {coverage:.0%} of tickers (rate-limited?)")

    buy_tickers = {t["ticker"] for t in trades if t["type"] == "Buy" and t["signal"] and fund.get(t["ticker"])}
    peers = {}
    for tk in sorted(buy_tickers):
        if fund[tk].get("industry_key"):
            peers[tk] = market.industry_peers(fund[tk]["industry_key"], tk)
    extra = sorted({p for ps in peers.values() for p in ps} - {k for k, v in fund.items() if v})
    log(f"Fetching {len(extra)} peer tickers…")
    fund.update(market.fetch_all(extra))
    stale_set = set(stale)
    fcache.write_text(json.dumps({k: v for k, v in fund.items() if v and k not in stale_set}))  # today's data only

    log("Loading bill sponsorship records…")
    congresses = sorted({bills.congress_for(since), bills.congress_for(today)})
    bill_idx = bills.by_member(bills.load(congresses, cache_dir=OUT / "bills"))

    first_trade = min(t["trade_date"] for t in trades)
    ref = ["SPY"] + list(market.SECTOR_ETF.values())
    px = market.closes([t for t in tickers if fund.get(t)] + ref, first_trade)

    for t in trades:
        f = fund.get(t["ticker"]) or {}
        t["move_since_trade"] = market.ret_since(px, t["ticker"], t["trade_date"])
        t["spy_since_trade"] = market.ret_since(px, "SPY", t["trade_date"])
        m = members.get(t["bioguide"])
        t["relevant_committees"] = committees.relevant(m["committees"], f.get("sector"), f.get("industry")) if m else []
        t["bills"] = bills.links(bill_idx.get(t["bioguide"], []), t["trade_date"], f.get("sector"),
                                 f.get("industry"), f.get("name"))[:4]

    # ---- per-ticker view, built only from signal trades
    by_tk = collections.defaultdict(list)
    for t in trades:
        if t["signal"]:
            by_tk[t["ticker"]].append(t)
    cards = []
    for tk, ts in by_tk.items():
        f = fund.get(tk)
        buys = [t for t in ts if t["type"] == "Buy"]
        sells = [t for t in ts if t["type"].startswith("Sell")]
        buyers = sorted({t["display"] for t in buys})
        sellers = sorted({t["display"] for t in sells})
        etf = market.SECTOR_ETF.get((f or {}).get("sector"))
        first_buy = min((t["trade_date"] for t in buys), default=None)
        comm = sorted({c for t in buys for c in t["relevant_committees"]})
        bill_links = {}
        for t in buys:
            for b in t["bills"]:
                if b["strength"] >= 2:
                    bill_links.setdefault((t["display"], b["bill"]), {**b, "member": t["display"]})
        key = bills.company_key((f or {}).get("name")) or tk
        press_search = {t["display"]: press.news_url(members[t["bioguide"]]["search_name"], key)
                        for t in buys if t["bioguide"]}
        card = {
            "ticker": tk, "fund": f, "trades": len(ts), "buyers": buyers, "sellers": sellers,
            "buy_count": len(buys), "sell_count": len(sells),
            "first_buy": first_buy, "last_buy_filed": max((t["filing_date"] for t in buys), default=None),
            "last_trade": max(t["trade_date"] for t in ts),
            "committee_overlap": comm, "sector_etf": etf,
            "bill_links": sorted(bill_links.values(), key=lambda b: (-b["strength"], abs(b["days_from_trade"])))[:6],
            "press_search": press_search,
            "since_first_buy": market.ret_since(px, tk, first_buy) if first_buy else None,
            "spy_since_first_buy": market.ret_since(px, "SPY", first_buy) if first_buy else None,
            "etf_since_first_buy": market.ret_since(px, etf, first_buy) if first_buy and etf else None,
            "peers": [{"ticker": p, **{k: (fund.get(p) or {}).get(k) for k in
                       ("name", "price", "upside_pct", "consensus", "pe_forward", "analysts")}}
                      for p in peers.get(tk, []) if fund.get(p)],
        }
        cards.append(card)

    # ---- recent market and government news for every scored stock (cached for the day)
    ccache = OUT / f"catalysts-{today}.json"
    cat = json.loads(ccache.read_text()) if ccache.exists() else {}
    # funds and ETFs have no sector: government contracts don't apply to them
    todo = {c["ticker"]: (c["fund"]["name"] if c["fund"].get("sector") else None) for c in cards
            if c["fund"] and c["buy_count"] and c["ticker"] not in cat}
    log(f"Fetching analyst, estimate, contract and SEC news for {len(todo)} stocks…")
    cat.update(catalysts.gather_all(todo))
    ccache.write_text(json.dumps(cat))
    # a field Yahoo or USAspending missed today falls back to the most recent earlier day (up to 7 days)
    misses = 0
    for d in range(1, 8):
        prev = OUT / f"catalysts-{today - dt.timedelta(days=d)}.json"
        if not prev.exists():
            continue
        old = json.loads(prev.read_text())
        for tk, entry in cat.items():
            for k in ("analysts", "estimates", "contracts"):
                if entry.get(k) is None and (old.get(tk) or {}).get(k) is not None:
                    entry[k] = old[tk][k]
                    entry.setdefault("stale", []).append(k)
        break
    misses = sum(1 for e in cat.values() if e.get("analysts") is None or e.get("estimates") is None)
    log(f"Catalyst gaps after fallback: {misses} stocks missing analyst or estimate data")

    for card in cards:
        tk, f, buys = card["ticker"], card["fund"], card["buy_count"]
        comm, buyers = card["committee_overlap"], card["buyers"]
        if f and buys:
            c = cat.get(tk) or {}
            an, est, con = c.get("analysts"), c.get("estimates"), c.get("contracts")
            ed = c.get("earnings_date")
            card["catalysts"] = c
            card["earnings_date"] = ed
            card["earnings_soon"] = bool(ed and (dt.date.fromisoformat(ed) - today).days <= 7)
            med = _median(p["pe_forward"] for p in card["peers"])
            bl = card["bill_links"]
            access = [
                ("Cluster", len(buyers) >= 2, f"{len(buyers)} member(s) bought"),
                ("Committee link", bool(comm), ", ".join(comm) or "No buyer on an overseeing committee"),
                ("Bill link", bool(bl), f'{bl[0]["member"]} {bl[0]["role"].lower()}ed {bl[0]["bill"]} '
                 f'({bl[0]["why"].lower()})' if bl else "No backed bill touching this company or industry"),
            ]
            pe = f["pe_forward"]
            checks = [
                ("Analyst consensus", f["consensus"] in ("buy", "strong_buy"),
                 f'{(f["consensus"] or "n/a").replace("_", " ")} ({f["analysts"] or 0} analysts)'),
                ("Target upside ≥15%", (f["upside_pct"] if f["upside_pct"] is not None else -99) >= 15,
                 f'{f["upside_pct"]}% to mean target' if f["upside_pct"] is not None else "No target"),
                ("EPS growth expected", bool(f["eps_forward"] and f["eps_trailing"] and f["eps_forward"] > f["eps_trailing"]),
                 f'Fwd EPS {round(f["eps_forward"], 2) if f["eps_forward"] else "n/a"} vs trailing {f["eps_trailing"]}'),
                ("Cheaper than peers", bool(med and pe and 0 < pe < med),
                 f'Fwd P/E {round(pe, 1) if pe else "n/a"} vs peer median {round(med, 1)}' if med
                 else "Not enough comparable peers"),
                ("Analyst momentum", bool(an and an["up"] > an["down"]),
                 f'{an["up"]} upgrades or target rises vs {an["down"]} downgrades or cuts in 30 days' if an
                 else "No analyst data"),
                ("Estimates rising", bool(est and est["up"] > est["down"]),
                 f'{est["up"]} earnings estimates raised vs {est["down"]} cut in 30 days' if est
                 else "No estimate data"),
                ("Government contracts", bool(con and con["total"] >= 1_000_000),
                 f'${con["total"] / 1e6:,.1f}m in new US government contracts in 90 days ({con["count"]} actions)'
                 if con and con["total"] else "No new US government contract money found in 90 days"),
            ]
            card["access"] = [{"label": a, "pass": b, "detail": c} for a, b, c in access]
            card["checks"] = [{"label": a, "pass": b, "detail": c} for a, b, c in checks]
            card["access_score"] = sum(b for _, b, _ in access)
            card["data_score"] = sum(b for _, b, _ in checks)
            card["score"] = card["access_score"] + card["data_score"]
    cards.sort(key=lambda c: (-(c.get("score") or -1), -(c.get("access_score") or 0), -c["buy_count"], c["ticker"]))

    # ---- price lines for the app: last ~6 months of closes for the top stocks and the S&P 500
    recent = px.iloc[-130:]
    chart_dates = [d.strftime("%Y-%m-%d") for d in recent.index]
    series = lambda tk: [None if v != v else round(float(v), 2) for v in recent[tk]] if tk in recent else None
    for c in [c for c in cards if c.get("checks")][:80]:
        c["series"] = series(c["ticker"])

    data = {
        "chart_dates": chart_dates, "spy_series": series("SPY"),
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "window_days": DAYS, "since": since.isoformat(), "sources": sources,
        "trades": sorted(trades, key=lambda t: (t["filing_date"], t["trade_date"]), reverse=True),
        "tickers": cards, "skipped_paper_filings": skipped, "failed_filings": failed,
        "members": {b: {"name": m["official"], "party": m["party"], "state": m["state"],
                        "chamber": m["chamber"], "committees": m["committees"]}
                    for b, m in members.items() if b in {t["bioguide"] for t in trades}},
    }
    (OUT / "data.json").write_text(json.dumps(data, default=str))
    log(f"Wrote {OUT / 'data.json'}: {len(trades)} trades, {len(cards)} tickers, "
        f"{sum(1 for t in trades if not t['bioguide'])} unmatched member names, "
        f"{sum(not t['signal'] for t in trades)} shown but not scored")


if __name__ == "__main__":
    try:
        main()
    except PipelineError as e:
        log("PIPELINE FAILED:", e)
        sys.exit(2)
