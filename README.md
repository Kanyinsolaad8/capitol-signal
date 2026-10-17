# Capitol Signal

A personal app that tracks US Congress members' stock trades from the official disclosure filings, checks each stock they buy against two questions, and runs a £500 paper-money test.

- **Why might they know something?** Several members bought it, a buyer sits on a committee that oversees that business, or a buyer backed a bill that targets the company or its industry.
- **What does the market data say?** Analyst consensus, upside to the average analyst target, expected earnings growth, and valuation against industry peers (Yahoo Finance).

## The £500 test

Paper money only. Nothing here connects to a broker or can place a real trade.

Three lines, each modelling the same £500 from the start date:

- **Filtered** (your version): buys a freshly filed stock only when Political link is 2+ of 3, analysts rate it Buy or Strong Buy, and the mean target is 15%+ above the price. Sells when a buyer files a sale, analysts cut to Hold or worse, upside falls under 5% or the target is reached, or it falls 15% (stop-loss). 5 slots of £100.
- **Plain copy** (Autopilot style): copies freshly filed buys, biggest reported amounts first, and sells only when a buyer files a sale. 5 slots of £100.
- **S&P 500**: the £500 in SPY on day one, left alone.

Fills use the latest close, fractional shares, and a 0.15% currency fee on every buy and sell.

## How it runs

A GitHub Actions job (`.github/workflows/daily.yml`) runs every weekday at 07:00 UTC (7am UK in winter, 8am in summer) and on Sundays for a summary. It:

1. reads new House and Senate filings, committee seats, bill sponsorships and Yahoo data (`pipeline/build.py`)
2. runs the £500 test (`pipeline/portfolio.py`) and saves it to `state/portfolio.json`
3. rebuilds the app and publishes it to GitHub Pages (`app/render.py`)
4. sends a phone alert through ntfy only when the test buys or sells, plus a Sunday summary (`pipeline/notify.py`). If a run fails, you get an alert saying so and the app keeps the last good data.

The ntfy channel name is stored as the repository secret `NTFY_TOPIC`. No passwords, API keys or personal data are in this code.

## Sources

House Clerk Periodic Transaction Reports · Senate eFD · unitedstates/congress-legislators · GovInfo bill status · Yahoo Finance (unofficial).

Research aid, not financial advice.
