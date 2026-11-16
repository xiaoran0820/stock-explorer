# Stock Explorer

A local workbench for the China A-share market: indices, a watchlist, active stocks, intraday and candlestick charts, trending topics, backtest picks and quick reads on single stocks.

The repository also holds two offline research projects: price-action trading rules, described in [Research: price-action backtests](#research-price-action-backtests), and a portfolio of quiet stocks, described in [Research: quiet stocks and lottery stocks](#research-quiet-stocks-and-lottery-stocks). Both are separate from the website and add nothing to it.

## Getting started

```sh
python3 -m pip install -r requirements.txt
python3 app.py
```

Open http://localhost:8080 in a browser. The server listens on this computer only.

## Website features

### Trending topics

While the server runs, it fetches Eastmoney concept boards and the Sina Finance news feed every 5 minutes in the background. Topics are ranked by board gain, turnover and news mentions; some index-membership, fund-flow and previous-day-performance tags are left out. The list is for finding active sectors, not a measure of how much the whole internet is discussing them.

The page shows each source, the fetch time and the board's change, and headlines link to the original articles. Successful results are cached in `.cache/hot-news.json`; when a source fails, the old data stays and is marked as cached. Nothing is fetched after the server stops.

Market hours are shown in Beijing time and judged by weekday and trading hours only, without a public-holiday calendar. Index and stock quotes show the actual time of their data.

### Stock quick read and optional AI comment

Opening a stock produces a quick read: price performance, the 5- and 20-day moving averages, recent daily volume and the day's price range. Missing data is stated plainly, and only bars up to the quote date are compared. Quick reads are cached for 5 minutes.

By default the read is written by rules and labeled as such. For a real AI comment, copy `.env.example` to `.env`, fill in the key, base URL (usually ending in `/v1`) and model name of an OpenAI-compatible service, and start the server with those variables:

```sh
set -a
source .env
set +a
python3 app.py
```

The AI is called only when both the key and the model name are set, and a successful reply is labeled as an AI comment with the model name. The service receives that stock's public quote and recent bars, never the watchlist. If it fails, the page falls back to the rule-based read. The key never reaches the browser and should not be committed.

Everything on the site is for research only and is not investment advice.

### Company research

For a Shanghai or Shenzhen A-share, the COMPANY RESEARCH panel shows the main business, industry, website and historical financials. Choose the last 30 days, 90 days or year, or enter custom dates (up to 730 days at a time). The timeline can be filtered to media reports or company announcements, with dates in Beijing time.

- Company profiles and financial indicators come from Eastmoney, announcements and original filings from CNINFO, media history from the Google News index, and recent reports from Sina Finance. The index is not a complete news archive. Up to 60 announcements are read per request; narrow the dates to find more. If one source fails, the page keeps what the others returned.
- Revenue, net profit attributable to shareholders and net operating cash flow are shown as reported year-to-date figures. The page picks the latest report published on or before the end date. If a platform only offers a version revised after that date, the version date is shown, and such data is not fit for strict historical backtests.
- The operations and events analysis summarizes revenue, profit, cash flow and points to check next from the reports and event headlines, with source links. It runs locally by rules and needs no AI key.

### Backtest picks

The BACKTEST PICKS page tests trading rules on past prices, then picks stocks from trending topics:

- Universe: by default the top 5 trending topics and the 8 largest Shanghai and Shenzhen A-shares in each. You can also enter any topic (matched the same way as topic discovery) or start from a topic search result. The URLs `#picks` and `#picks/<topic>` open the page directly.
- Rules, switchable on the page (both are computed at once):
  - Moving-average cross: buy when MA5 crosses above MA20, sell when it crosses below. Only real crossings count.
  - Momentum: hold while the close is above the close 20 sessions earlier and above the 20-day average; otherwise sell.
- Windows: the last half year, year or two years (120, 250 or 500 sessions). Signals are taken after the close and filled at the next session's open, with no look-ahead. Returns are proportional and net of 0.03% commission, 0.05% stamp duty on sales and 0.001% transfer fee.
- A stock is picked when the rule made money in the window, beat buy-and-hold, and its latest signal is still to hold (a fresh buy counts). Click a stock in the table to open it.
- Prices are unadjusted Tencent daily bars (Sina bars when Tencent rate-limits), forward-adjusted by proportion with Sina's adjustment factors, so a percentage move includes dividends and bonus issues the way a holder earned them. Tencent's own forward adjustment is not used because it subtracts later cash dividends, which overstates the older moves of big dividend payers. If the factors cannot be fetched, a stock is computed on unadjusted prices and marked as such. Bars are cached for 30 minutes and results for 10 minutes; unadjusted or incomplete results are not cached.

Past backtests do not predict future returns.

### Dividend and low volatility

The BACKTEST PICKS page also shows a monthly DIVIDEND & LOW VOLATILITY candidate list. Opening a stock shows why it is or is not on the list, each condition of the 55-day breakout, and, when all conditions hold, the opening price range, reference stop and 1R target. The analysis uses closed daily bars adjusted the same way as the backtest picks. When the live bars or the adjustment factors are unavailable, it uses the local archive and shows that date, because unadjusted prices would misplace the averages, the 55-day high and the stop around each dividend. An entry plan is valid only until the open of the first session after the signal day and is marked expired once that open passes. Sessions are counted on weekdays without a holiday calendar, so around holidays a plan can expire early.

The monthly ranking uses the local price archive and implemented cash dividends. Yield is measured per share held on the ranking day: cash paid before a later bonus or capitalization issue is restated, so after a 1-for-10 issue an earlier dividend counts as its amount divided by 1.1. After updating the price archive and dividend records in `.cache/market-data`, run `python3 build_steady_snapshot.py` to build a new list (needs NumPy). The page picks up the new snapshot automatically and marks an outdated list as historical.

How the breakout rule did on past lists is covered in the research section below.

### My holdings

MY HOLDINGS, at the top of the BACKTEST PICKS page, records stocks you have bought:

- Enter the stock (code or name) and the purchase date; price and share count are optional. Without a price, the close on the purchase date is used, or on the last session before it.
- Records are stored locally in `data/holdings.json` (listed in `.gitignore`); delete a record after selling. If the file is damaged, the page reports an error and leaves the file untouched.
- Each record shows the selected rule's latest signal, computed on the same adjusted daily bars as the backtest: stocks you hold show "keep holding" or "sell signal"; ranked stocks you do not hold show "buy signal" or "wait", never "hold".
- Profit and loss compare the Sina live price with the purchase price, without dividends or fees; during a suspension or before the open, the previous close is used.

### Data sources

- Eastmoney list endpoints use the main host `push2.eastmoney.com`, because numbered nodes such as `82.push2` often drop connections. A dropped connection is retried once; a timeout is not.
- Index constituents come from the public files of China Securities Index or China National Index. If a Shenzhen file fails to download, the latest Sina constituent page is tried and partial lists are marked. The constituent panel shows the list date, source and count, and sorts by change or market value.
- Topic search pages through the Eastmoney concept directory and adds the public board directory when that is incomplete; results show Shanghai and Shenzhen A-shares only. When Eastmoney news fails, Sina flash news or web search is tried and the fallback is labeled.
- Backtests, holding signals and the dividend analysis use unadjusted daily bars from Tencent (or Sina, or Eastmoney for the dividend analysis) divided by Sina's forward-adjustment factors. Checked against the multiplicative factors of the research archive for ten stocks since 2018, including bonus-share issuers, the two agree within 0.3% on every session.
- Intraday charts use Eastmoney intraday data, falling back to its minute bars. Chart daily, weekly and monthly bars are unadjusted Sina data. Volume is shown in lots of 100 shares everywhere. Missing valuation or turnover figures are shown as a dash.

## Research: price-action backtests

*Offline research, not part of the website. The scripts read local data and write JSON reports; the website never runs them.*

Price-action traders describe setups by eye: a second entry after a pullback, a breakout to a new high. This project asked whether such setups still make money once they are written as exact rules and tested without hindsight on China's A-share market, and in particular whether they beat simply holding the same stocks. In these tests they did not.

### Data

- Daily prices for 5,809 Shanghai, Shenzhen and Beijing stocks from 2020-01-02 to 2026-11-06, including stocks that later delisted. A forced delisting is written off at zero. Sixteen companies instead left through a share-swap merger or a voluntary delisting with a cash option, which paid their holders; a holding in one of them is sold at its last traded close (they are listed in `PAID_OUT_DELISTINGS` in `market_pool_backtest.py`). A stock whose code changed keeps one price history; this covers the Beijing exchange's new codes and 300114, which became 302132 in February 2025. Prices are adjusted with multiplicative factors; price-limit checks use raw prices, which are the adjusted prices divided by the factor.
- 282,774 Chinese financial news articles from 2025-02-01 to 2026-09-23, used to rebuild which industries were in the news on each past day. Each day only sees articles published by its 15:00 close, Beijing time.
- Implemented cash dividends and bonus issues, for the dividend pool.
- Two fixed lists of large caps: 24 stocks from different industries, and 12 other stocks used only as a check. `build_price_snapshot.py` cuts both from the same price archive, together with the sessions on which each stock was at a price limit.

### Rules

Every rule decides at a close and trades in the next session. A position bought today cannot be sold today (A-share T+1). Costs are 0.031% to buy and 0.081% to sell (commission, transfer fee and stamp duty), and every test is repeated with 0.05% extra slippage on each side.

- **55-day breakout with a 1R target** (`market_pool_backtest.py`, `trend_pullback_backtest.py`): buy at the next open when the close is above its 100-day average, the average is higher than 20 sessions earlier, and the close is above the highest high of the previous 55 sessions. The stop is the lowest low of the last 10 sessions; the target is the entry price plus the initial risk (1R). The trade is skipped if the open is at or below the stop, or more than one signal-day risk above the signal close.
- **Brooks H2, the second entry** (`brooks_backtest.py`): in an uptrend of higher swing highs and lows confirmed by a strong breakout bar, wait for a pullback. After its first push up fails, buy with a stop order above the latest bar's high, so the order fills on the second push up. Exit either at the swing high before the pullback, or with a stop raised to each new confirmed higher low once price clears the high before it. A variant only takes signals from strong bull bars.
- **Pullback to the 20-day exponential average** within 30 sessions of a breakout, in the same uptrend (`trend_pullback_backtest.py`). Both trend entries are tested with three exits: the 1R target, half at 1R with the rest on a trailing stop, or a 20-day-low trailing stop alone.

The market-wide replay runs the breakout rule on three pools:

- **All stocks.**
- **Dividend low-volatility pool:** each month, stocks that paid cash dividends in each of the last three years and trade at least 30 million yuan a day (20-day median); from the higher-yield half, the 50 with the lowest one-year volatility.
- **News hot-topic pool:** each day, up to five industries whose share of the news over the last 5 days jumped against the 20 days before (at least 3 articles from at least 2 sources), and in each up to 10 stocks trading at least 30 million yuan a day with rising turnover. A keyword list maps headlines to 34 industries. The pool held about 43 stocks on average.

The replay holds at most 20 stocks, each bought with 1/20 of the portfolio. When more signals arrive than free slots, the most actively traded stocks go first. A stock that leaves its pool is not sold for that reason.

Each pool also gets three baselines that use no entry or exit rules:

- **hold the whole pool:** equal weight, rebalanced whenever the pool changes;
- **hold 20 names:** at most 20 pool members at 1/20 each, filled in the rule's turnover order and kept while they stay in the pool, so concentration matches the rule;
- **hold at the rule's exposure:** the whole pool with only the rule's average invested share in stocks and the rest in cash, so the cash cushion matches the rule.

### Fill assumptions

Daily bars do not show the order book, so every test runs under two sets of fill rules, defined in `execution.py`:

- **Ideal:** an order at the open fills at the open price on any day the stock traded at more than one price. A stop fills at the stop price, or at the open after a gap through it.
- **Conservative:** the ideal rules, plus no buy when a stock opens at its limit-up price and no sale on a day it closes at its limit-down price; a triggered stop then waits for the next open at which the stock can trade. In the market-wide replay an entry also may not exceed 5% of the stock's 20-day median turnover for a 1,000,000-yuan account. Limits are 10% on the main boards, 20% on ChiNext (from 2020-08-24) and STAR, and 30% on the Beijing exchange, checked on raw prices with one 0.01-yuan tick of tolerance. The fixed lists hold adjusted prices only, so `build_price_snapshot.py` runs the same check when it cuts them and stores the flagged sessions.

Both sets assume the stop came first when one bar touched both the stop and the target.

### Results

Return is the portfolio's total return over the period, max drawdown its largest fall from a peak, win rate the share of closed trades that made money, and "avg. invested" the average share of the portfolio held in stocks.

**Market-wide replay, 2020-01-02 to 2026-11-06**

| Pool and method | Return | Max drawdown | Closed trades | Win rate | Avg. invested | With 0.05% slippage | Conservative fills |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| All stocks, breakout rule | -74.78% | -80.62% | 734 | 46.46% | 89.7% | -75.66% | -78.84% |
| Dividend low-vol pool, breakout rule | +30.18% | -18.20% | 547 | 57.04% | 71.8% | +26.68% | +29.37% |
| Dividend low-vol pool, hold the whole pool | +86.85% | -15.63% | - | - | 99.9% | +83.54% | +87.32% |
| Dividend low-vol pool, hold 20 names, 1/20 each | +77.91% | -15.70% | - | - | 95.0% | +76.42% | +77.96% |
| Dividend low-vol pool, hold the pool, 72% invested | +59.61% | -11.21% | - | - | 71.9% | +57.63% | +59.76% |

**Common window with the news pool, 2025-02-27 to 2026-09-23**

The news archive starts on 2025-02-01. Its first 25 days only build the baseline for "hot", so the three pools are compared from 2025-02-27 to the last news day.

| Pool and method | Return | Max drawdown | Closed trades | Win rate | Avg. invested | With 0.05% slippage | Conservative fills |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| All stocks, breakout rule | -11.14% | -34.81% | 179 | 52.51% | 89.2% | -11.97% | -30.21% |
| Dividend low-vol pool, breakout rule | -3.87% | -9.39% | 139 | 48.20% | 56.8% | -4.58% | -3.87% |
| Dividend low-vol pool, hold the whole pool | +5.84% | -12.16% | - | - | 100.0% | +5.43% | +5.84% |
| Dividend low-vol pool, hold 20 names, 1/20 each | +6.17% | -13.62% | - | - | 98.3% | +5.89% | +6.17% |
| Dividend low-vol pool, hold the pool, 57% invested | +3.60% | -7.03% | - | - | 57.0% | +3.37% | +3.60% |
| News hot-topic pool, breakout rule | -15.80% | -37.80% | 150 | 48.00% | 84.1% | -16.46% | -19.93% |
| News hot-topic pool, hold the whole pool | -56.42% | -56.95% | - | - | 99.8% | -64.58% | -63.05% |
| News hot-topic pool, hold 20 names, 1/20 each | -65.73% | -67.33% | - | - | 99.5% | -71.91% | -71.76% |
| News hot-topic pool, hold the pool, 84% invested | -50.33% | -50.86% | - | - | 84.6% | -58.37% | -57.08% |

**Every signal on its own.** Taking every breakout signal in every stock independently, with no 20-stock limit (ideal fills, no slippage), gives 53,361 closed trades in 5,536 stocks: 49.76% won, the average trade returned -0.12%, and gross gains were 0.988 times gross losses. With conservative fills the average trade returned -0.24% (ratio 0.976).

**Fixed 24-stock list, 2020-01-02 to 2026-11-06**

Each stock trades in its own sleeve of 1/24 of the capital.

| Rule | Return | Max drawdown | Closed trades | Win rate | Avg. invested | With 0.05% slippage | Conservative fills |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Buy and hold the same stocks | +92.99% | -37.17% | - | - | 100.0% | +92.89% | +92.99% |
| H2, exit at the prior swing high | +0.67% | -3.35% | 187 | 44.39% | 2.5% | +0.01% | +0.67% |
| H2, trailing structure stop | -7.94% | -10.81% | 164 | 18.90% | 6.1% | -8.20% | -8.05% |
| H2 strong bars only, prior swing high | +0.05% | -2.16% | 54 | 46.30% | 0.7% | +0.10% | +0.05% |
| H2 strong bars only, trailing stop | -3.50% | -4.94% | 51 | 19.61% | 2.0% | -3.59% | -3.64% |
| 55-day breakout, 1R target | +19.52% | -24.00% | 314 | 57.32% | 30.8% | +17.89% | +19.94% |
| 55-day breakout, half at 1R, half trailed | +7.50% | -12.09% | 224 | 49.11% | 13.4% | +6.49% | +7.91% |
| 55-day breakout, trailing stop only | +5.72% | -18.94% | 219 | 42.47% | 17.7% | +4.75% | +5.97% |
| EMA pullback, 1R target | -1.96% | -13.49% | 469 | 48.61% | 8.6% | -3.82% | -1.96% |
| EMA pullback, half at 1R, half trailed | +3.06% | -14.97% | 343 | 45.48% | 10.8% | +1.62% | +3.06% |
| EMA pullback, trailing stop only | +7.35% | -21.51% | 303 | 28.05% | 17.3% | +6.04% | +7.12% |

Among the 164 H2 entries that closed under both exits, the trailing stop did better 14 times and returned 1.22 percentage points less per trade on average. Another 23 trades closed under the swing-high exit only, because the trailing version was still holding an earlier entry or was still open at the end.

**Check on 12 other large caps, same period**

| Rule | Return | Max drawdown | Closed trades | Win rate | With 0.05% slippage | Conservative fills |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Buy and hold the same stocks | +49.12% | -32.98% | - | - | +49.04% | +49.12% |
| 55-day breakout, 1R target | +2.93% | -13.38% | 155 | 50.97% | +1.56% | +3.21% |
| 55-day breakout, half at 1R, half trailed | +4.86% | -7.72% | 110 | 44.55% | +3.90% | +4.86% |
| 55-day breakout, trailing stop only | +1.51% | -12.14% | 107 | 35.51% | +0.64% | +1.51% |
| EMA pullback, 1R target | +0.92% | -7.10% | 243 | 49.79% | -1.11% | +0.63% |
| EMA pullback, half at 1R, half trailed | +0.25% | -8.81% | 176 | 47.16% | -1.19% | -0.04% |
| EMA pullback, trailing stop only | +4.69% | -14.36% | 149 | 27.52% | +3.41% | +4.43% |

### What the results show

1. **No timing rule beat holding the same stocks.** On the 24-stock list, holding returned +92.99% and the best rule +19.52%; on the 12-stock check, holding returned +49.12% against at most +4.86%.
2. **Choosing the pool mattered more than timing.** Held as a whole, the dividend low-volatility pool returned +86.85%. Trading it with the breakout rule returned +30.18%, below the 20-name baseline (+77.91%) and the baseline with the same 72% invested (+59.61%), and with a deeper drawdown than the latter (-18.20% against -11.21%). Holding fewer stocks or keeping cash does not explain the gap.
3. **A high win rate is not an edge.** The rule won 57.04% of its trades in the dividend pool and still trailed holding it; across all 53,361 signals the average trade lost money after costs.
4. **On the news pool the rule lost less than every baseline** (-15.80% against -56.42% for holding the pool). Its stops limited the damage in a pool that fell, while the baselines followed a list that changed almost daily and turned the portfolio over about 207 times in 19 months.
5. **The 24-stock winner did not hold up on a second sample.** The breakout with a 1R target returned +19.52% on the first list but +2.93% on the check list.
6. **Fill assumptions move a single 20-stock path more than expected.** In the common window the all-stock rule fell from -11.14% to -30.21% under conservative fills, although only one entry was refused for opening at limit-up (on 2025-04-08) and 11 exits were delayed by limit-down closes. The slots those changes freed and filled differently replaced 54 of the ideal run's 179 trades. The report lists these counts for every conservative run under `versus_ideal`.

### Limitations

- The baselines are not identical to the rule. Holding the whole pool means 50 stocks in the dividend pool against the rule's maximum of 20; the 20-name baseline matches concentration but stays almost fully invested; the exposure baseline matches the average cash share but not its timing. Each isolates one difference at a time.
- The news pool is an estimate rebuilt from news, not what investors actually watched: a keyword list maps headlines to industries, and "hot" means a jump in the share of articles. The archive covers about 19 months, so the news pool has a short test.
- Daily bars cannot show queue position, call auctions or intraday paths, so limit locks are approximated, and ST stocks (5% limit) are not identified. The conservative rules are a stress test rather than a guaranteed lower bound: a delayed trade can also land at a better price.
- Market impact is not modeled beyond the turnover cap, which never bound for a 1,000,000-yuan account.
- Many variants were tried (two H2 exits with and without a strong-bar filter, six trend variants, three pools, two fill sets and two slippage levels), so the best single number is biased upward.
- The two fixed lists are hand-picked large caps that still trade today, which favors survivors; the market-wide replay includes delisted stocks.
- A delisting that paid holders is valued at the last traded close. In a share-swap merger, holders actually received the acquirer's shares, whose price kept moving until they were delivered; a cash option usually paid slightly more than the last close.
- Win rate, average trade and the gains-to-losses ratio count closed trades only. Positions still open at the end appear in each report as unrealized return.
- Past results do not predict future returns, and none of this is investment advice.

**Data note.** An earlier version of the fixed lists used Tencent forward-adjusted prices, which subtract later cash dividends instead of scaling by them. That pushed the early prices of big dividend payers toward zero (China Shenhua closed at 0.18 yuan on 2020-04-13 in that data, against 9.47 when adjusted by multiplication) and inflated the 24-stock buy-and-hold return to +187%. The lists are now cut from the multiplicative price archive, and every number above uses the corrected data.

### Reproduce

The result files live in `.cache/`, which git ignores together with the source data (the price archive alone is 570 MB). With that data in `.cache/market-data` and NumPy installed:

```sh
python3 build_price_snapshot.py
python3 brooks_backtest.py > .cache/brooks-results.json
python3 trend_pullback_backtest.py > .cache/trend-pullback-results.json
python3 trend_pullback_backtest.py .cache/trend-validation-snapshot.json > .cache/trend-validation-results.json
python3 market_pool_backtest.py > .cache/market-pool-results.json
```

The market-wide replay takes under a minute on a laptop. The unit tests check the trading rules without any market data: next-session entries, T+1, gaps through the stop, a stop and a target hit on the same day, limit-up and limit-down days, suspensions, a portfolio that falls to zero, a delisting that paid its holders, a changed stock code, positions still open at the end, and equal budgets for the rule and its baselines.

## Research: quiet stocks and lottery stocks

*Offline research, not part of the website. It uses the price archive, period, costs and fill rules of the price-action research above.*

Research on China's A-share market has linked heavy speculative trading to weak later returns; for example, Liu, Stambaugh and Yuan ("Size and value in China", *Journal of Financial Economics*, 2019) add a factor built from abnormal turnover to their model of Chinese stock returns. This test asked whether the opposite end pays: a long-only portfolio of the quietest stocks, against simply holding the market. It did not. The quietest 50 stocks returned +37.09% against +70.21% for an equal-weight portfolio of all eligible stocks, with smaller swings, and the dividend low-volatility pool beat both. The loudest 50 stocks lost 90.59%.

### Rules

At each month-end close, a stock is eligible if it traded that day, has traded for at least a year (a traded close 250 or more sessions earlier, and at least 231 traded closes in the last 253 sessions), and trades at least 30 million yuan a day (20-day median). Eligible stocks are ranked three ways, lowest first:

- **Volatility:** the standard deviation of daily log returns over the last year.
- **Surge:** the average number of shares traded over the last 20 sessions against the last 250. Shares are estimated as turnover divided by the adjusted close, so a price rise alone does not count as heavier trading.
- **Spike:** the largest one-day gain over the last 20 sessions.

The three places are added. The **quiet** portfolio holds the 50 stocks with the lowest sum at equal weight; a holding stays while it ranks within the best 80, and free places go to the best-ranked stocks not held. The **loud** portfolio does the same from the other end of the ranking. Each list is set at the month-end close, bought at the next open and rebalanced to equal weight whenever it changes. The rules were fixed before the first run and not tuned afterwards.

Two baselines use the same dates and execution:

- **Equal-weight universe:** every eligible stock (3,725 on average), rebalanced monthly.
- **Dividend low-volatility pool:** the pool of the market-wide replay above, held as a whole.

The turnover cap of the market-wide replay is not applied. For a 1,000,000-yuan account a quiet-list position is about 20,000 yuan, under 0.1% of the 30 million yuan a day that every eligible stock trades.

### Results

Return is the total return over the period, annual return its yearly rate, and volatility the annualized standard deviation of daily portfolio returns.

**2020-01-02 to 2026-11-06**

| Portfolio | Return | Annual return | Max drawdown | Volatility | With 0.05% slippage | Conservative fills |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Quiet 50 | +37.09% | +4.71% | -23.49% | 16.55% | +30.31% | +38.28% |
| Equal-weight universe | +70.21% | +8.07% | -37.47% | 23.54% | +68.52% | +68.69% |
| Dividend low-vol pool | +86.85% | +9.56% | -15.63% | 14.01% | +83.54% | +87.32% |
| Loud 50 | -90.59% | -29.18% | -91.88% | 41.43% | -91.11% | -90.71% |

**Calendar years and halves, ideal fills**

| Period | Quiet 50 | Equal-weight universe | Dividend low-vol pool | Loud 50 |
| --- | ---: | ---: | ---: | ---: |
| 2020 | +0.38% | +19.34% | -1.65% | -32.25% |
| 2021 | +13.42% | +23.27% | +12.41% | -22.76% |
| 2022 | -8.06% | -13.89% | +4.67% | -41.43% |
| 2023 | +0.05% | +2.28% | +16.20% | -33.86% |
| 2024 | +21.13% | +4.35% | +30.13% | -33.49% |
| 2025 | +8.57% | +36.99% | +1.40% | +8.01% |
| 2026 (to 2026-11-06) | -0.47% | -8.09% | +5.32% | -35.42% |
| 2020-01-02 to 2023-12-29 | +4.74% | +29.56% | +34.47% | -79.73% |
| 2024-01-02 to 2026-11-06 | +30.89% | +31.38% | +38.96% | -53.61% |

### What the results show

1. **Avoiding lottery stocks mattered; buying the quietest did not add return.** The loud 50 lost 90.59% and fell in six of the seven calendar years. The quiet 50 returned +37.09%, about half of the equal-weight universe's +70.21%, and trailed it in both halves.
2. **The quiet portfolio is defensive.** It fell less in 2022 (-8.06% against -13.89%) and gained more in 2024 (+21.13% against +4.35%), but trailed in rising years such as 2025 (+8.57% against +36.99%). Its lower volatility did not make up for the lower return: annual return per unit of volatility was 0.28, against 0.34 for the universe.
3. **The dividend low-volatility pool beat it on every measure:** a higher return, a smaller drawdown and lower volatility (+86.85%, -15.63% and 14.01%). Both lists favor calm stocks; the dividend pool also requires a high yield, which works as a valuation screen and is a plausible source of the difference.
4. **The buffer did not stop the churn.** The 20-session measures change a lot from month to month, so the quiet list replaced about 30 of its 50 names each month, and 0.05% slippage per side cut its return from +37.09% to +30.31%. Conservative fills moved the result by about one point.

### Limitations

- One specification, tested once. A wider buffer would trade less, but choosing it after seeing these results would bias the comparison.
- Share counts are estimated from turnover and price; turnover against the free float is not available.
- The equal-weight universe gives a small company the same weight as a large one; it is not a market-capitalization index.
- ST stocks (5% limit) are not identified, as in the price-action research, and the loud list held some of them.
- Past results do not predict future returns, and none of this is investment advice.

### Reproduce

With the data in `.cache/market-data` and NumPy installed:

```sh
python3 quiet_stock_backtest.py > .cache/quiet-stock-results.json
```

The run takes under a minute. Its unit tests cover the three measures, the ranking, the buffer, month-end lists that never see later prices, and the yearly figures.

## Tests

```sh
python3 -m unittest discover -s tests -v
node --check static/js/main.js
```
