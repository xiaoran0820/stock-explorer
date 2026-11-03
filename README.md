# StockExplorer （股探）

An A-share market dashboard with a **hot-topic stock matching engine**: type in a trending topic (or pick one of the suggested hot-topic keywords), and the app matches it to related concept boards and A-share stocks, ranked by relevance.

Built as a personal learning project with the help of AI-assisted coding tools, to explore how software can make financial information easier to discover and understand.

## Features

- **Hot-topic matching** — enter a topic such as 人工智能 (AI), 固态电池 (solid-state batteries) or 低空经济 (low-altitude economy), or choose a suggested keyword, and get matched concept boards plus related stocks ranked by a relevance score
- **Market overview** — real-time quotes for 8 major indices （上证指数， 深证成指， 创业板指， 科创50, 沪深300, 中证500, etc.)
- **Stock search & watchlist** — search any A-share by name or 6-digit code and keep a personal watchlist
- **Charts** — intraday minute charts and multi-period K-lines (daily / weekly / monthly) with technical indicators: MA, MACD, RSI, KDJ, BOLL and volume averages, all calculated in the backend
- **Index constituents & popular stocks** — browse index constituent stocks and a most-active stocks ranking
- **News search** — finance news related to any keyword or topic

## Tech stack

- **Backend:** Python + Flask, with an in-memory cache layer
- **Frontend:** vanilla JavaScript + ECharts, custom CSS (light/dark themes)
- **Data sources:** public APIs from Sina Finance （行情 / K-line / intraday) and Eastmoney （概念板块 / constituents / news); optional DuckDuckGo web search

## Run it locally

```bash
pip install -r requirements.txt
python app.py
```

Then open http://localhost:8080 in your browser.

## Notes

- Market data comes from third-party public APIs and may be delayed or unavailable outside trading hours; figures are shown for learning purposes only.
- This project is for personal study of software development and financial data. It is **not** investment advice.
