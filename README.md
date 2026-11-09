# StockExplorer · Market Observation Workbench

A local workbench for China's A-share market, with indices, a watchlist, active stocks, intraday and K-line charts, hot-topic discovery, backtest stock picking, company research, holdings tracking, and per-stock quick briefs.

## Getting started

```sh
python3 -m pip install -r requirements.txt
python3 app.py
```

Then open http://localhost:8080 in your browser. The service listens on localhost only.

## Automatic hot topics

While the service is running, it fetches Eastmoney concept sectors and Sina Finance rolling news in the background every 5 minutes. Topics are ranked by sector gain, turnover, and news mentions, excluding some index-membership, money-flow, and prior-day-performance labels. The ranking is meant for discovering active sectors; it does not represent discussion heat across the whole web.

The page shows the source, fetch time, and sector gains/losses for each item, and titles link to the original articles. Successful results are cached in `.cache/hot-news.json`; if an upstream source fails, the previous data is kept and marked as cached. Fetching stops when the service stops.

Market hours are shown in Beijing time and are judged by weekday and trading session only; public holidays are not taken into account. Index and stock quotes are labelled with their actual data time.

## Per-stock quick briefs and optional AI

Opening a stock automatically generates a market brief: price performance, 5/20-day moving averages, recent daily volume, and the day's price range. Missing data is stated explicitly, and only K-lines dated no later than the quote date are compared. Briefs are cached for 5 minutes.

By default, briefs are rule-generated and clearly labelled "Market brief". For a real AI commentary, copy `.env.example` to `.env`, fill in the key, API base URL (usually ending in `/v1`), and model name of an OpenAI-compatible service locally, then start the app through environment variables:

```sh
set -a
source .env
set +a
python3 app.py
```

The AI is called only when both a key and a model name are configured; a successful result is labelled "AI commentary" together with the model name. The model service receives the stock's public quotes and recent K-lines; your watchlist is never sent. If the service fails, the app falls back to the rule-generated brief automatically. Keys never reach the browser and must not be committed to version control.

All content is for research reference only and does not constitute investment advice.

## Company research

From a Shanghai or Shenzhen A-share's page, "Company research" shows the company's main business, industry, official website, and financial reports over time. Choose the last 30 days, the last 90 days, the last year, or a custom start and end date (at most 730 days per query). The timeline can be filtered between media reports and company announcements, and dates are computed in Beijing time.

- Company profiles and financial indicators come from Eastmoney, announcements and their original documents from CNInfo, media history from the Google News index, and Sina Finance supplements recent coverage. The index is not a complete news archive; at most 60 announcements are read per query, and a shorter date range can be used to find more. If one source fails, the page keeps the data from the others.
- Revenue, net profit attributable to shareholders, and net cash flow from operating activities are shown on the cumulative basis used in the reports. Among the reports obtainable, the page picks the latest period that had been disclosed on or before the cutoff date; if the platform only offers a version updated after the cutoff, the version date is labelled, and such figures are not suitable as strict historical backtest data.
- "Operations and events analysis" organises revenue, profit, cash flow, and follow-up checking points from the reports and event headlines, with source links. It runs locally by rules and needs no AI key.

## Backtest stock picking

The "Backtest stock picking" page tests trading rules on historical prices, then picks stocks from hot topics:

- Universe: by default the top 5 topics from the hot-topic radar, taking the 8 largest Shanghai/Shenzhen A-shares by market capitalisation from each topic. You can also type any topic (matched with the same logic as hot-topic discovery), or click "Backtest these stocks" in the hot-topic search results. The URLs `#picks` and `#picks/<topic>` open the page directly.
- Rules (switchable on the page; both are computed in one pass):
  - Dual moving average: buy when MA5 crosses above MA20 (golden cross), sell when it crosses below (death cross); only genuine crosses count.
  - Momentum trend: hold while the close is above its level 20 trading days ago and above the 20-day moving average; sell otherwise.
- Windows: the last six months, one year, or two years (120 / 250 / 500 trading days). Signals are generated after the close and executed at the next trading day's open, so no future data is used. Returns are computed proportionally and net of fees: 0.03% commission, 0.05% stamp duty (sell side only), and 0.001% transfer fee.
- A stock is picked when the strategy made money over the window, beat buy-and-hold, and its latest signal is still holding (including a fresh buy signal). Clicking a row in the table opens the stock's detail page.
- Data: forward-adjusted daily bars from Tencent Finance. If Tencent rate-limits, the app switches to unadjusted Sina bars and labels them on the page. K-lines are cached for 30 minutes and backtest results for 10 minutes; incomplete results are not cached.

Historical backtests do not represent future performance and are for research reference only.

## My holdings

"My holdings", above the backtest page, records stocks you bought yourself:

- Enter the stock (code or name) and the buy date; buy price and share count are optional. Without a buy price, the close on the buy date (or the most recent prior trading day) is used as the estimate.
- Records are stored locally in `data/holdings.json` (already in `.gitignore`). To remove a position after selling, delete its record. If the file is corrupted, the page reports an error instead of overwriting it.
- Advice follows the selected rule's latest signal: a held stock shows "keep holding" or "consider selling"; a ranked stock you do not hold shows "consider buying" or "stay on the sidelines", never "holding".
- Profit and loss use Sina's latest price against the buy price, excluding dividends and fees; for suspended stocks or before the open, the previous close is used.

## Market data and constituent sources

- Eastmoney list APIs use the main host `push2.eastmoney.com` (numbered nodes such as `82.push2` often drop the connection outright); a connection dropped by the server is retried once, while timeouts are not retried.
- Index constituents use the public sample files of the CSI and CNI indices. If a Shenzhen sample download fails, the app falls back to Sina's latest constituent pages, and such lists are labelled accordingly. The constituents panel shows the sample date, source, and number of stocks displayed, and supports sorting by price change and total market capitalisation.
- Topic search pages through the Eastmoney concept catalogue, supplemented by a public sector catalogue when the catalogue is incomplete; results show Shanghai and Shenzhen A-shares only. If Eastmoney news returns errors, the app tries Sina bulletins or web search, and the interface labels the fallback source.
- Intraday data prefers Eastmoney intraday data, falling back to its minute K-lines. Daily, weekly, and monthly K-lines are unadjusted Sina data, and volume is expressed in lots （手） consistently across the detail page and intraday views. Missing valuation or turnover figures on the detail page are shown as "—".

## Verification

```sh
python3 -m unittest discover -s tests -v
node --check static/js/main.js
```
