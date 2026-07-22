# GARCH Method

Skill from **video 1 of the Quant Series**: *I Re-Created A Nobel Prize Quant Trading Model With Claude Code.*

The model family is Engle's ARCH / Bollerslev's GARCH — the volatility machinery that won the 2003 Sveriges Riksbank Prize (the economics Nobel) and that real risk desks run daily. I'm the guy who turned it into a Claude Code skill.

**One sentence:** every other trading tool tries to answer *which way*. This one answers ***how much*** — and how much is the question that decides whether you survive.

---

## Install (the headline path — two commands)

In Claude Code:

```
/plugin marketplace add [YOUR-USERNAME]/garchmethod
/plugin install garch-method@garchmethod
```

Then just ask in plain English: *"what's the vol forecast on BTC-USD"*, *"how big should my SPY position be"*, *"test my strategy with and without vol targeting"* — Claude fires the skill automatically.

No API keys. No accounts. No sudo. Dependencies resolve on first run via `uv` (PEP 723 inline metadata) — nothing to pip-install yourself.

## What the skill does

It answers one question for **any asset**: how violent is this market, and how big should my position be?

- Fits **GARCH(1,1) walk-forward** — parameters re-estimated on an expanding window, every forecast made using only data that existed before that day. Zero lookahead.
- Produces a 1-day-ahead **volatility forecast** (daily + annualized)
- Classifies the **vol regime**: calm / normal / storm (percentile vs trailing year)
- Converts the forecast into a **position size**: `target_vol / forecast_vol`, capped [0.25x, 2.0x]
- Runs the **honest test** (`compare.py`): the same strategy signals sized two ways — fixed vs vol-targeted — with both equity curves and Sharpe / max drawdown / worst month side by side
- Ships with an **EMA 9/21 crossover demo** strategy; bring your own via a signals CSV (`date, signal in {-1,0,1}`)

It takes **either a ticker** (`--ticker BTC-USD`, fetched via `yfinance`) **or your own CSV** (`--csv my_prices.csv`, just a date + close column) — so it drops into whatever data pipeline you already run, on whatever asset you trade.

It's built to **compose**: use it as a sizing layer on a strategy you already have, a standalone risk throttle, or a comparison harness — without rewriting your strategy. See `skills/garch/SKILL.md` for the JSON contract and three worked composition patterns. It also composes cleanly with regime-direction skills (Markov-style bull/bear classifiers): theirs answers *which way*, this answers *how much*. Multiply the two.

**What it will never do:** predict direction. GARCH forecasts the *magnitude* of moves — the skill says this in its own output, every time.

## The on-camera build / zero-trust manual path

`garch-method.md` is the original one-shot onboarding prompt — the version built **live on camera**. Paste it into Claude Code (agent mode) and it builds the whole skill from scratch in front of you: detects your OS, installs `uv`, writes every file, runs the sanity check (a full fixed-vs-vol-targeted comparison on BTC as proof-of-life).

It's kept here as the **zero-trust path**: if you don't want to install a plugin from a marketplace, this builds the identical logic locally so you can read every line as it's written. Most people should use the two-command plugin install above — this is the transparent fallback and the on-camera artifact.

## Pine Script bonus — the Storm Gauge

`pine-script/storm-gauge.pine` — TradingView v5 indicator that puts the framework on a chart: vol bands on price (so you can *see* the clustering), storm tint, and a corner gauge showing market violence vs your limit, its 1-year percentile, the weather, and — if you enter — your size, in dollars.

Honest label, on the panel itself: the gauge uses *realized* vol (Pine-native math). The walk-forward GARCH *forecast* lives in the skill — the chart tells you how violent the market is; the skill forecasts tomorrow.

Open TradingView → Pine Editor → paste the `.pine` → Save → Add to Chart. **Use the daily chart.** Inputs: 365 periods/year for crypto, 252 for stocks.

## RSI mean-reversion — objective rule set + backtest

`rsi-mean-reversion.md` — a fully objective, zero-discretion RSI mean-reversion rule set for BTC/USD daily: exact entry (RSI-30 cross-down inside a bull regime), exit (RSI 50 target / 2.5·ATR stop / 10-bar time stop), stop logic, and 1%-risk ATR position sizing. Every rule reduces to arithmetic on closed daily bars.

Two runnable companions to the doc:

- `pine-script/rsi-mean-reversion.pine` — the same strategy as a TradingView **v6 strategy** (not just an indicator): $100k start, 0.1%/side commission, next-bar-open fills, one position at a time. Paste → Save → Add to Chart on a daily BTC/USD chart → read the Strategy Tester.
- `scripts/rsi_backtest.py` — the identical logic offline in pandas/numpy for CSV backtests, so you can verify the numbers without TradingView. `uv run scripts/rsi_backtest.py --csv btcusd_daily.csv` (add `--no-gate` for the classic RSI-30 variant, `--chart eq.png --trades trades.csv` for outputs).

## Credit

- **Model family:** Robert Engle (ARCH, Nobel 2003) and Tim Bollerslev (GARCH, 1986). Read the originals — the math is theirs.
- **Skill + installer + Pine:** Miles Deutscher.

## License

MIT.
