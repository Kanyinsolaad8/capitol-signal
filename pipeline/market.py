"""Yahoo Finance validation layer: analyst consensus, EPS, price moves, sector and peer comparison."""
import math, time, datetime as dt
import yfinance as yf

SECTOR_ETF = {"Technology": "XLK", "Healthcare": "XLV", "Financial Services": "XLF", "Energy": "XLE",
              "Industrials": "XLI", "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP",
              "Utilities": "XLU", "Basic Materials": "XLB", "Real Estate": "XLRE",
              "Communication Services": "XLC"}


def _num(v):
    try:
        v = float(v)
        return None if math.isnan(v) or math.isinf(v) else v
    except (TypeError, ValueError):
        return None


def fundamentals(ticker):
    try:
        i = yf.Ticker(ticker).info or {}
    except Exception:
        return None
    price = _num(i.get("currentPrice") or i.get("regularMarketPrice"))
    if not price:
        return None
    tgt = _num(i.get("targetMeanPrice"))
    return {
        "name": i.get("shortName") or i.get("longName") or ticker,
        "sector": i.get("sector"), "industry": i.get("industry"), "industry_key": i.get("industryKey"),
        "price": price, "target_mean": tgt, "target_high": _num(i.get("targetHighPrice")),
        "target_low": _num(i.get("targetLowPrice")),
        "upside_pct": round((tgt / price - 1) * 100, 1) if tgt else None,
        "consensus": i.get("recommendationKey"), "rec_mean": _num(i.get("recommendationMean")),
        "analysts": i.get("numberOfAnalystOpinions"),
        "eps_trailing": _num(i.get("trailingEps")), "eps_forward": _num(i.get("forwardEps")),
        "pe_trailing": _num(i.get("trailingPE")), "pe_forward": _num(i.get("forwardPE")),
        "market_cap": _num(i.get("marketCap")),
    }


def industry_peers(industry_key, exclude, n=4):
    try:
        top = yf.Industry(industry_key).top_companies
        return [s for s in top.index.tolist() if s != exclude][:n]
    except Exception:
        return []


def fetch_all(tickers, workers=None, retries=3):
    """Fundamentals for each ticker; misses (often Yahoo rate limits) are retried more gently."""
    from concurrent.futures import ThreadPoolExecutor
    import os
    workers = workers or (3 if os.environ.get("CI") else 6)  # gentler on shared CI IP addresses
    out = {}
    todo = list(tickers)
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(20 * attempt)
        with ThreadPoolExecutor(max(1, workers // (attempt + 1))) as ex:
            out.update(zip(todo, ex.map(fundamentals, todo)))
        todo = [t for t in todo if out.get(t) is None]
        if not todo:
            break
    return out


def closes(tickers, start):
    df = yf.download(sorted(set(tickers)), start=start, auto_adjust=True, progress=False, threads=True)["Close"]
    if df is None or df.empty:
        raise RuntimeError("Yahoo returned no price history")
    return df


def fx_gbpusd():
    """USD per 1 GBP (latest close)."""
    s = yf.download("GBPUSD=X", period="10d", auto_adjust=True, progress=False)["Close"].dropna()
    return float(s.iloc[-1].iloc[0] if hasattr(s.iloc[-1], "iloc") else s.iloc[-1])


def ret_since(close_df, ticker, date):
    """% move from the first close on/after 'date' to the latest close."""
    if ticker not in close_df:
        return None
    s = close_df[ticker].dropna()
    s_after = s[s.index >= str(date)]
    if s_after.empty or s.empty:
        return None
    return round((s.iloc[-1] / s_after.iloc[0] - 1) * 100, 1)
