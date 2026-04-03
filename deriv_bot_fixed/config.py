import os
from dotenv import load_dotenv

load_dotenv()

# ── Deriv ──────────────────────────────────────────────────────────────────
DERIV_APP_ID        = os.getenv("DERIV_APP_ID", "1089")

# Tokens
DERIV_PAT           = os.getenv("DERIV_PAT", "")
DERIV_OAUTH_TOKEN   = os.getenv("DERIV_OAUTH_TOKEN", "")
DERIV_ACCOUNT_ID    = os.getenv("DERIV_ACCOUNT_ID", "")
DERIV_DEMO          = os.getenv("DERIV_DEMO", "true").lower() == "true"

DERIV_AUTH_TOKEN    = DERIV_PAT or DERIV_OAUTH_TOKEN

DERIV_REST_BASE     = "https://api.derivws.com"
DERIV_OTP_ENDPOINT  = f"{DERIV_REST_BASE}/trading/v1/options/accounts/{DERIV_ACCOUNT_ID}/otp"
DERIV_WS_PUBLIC     = "wss://api.derivws.com/trading/v1/options/ws/public"

# ── Telegram ───────────────────────────────────────────────────────────────
TELEGRAM_TOKEN      = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID    = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

# ── Risk management defaults ───────────────────────────────────────────────
DEFAULT_STAKE       = float(os.getenv("DEFAULT_STAKE", "1.0"))
DAILY_TARGET        = float(os.getenv("DAILY_TARGET", "3.0"))
# Set stoploss to exactly 1 dollar (expressed as a negative number for math)
DAILY_STOPLOSS      = float(os.getenv("DAILY_STOPLOSS", "-1.0"))  
MAX_STAKE_PCT       = float(os.getenv("MAX_STAKE_PCT", "0.07"))

# ── Trading defaults ───────────────────────────────────────────────────────
DEFAULT_MARKET      = os.getenv("DEFAULT_MARKET", "1HZ10V")
DEFAULT_CONTRACT    = os.getenv("DEFAULT_CONTRACT", "ACCU")
ACCU_GROWTH_RATE    = float(os.getenv("ACCU_GROWTH_RATE", "0.01"))
ACCU_PROFIT_TARGET  = float(os.getenv("ACCU_PROFIT_TARGET", "0.20"))

# ── Data / DB ──────────────────────────────────────────────────────────────
DB_PATH             = os.getenv("DB_PATH", "deriv_bot.db")
TICK_BUFFER_SIZE    = 500
HISTORY_FETCH_COUNT = 200

# ── Markets & Contracts ────────────────────────────────────────────────────
MARKETS = {
    "R_10":    "Volatility 10 Index", "R_25":    "Volatility 25 Index",
    "R_50":    "Volatility 50 Index", "R_75":    "Volatility 75 Index",
    "R_100":   "Volatility 100 Index", "1HZ10V":  "Volatility 10 (1s) Index",
    "1HZ25V":  "Volatility 25 (1s) Index", "1HZ50V":  "Volatility 50 (1s) Index",
    "1HZ75V":  "Volatility 75 (1s) Index", "1HZ100V": "Volatility 100 (1s) Index",
}

CONTRACT_TYPES = {
    "ACCU": "Accumulators", "CALL": "Rise", "PUT": "Fall",
    "DIGITMATCH": "Digit Match", "DIGITDIFF": "Digit Differ",
    "DIGITEVEN": "Digit Even", "DIGITODD": "Digit Odd",
    "DIGITOVER": "Digit Over", "DIGITUNDER": "Digit Under",
    "TICKHIGH": "Tick High", "TICKLOW": "Tick Low",
    "MULTUP": "Multiplier Up", "MULTDOWN": "Multiplier Down",
}

# ── WebSocket Keepalive ────────────────────────────────────────────────────
# We disable the built-in ping_interval in the library and handle it manually 
# in deriv.py to prevent random 1006 crashes.
WS_PING_INTERVAL = None 
MANUAL_PING_SECONDS = 15