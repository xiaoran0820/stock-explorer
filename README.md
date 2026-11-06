# StockExplorer · Market Observation Workbench

A local workbench for China's A-share market, with indices, a watchlist, active stocks, intraday and K-line charts, hot-topic discovery, and per-stock quick briefs.

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

## Market data and constituent sources

- Index constituents use the public sample files of the CSI and CNI indices. If a Shenzhen sample download fails, the app falls back to Sina's latest constituent pages, and such lists are labelled accordingly. The constituents panel shows the sample date, source, and number of stocks displayed, and supports sorting by price change and total market capitalisation.
- Topic search pages through the Eastmoney concept catalogue, supplemented by a public sector catalogue when the catalogue is incomplete; results show Shanghai and Shenzhen A-shares only. If Eastmoney news returns errors, the app tries Sina bulletins or web search, and the interface labels the fallback source.
- Intraday data prefers Eastmoney intraday data, falling back to its minute K-lines. Daily, weekly, and monthly K-lines are unadjusted Sina data, and volume is expressed in lots （手） consistently across the detail page and intraday views. Missing valuation or turnover figures on the detail page are shown as "—".

## Verification

```sh
python3 -m unittest discover -s tests -v
node --check static/js/main.js
```
