# Deriv Trading Bot

Multi-strategy Deriv bot with Telegram control, SQLite data logging,
and automatic algorithm selection.

## Setup (5 minutes)

### 1. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure credentials
```bash
cp .env.example .env
# Edit .env with your tokens (see below)
```

### 3. Get your Deriv credentials

**OAuth Token + Account ID:**
1. Go to https://app.deriv.com
2. Open DevTools → Network tab
3. Any API request will show `Authorization: Bearer ory_at_...` — copy that token
4. Your account ID is shown in the URL or account switcher (e.g. `DOT90004580`)

> Alternatively register an OAuth2 app at https://developers.deriv.com
> and implement the full OAuth2 PKCE flow to get a token programmatically.

### 4. Get your Telegram credentials

**Bot token:**
1. Open Telegram, message `@BotFather`
2. `/newbot` → follow prompts → copy the token

**Your chat ID:**
1. Message `@userinfobot` on Telegram
2. It will reply with your ID (a number like `123456789`)

### 5. Run
```bash
python main.py
```

Optional CLI mode (no Telegram buttons required if you prefer local control):
```bash
python main.py --cli
```

The bot will send you a startup message on Telegram when configured.
Use `/start` (or `start` in CLI) to begin trading (demo mode by default).

---

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Start trading |
| `/stop` | Stop trading (graceful) |
| `/pause` | Pause new entries |
| `/resume` | Resume after pause |
| `/status` | Live dashboard |
| `/balance` | Balance + daily P&L |
| `/history [n]` | Last N trades |
| `/performance` | Algorithm win rates |
| `/market SYM` | Switch market e.g. `/market 1HZ10V` |
| `/contract TYP` | Override contract e.g. `/contract ACCU` |
| `/auto` | Auto-select best contract |
| `/stake AMT` | Set stake e.g. `/stake 1` |
| `/setloss AMT` | Set daily stop-loss |
| `/settarget AMT` | Set daily target |
| `/export` | Download CSV trade log |
| `/help` | All commands |

---

## Markets

| Symbol | Name |
|--------|------|
| `1HZ10V` | Volatility 10 (1s) — **recommended for $5 account** |
| `1HZ25V` | Volatility 25 (1s) |
| `1HZ50V` | Volatility 50 (1s) |
| `R_10` | Volatility 10 (2s) |
| `R_25` | Volatility 25 (2s) |
| `R_50` | Volatility 50 (2s) |
| `R_75` | Volatility 75 |
| `R_100` | Volatility 100 |

---

## Algorithms

| Key | Contract | Logic |
|-----|----------|-------|
| `ACCU` | Accumulator | Spike exhaustion + ATR calm detector — **your proven edge** |
| `CALL_PUT` | Rise / Fall | EMA9/21 crossover + RSI filter |
| `DIGIT` | Digit Match/Differ | Last-digit frequency analysis over 100 ticks |
| `OVER_UNDER` | Digit Over/Under | Rolling digit distribution bias |
| `EVEN_ODD` | Digit Even/Odd | Even/odd rolling ratio |
| `TICK_HL` | Tick High/Low | Directional momentum streak classifier |
| `MULT` | Multipliers | Bollinger Band breakout |

The bot starts with Accumulator by default.
After 30+ trades, `/auto` lets it switch to whichever algo has the best win rate.

---

## Risk rules (built in)
- Daily stop-loss: bot halts when daily P&L hits `-$DAILY_STOPLOSS`
- Daily target: bot halts when daily P&L hits `+$DAILY_TARGET`
- Max stake: capped at 10% of account balance
- Emergency exit: Accumulator closes immediately on new spike detection

---

## File structure

```
deriv_bot/
├── main.py          # Orchestrator — start here
├── config.py        # All settings (reads from .env)
├── deriv.py         # Deriv WebSocket engine
├── strategies.py    # All trading algorithms
├── database.py      # SQLite async layer
├── telegram_bot.py  # Telegram control + push notifications
├── requirements.txt
├── .env.example     # Copy to .env and fill in
└── README.md
```

---

## Demo vs Real

Set `DERIV_DEMO=true` in `.env` to trade on a demo account.
Set `DERIV_DEMO=false` only after consistent demo results.
Recommended: run demo for at least 200 trades before going live.

## Telegram analytics commands

- `/analytics`: Summarizes the bot trading key metrics (total trades, win/loss, P&L, top strategies, recent daily results).
- `/gemini`: Returns a recommended Gemini prompt template for AI analysis based on this bot's trade metrics.

## Suggested Gemini model prompt design

Use the `/analytics` output (JSON or bot summary) as input to Gemini. Example:

```
You are an expert trading analytics assistant. Here is the bot performance data:
{...analytics JSON...}

Please return a JSON object with:
- summary: brief conclusions
- recommendations: parameter and strategy changes
- issues: risk events, drawdown, losing streaks
- next_steps: prioritized tests and data collection guidelines
```

Include explicit instructions:
1. Keep recommendations practical and conservative for Deriv 1s/2s indices.
2. Suggest minimum sample sizes for signficant improvement in win rate.
3. Highlight if Accumulator, digit, momentum, or multiplier strategies should be favoured.
