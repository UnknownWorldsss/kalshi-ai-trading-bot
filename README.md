# 🤖 Kalshi AI Trading Bot

<div align="center">

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**An autonomous trading bot for [Kalshi](https://kalshi.com) prediction markets powered by a five-model AI ensemble.**

Five frontier LLMs debate every trade. The system only enters when they agree.

[Quick Start](#-quick-start) · [Features](#-features) · [How It Works](#-how-it-works) · [Configuration](#configuration-reference)

</div>

---

> ⚠️ **Disclaimer — This is experimental software for educational and research purposes only.** Trading involves substantial risk of loss. Only trade with capital you can afford to lose. Past performance does not guarantee future results. This software is not financial advice.

---

## 🚀 Quick Start

**Three steps to get running in paper-trading mode (no real money):**

```bash
# 1. Clone and set up
git clone https://github.com/UnknownWorldsss/kalshi-ai-trading-bot.git
cd kalshi-ai-trading-bot
python setup.py        # creates .venv, installs deps, checks config

# 2. Add your API keys
cp env.template .env   # then open .env and fill in your keys

# 3. Run in paper trading mode
python cli.py run --paper
```

**Required API Keys:**
- Kalshi API key → [kalshi.com/account/settings](https://kalshi.com/account/settings)
- OpenRouter key → [openrouter.ai](https://openrouter.ai/) (for Claude, GPT-4o, Gemini, DeepSeek)
- xAI key → [console.x.ai](https://console.x.ai/) (optional, can use OpenRouter for Grok too)

---

## ✅ Features

### Multi-Model AI Ensemble (5 Models)
- ✅ **Five frontier LLMs** collaborate on every decision
- ✅ **Role-based specialization** — each model plays a distinct analytical role
- ✅ **Consensus gating** — positions only entered when models agree
- ✅ **OpenRouter integration** — routes all models through single API

| Model | Provider | Role | Weight |
|---|---|---|---|
| Grok-3 | xAI / OpenRouter | Lead Forecaster | 30% |
| Claude 3.5 Sonnet | OpenRouter | News Analyst | 20% |
| GPT-4o | OpenRouter | Bull Researcher | 20% |
| Gemini 3 Pro | OpenRouter | Bear Researcher | 15% |
| DeepSeek R1 | OpenRouter | Risk Manager | 15% |

### Trading & Risk Management
- ✅ **Paper trading mode** — simulate without real money
- ✅ **Kelly Criterion sizing** — fractional Kelly for risk control
- ✅ **Daily loss limits** — automatic circuit breakers
- ✅ **SQLite telemetry** — every decision logged locally

---

## 🧠 How It Works

```
  INGEST    →    DECIDE (5-Model Ensemble)    →    EXECUTE    →    TRACK
 --------        ─────────────────────────        ---------        --------
                 ┌─────────────────────────┐
  Kalshi    ───► │  Grok-3  (Forecaster)   │
  REST API       ├─────────────────────────┤
                 │  Claude  (News Analyst) │
                 ├─────────────────────────┤
                 │  GPT-4o  (Bull Case)    │  ──►  Kalshi  ──►  P&L
                 ├─────────────────────────┤       Order        Tracking
                 │  Gemini  (Bear Case)    │
                 ├─────────────────────────┤
                 │  DeepSeek(Risk Manager) │
                 └─────────────────────────┘
                      Debate → Consensus
```

### Key Improvements in This Fork

1. **OpenRouter Integration** — All 5 models route through OpenRouter (fixes xAI 403 errors)
2. **Ensemble Mode Fixed** — `paper_trader.py` now properly uses `ModelRouter`
3. **Model Updates** — Updated to latest Gemini model (`gemini-3-pro-preview`)
4. **Debug Logging** — Added detailed logging for decision engine checks
5. **Relaxed Filters** — Configurable thresholds for testing

---

## 📦 Installation

```bash
git clone https://github.com/UnknownWorldsss/kalshi-ai-trading-bot.git
cd kalshi-ai-trading-bot
python setup.py
```

### Configuration

```bash
cp env.template .env   # fill in your keys
```

| Variable | Description |
|---|---|
| `KALSHI_API_KEY` | Your Kalshi API key ID |
| `KALSHI_PRIVATE_KEY` | Your Kalshi private key (place as `kalshi_private_key.pem`) |
| `OPENROUTER_API_KEY` | OpenRouter key (Claude, GPT-4o, Gemini, DeepSeek, Grok) |
| `XAI_API_KEY` | Optional: direct xAI key |

### Initialize Database

```bash
python -m src.utils.database
```

---

## 🖥️ Running

```bash
# Paper trading (recommended for testing)
python cli.py run --paper

# Live trading (real money)
python cli.py run --live

# Launch dashboard
python cli.py dashboard

# Check status
python cli.py status
```

### Paper Trading Only

```bash
# Scan markets and log signals
python paper_trader.py

# Continuous scanning every 15 minutes
python paper_trader.py --loop --interval 900

# View stats
python paper_trader.py --stats
```

---

## ⚙️ Configuration Reference

Key settings in `src/config/settings.py`:

```python
# AI Models (via OpenRouter)
primary_model = "x-ai/grok-3"
fallback_model = "anthropic/claude-3.5-sonnet"

# Trading thresholds
min_confidence_to_trade = 0.55    # Minimum ensemble confidence
min_volume_for_ai_analysis = 0    # Minimum market volume

# Budget & Risk
daily_ai_budget = 50.0            # Max daily API spend ($)
max_daily_loss_pct = 15.0         # Stop trading at this loss
```

---

## 🗂️ Project Structure

```
kalshi-ai-trading-bot/
├── paper_trader.py            # Paper trading entry point
├── cli.py                     # Unified CLI
├── src/
│   ├── agents/                # Multi-model ensemble
│   ├── clients/               # API clients (Kalshi, OpenRouter)
│   ├── config/                # Settings
│   ├── jobs/                  # Core pipeline
│   └── utils/                 # Database, logging
├── docs/                      # Documentation
└── logs/                      # Runtime logs
```

---

## 📊 Performance Tracking

All data stored in `trading_system.db` (SQLite):

```bash
# View recent analyses
sqlite3 trading_system.db "SELECT decision_action, confidence FROM market_analyses ORDER BY analysis_timestamp DESC LIMIT 10;"
```

---

## 🔧 Troubleshooting

**xAI 403 Error?**  
→ Use OpenRouter. Set `OPENROUTER_API_KEY` and models will route through there.

**Ensemble not working?**  
→ Check logs for `ensemble_enabled=True`. If false, `ModelRouter` isn't initialized.

**No BUY signals?**  
→ Lower `min_confidence_to_trade` or check `daily_ai_budget` isn't exceeded.

---

## 📄 License

MIT License — see [LICENSE](LICENSE)

Original work by [Ryan Frigo](https://github.com/ryanfrigo).  
This fork maintained by [UnknownWorldsss](https://github.com/UnknownWorldsss).

---

<div align="center">

Made with ❤️ for the Kalshi trading community

</div>
