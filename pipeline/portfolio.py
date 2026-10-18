"""The £500 paper test. Two strategies side by side, plus the S&P 500 as the yardstick.

  copy      Autopilot-style: copy freshly filed buys (biggest reported amounts first); sell only when a
            politician who bought it files a sale.
  filtered  Your version. Buy only when ALL of these hold:
              - Political link >= 2 of 3 (several members bought, committee link, bill link)
              - analysts rate it Buy or Strong Buy, and the mean target is >= 15% above the price
              - Market checks >= 4 of 7 (consensus, upside, EPS growth, cheaper than peers, analyst
                momentum, estimates rising, new US government contracts)
              - results are not due within 7 days
            Sell when a buyer files a sale, analysts cut to Hold or worse, upside to target falls under 5%
            (or the target is reached), analysts turn negative (2+ downgrades or target cuts in 30 days,
            more than upgrades), or the price falls 15% below entry (stop-loss).
  sp500     The same £500 put into the S&P 500 (SPY) on day one and left alone.

Paper money only: nothing here connects to a broker. Each strategy models the same £500 so you can see which
one you'd rather have put your real £500 into. Cash is held in pounds; every buy and sell converts at that
day's GBP/USD rate with a 0.15% currency fee (typical of UK app brokers). Fills use the latest closing price
and fractional shares. 5 slots: each new buy uses the free cash divided by the empty slots.

Usage: python portfolio.py daily | weekly      (writes state/portfolio.json and state/events.json)
"""
import json, sys, datetime as dt, pathlib, re
import market

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / "state" / "portfolio.json"
EVENTS = ROOT / "state" / "events.json"
DATA = ROOT / "data" / "data.json"
START_GBP, SLOTS, FX_FEE = 500.0, 5, 0.0015
STOP_LOSS, MIN_UPSIDE_EXIT = -0.15, 5.0
ENTRY = {"access": 2, "upside": 15.0, "consensus": ("buy", "strong_buy"), "data": 4, "earnings_days": 7}
RULES_VERSION = 2  # bump when the rules change: the test restarts so the comparison stays clean
FRESH_DAYS = 14  # a filing counts as new news if first seen by this test and filed in the last 14 days
FIRST_RUN_LOOKBACK_DAYS = 7


def _amount_floor(a):
    m = re.search(r"\$([\d,]+)", a or "")
    return int(m.group(1).replace(",", "")) if m else 0


def new_state(today, fx, spy_price):
    shares = START_GBP * fx * (1 - FX_FEE) / spy_price
    return {
        "start_date": today, "start_gbp": START_GBP, "fx_fee": FX_FEE, "slots": SLOTS, "last_run": None,
        "rules_version": RULES_VERSION,
        "rules": __doc__.split("Usage:")[0].strip(), "seen_sources": [],
        "strategies": {
            "copy": {"label": "Plain copy (Autopilot style)", "cash_gbp": START_GBP, "positions": {}, "ledger": []},
            "filtered": {"label": "Your filtered version", "cash_gbp": START_GBP, "positions": {}, "ledger": []},
            "sp500": {"label": "S&P 500 yardstick", "cash_gbp": 0.0,
                      "positions": {"SPY": {"shares": shares, "entry_price": spy_price, "price": spy_price,
                                            "entry_date": today, "cost_gbp": START_GBP, "buyers": [],
                                            "reason": "Day-one buy"}},
                      "ledger": [{"date": today, "action": "BUY", "ticker": "SPY", "gbp": START_GBP,
                                  "price": spy_price, "reason": "Day-one buy"}]},
        },
        "history": [],
    }


def value_gbp(strat, fx):
    return strat["cash_gbp"] + sum(p["shares"] * p["price"] for p in strat["positions"].values()) / fx


def sell(strat, ticker, price, fx, today, reason, events, name):
    p = strat["positions"].pop(ticker)
    proceeds = p["shares"] * price / fx * (1 - FX_FEE)
    strat["cash_gbp"] += proceeds
    pnl = proceeds / p["cost_gbp"] - 1
    strat["ledger"].append({"date": today, "action": "SELL", "ticker": ticker, "gbp": round(proceeds, 2),
                            "price": price, "pnl_pct": round(pnl * 100, 1), "reason": reason})
    events.append({"strategy": name, "action": "SELL", "ticker": ticker, "gbp": round(proceeds, 2),
                   "pnl_pct": round(pnl * 100, 1), "reason": reason})


def buy(strat, ticker, price, gbp, fx, today, reason, buyers, events, name, extra=None):
    strat["cash_gbp"] -= gbp
    strat["positions"][ticker] = {"shares": gbp * fx * (1 - FX_FEE) / price, "entry_price": price, "price": price,
                                  "entry_date": today, "cost_gbp": gbp, "buyers": buyers, "reason": reason,
                                  **(extra or {})}
    strat["ledger"].append({"date": today, "action": "BUY", "ticker": ticker, "gbp": round(gbp, 2),
                            "price": price, "reason": reason})
    events.append({"strategy": name, "action": "BUY", "ticker": ticker, "gbp": round(gbp, 2), "reason": reason})


def run_daily(state, data, today, fx):
    events = []
    seen = set(state.get("seen_sources", []))
    floor = (dt.date.fromisoformat(today) - dt.timedelta(
        days=FIRST_RUN_LOOKBACK_DAYS if not state["last_run"] else FRESH_DAYS)).isoformat()
    fresh = [t for t in data["trades"] if t["signal"] and t["source"] not in seen and floor <= t["filing_date"] <= today]
    cards = {c["ticker"]: c for c in data["tickers"]}

    held = {t for s in state["strategies"].values() for t in s["positions"]}
    fund = {c["ticker"]: c["fund"] for c in data["tickers"] if c.get("fund")}
    missing = sorted(t for t in held if t not in fund)
    fund.update({k: v for k, v in market.fetch_all(missing).items() if v})
    prices = {t: f["price"] for t, f in fund.items() if f and f.get("price")}

    def sold_by_buyer(ticker, pos):
        return [t for t in data["trades"] if t["ticker"] == ticker and t["signal"] and t["type"].startswith("Sell")
                and t["filing_date"] > pos["entry_date"] and (t.get("display") or t["member"]) in pos["buyers"]]

    def neg_analysts(ticker):
        an = ((cards.get(ticker) or {}).get("catalysts") or {}).get("analysts")
        if an and an["down"] >= 2 and an["down"] > an["up"]:
            return f"Analysts turning negative: {an['down']} downgrades or target cuts in 30 days"
        return None

    # ---- exits first, so freed cash can be reused today
    for name in ("copy", "filtered"):
        strat = state["strategies"][name]
        for tk, pos in list(strat["positions"].items()):
            f, price = fund.get(tk), prices.get(tk)
            if not price:
                continue  # no price today: hold and check again next run
            s = sold_by_buyer(tk, pos)
            reason = None
            if s:
                reason = f"{s[0].get('display') or s[0]['member']} filed a sale"
            elif name == "filtered":
                move = price / pos["entry_price"] - 1
                if move <= STOP_LOSS:
                    reason = f"Stop-loss: {move * 100:.0f}% from entry"
                elif f and f.get("consensus") and f["consensus"] not in ENTRY["consensus"]:
                    reason = f"Analysts cut to {f['consensus'].replace('_', ' ')}"
                elif neg_analysts(tk):
                    reason = neg_analysts(tk)
                elif f and f.get("upside_pct") is not None and f["upside_pct"] <= 0:
                    reason = "Reached the analyst target"
                elif f and f.get("upside_pct") is not None and f["upside_pct"] < MIN_UPSIDE_EXIT:
                    reason = f"Upside to target down to {f['upside_pct']:.1f}%"
            if reason:
                sell(strat, tk, price, fx, today, reason, events, name)

    # ---- entries from freshly seen buys
    fresh_sells = {(t.get("display") or t["member"], t["ticker"]): t["trade_date"]
                   for t in fresh if t["type"].startswith("Sell")}
    by_tk = {}
    for t in sorted((t for t in fresh if t["type"] == "Buy"), key=lambda t: -_amount_floor(t["amount"])):
        who = t.get("display") or t["member"]
        if fresh_sells.get((who, t["ticker"]), "") >= t["trade_date"]:
            continue  # bought and sold again in the same batch of filings: a round trip, not a position
        by_tk.setdefault(t["ticker"], []).append(t)

    def fill(name, candidates):
        strat = state["strategies"][name]
        for c in candidates:
            free = SLOTS - len(strat["positions"])
            if free <= 0:
                break
            tk = c["ticker"]
            if tk in strat["positions"] or not prices.get(tk):
                continue
            gbp = strat["cash_gbp"] / free
            if gbp < 5:  # not worth a trade
                break
            buy(strat, tk, prices[tk], gbp, fx, today, c["reason"], c["buyers"], events, name, c.get("extra"))

    copy_cands = [{"ticker": tk, "buyers": sorted({t.get("display") or t["member"] for t in ts}),
                   "reason": f"{ts[0].get('display') or ts[0]['member']} filed a buy ({ts[0]['amount']})"}
                  for tk, ts in sorted(by_tk.items(), key=lambda kv: (-_amount_floor(kv[1][0]["amount"]),
                                                                      kv[1][0]["filing_date"]))]
    filt = []
    for tk in by_tk:
        c, f = cards.get(tk), fund.get(tk)
        if not c or not f or "access_score" not in c:
            continue
        if (c["access_score"] >= ENTRY["access"] and f.get("consensus") in ENTRY["consensus"]
                and (f.get("upside_pct") if f.get("upside_pct") is not None else -99) >= ENTRY["upside"]
                and c.get("data_score", 0) >= ENTRY["data"] and not c.get("earnings_soon")):
            filt.append({"ticker": tk, "buyers": c["buyers"], "score": c["score"], "upside": f["upside_pct"],
                         "reason": f"Political link {c['access_score']}/{len(c['access'])}, data "
                                   f"{c['data_score']}/{len(c['checks'])}, {f['consensus'].replace('_', ' ')}, "
                                   f"{f['upside_pct']:.0f}% to target",
                         "extra": {"target_at_entry": f.get("target_mean")}})
    filt.sort(key=lambda c: (-c["score"], -c["upside"]))
    fill("copy", copy_cands)
    fill("filtered", filt)

    # everything filed inside the fresh window is now known, bought or not, so a later run never treats it as new
    window = (dt.date.fromisoformat(today) - dt.timedelta(days=FRESH_DAYS)).isoformat()
    state["seen_sources"] = sorted(seen | {t["source"] for t in data["trades"] if t["filing_date"] >= window})
    record(state, today, fx, prices)
    state["last_run"] = today
    return events


def record(state, today, fx, prices):
    for s in state["strategies"].values():  # mark to market; keep the last known price if Yahoo has none
        for tk, p in s["positions"].items():
            p["price"] = prices.get(tk, p.get("price", p["entry_price"]))
            hist = p.setdefault("history", [[p["entry_date"], p["entry_price"]]])
            if hist[-1][0] == today:
                hist[-1] = [today, p["price"]]
            else:
                hist.append([today, p["price"]])
            p["value_gbp"] = round(p["shares"] * p["price"] / fx, 2)
            p["move_pct"] = round((p["price"] / p["entry_price"] - 1) * 100, 1)
    row = {"date": today, **{name: round(value_gbp(s, fx), 2) for name, s in state["strategies"].items()}}
    state["history"] = [h for h in state["history"] if h["date"] != today] + [row]
    state["fx_gbpusd"] = fx


def main(mode):
    today = dt.date.today().isoformat()
    fx = market.fx_gbpusd()
    data = json.loads(DATA.read_text())
    state = json.loads(STATE.read_text()) if STATE.exists() else None
    archived = []
    if state is not None and state.get("rules_version") != RULES_VERSION:
        last = state["history"][-1] if state.get("history") else {}
        archived = state.get("archived", []) + [{"rules_version": state.get("rules_version", 1),
                                                 "start_date": state["start_date"], "end": last}]
        state = None  # rules changed: restart all three lines so the comparison stays clean
    if state is None:
        spy = market.fundamentals("SPY")
        if not spy:
            raise SystemExit("Could not price SPY to start the test; will try again next run")
        state = new_state(today, fx, spy["price"])
        state["archived"] = archived
    if mode == "daily":
        events = run_daily(state, data, today, fx)
    else:
        held = sorted({t for s in state["strategies"].values() for t in s["positions"]})
        f = market.fetch_all(held)
        record(state, today, fx, {t: v["price"] for t, v in f.items() if v})
        events = []
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1))
    EVENTS.write_text(json.dumps({"mode": mode, "date": today, "events": events,
                                  "latest": state["history"][-1], "start_gbp": state["start_gbp"],
                                  "start_date": state["start_date"]}, indent=1))
    print(json.dumps(events, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "daily")
