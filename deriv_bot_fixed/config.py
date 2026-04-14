"""
config.py — Hardened Configuration Module
─────────────────────────────────────────
Upgrades:
  1. Safe Casting: Prevents silent type errors if .env values are malformed.
  2. Fail-Fast Validation: Ensures critical tokens exist before the bot boots.
  3. Pathlib Integration: Safely resolves the database path regardless of OS.
  4. Immutable Constants: Uses frozenset/types mapping for strict immutability.
"""

from types import MappingProxyType
import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# Initialize basic logger for config errors
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("config")

# Force reload of .env to ensure fresh variables
load_dotenv(override=True)

# ── Helper Functions ──


def _get_float(key: str, default: float) -> float:
    try:
        val = os.getenv(key)
        return float(val) if val is not None else default
    except ValueError:
        log.error(f"Config Error: '{key}' must be a valid number.")
        sys.exit(1)


def _get_int(key: str, default: int) -> int:
    try:
        val = os.getenv(key)
        return int(val) if val is not None else default
    except ValueError:
        log.error(f"Config Error: '{key}' must be a valid integer.")
        sys.exit(1)


# ── Deriv ──────────────────────────────────────────────────────────────────
DERIV_APP_ID = os.getenv("DERIV_APP_ID", "1089")

# Tokens
DERIV_PAT = os.getenv("DERIV_PAT", "")
DERIV_OAUTH_TOKEN = os.getenv("DERIV_OAUTH_TOKEN", "")
DERIV_ACCOUNT_ID = os.getenv("DERIV_ACCOUNT_ID", "")
DERIV_DEMO = os.getenv("DERIV_DEMO", "true").lower() == "true"

# Fallback mechanism: use PAT, if empty, try OAUTH
DERIV_AUTH_TOKEN = DERIV_PAT or DERIV_OAUTH_TOKEN

DERIV_REST_BASE = "https://api.derivws.com"
DERIV_OTP_ENDPOINT = f"{DERIV_REST_BASE}/trading/v1/options/accounts/{DERIV_ACCOUNT_ID}/otp"
DERIV_WS_PUBLIC = "wss://api.derivws.com/trading/v1/options/ws/public"

# ── Telegram ───────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = _get_int("TELEGRAM_CHAT_ID", 0)

# ── Risk Management Defaults ───────────────────────────────────────────────
DEFAULT_STAKE = _get_float("DEFAULT_STAKE", 1.0)
DAILY_TARGET = _get_float("DAILY_TARGET", 3.0)
DAILY_STOPLOSS = _get_float("DAILY_STOPLOSS", -1.0)
MAX_STAKE_PCT = _get_float("MAX_STAKE_PCT", 0.07)

# ── Trading Defaults ───────────────────────────────────────────────────────
# Updated to 1s index for Variance Arbitrage
DEFAULT_MARKET = os.getenv("DEFAULT_MARKET", "1HZ25V")
DEFAULT_CONTRACT = os.getenv("DEFAULT_CONTRACT", "ACCU")
ACCU_GROWTH_RATE = _get_float("ACCU_GROWTH_RATE", 0.01)
## ACCU_PROFIT_TARGET = _get_float("ACCU_PROFIT_TARGET", 0.20)

# ── Data / DB ──────────────────────────────────────────────────────────────
# Safely resolve the DB path relative to the root project directory
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "deriv_bot.db"))

TICK_BUFFER_SIZE = _get_int("TICK_BUFFER_SIZE", 500)
HISTORY_FETCH_COUNT = _get_int("HISTORY_FETCH_COUNT", 200)

# ── Markets & Contracts ────────────────────────────────────────────────────
# Upgraded to MappingProxies (read-only dictionaries) to prevent accidental mutation

MARKETS = MappingProxyType({
    "R_10":    "Volatility 10 Index", "R_25":    "Volatility 25 Index",
    "R_50":    "Volatility 50 Index", "R_75":    "Volatility 75 Index",
    "R_100":   "Volatility 100 Index", "1HZ10V":  "Volatility 10 (1s) Index",
    "1HZ25V":  "Volatility 25 (1s) Index", "1HZ50V":  "Volatility 50 (1s) Index",
    "1HZ75V":  "Volatility 75 (1s) Index", "1HZ100V": "Volatility 100 (1s) Index",
})

CONTRACT_TYPES = MappingProxyType({
    "ACCU": "Accumulators", "CALL": "Rise", "PUT": "Fall",
    "DIGITMATCH": "Digit Match", "DIGITDIFF": "Digit Differ",
    "DIGITEVEN": "Digit Even", "DIGITODD": "Digit Odd",
    "DIGITOVER": "Digit Over", "DIGITUNDER": "Digit Under",
    "TICKHIGH": "Tick High", "TICKLOW": "Tick Low",
    "MULTUP": "Multiplier Up", "MULTDOWN": "Multiplier Down",
})

# ── WebSocket Keepalive ────────────────────────────────────────────────────
WS_PING_INTERVAL = None
MANUAL_PING_SECONDS = 15

# ── Fail-Fast Validation Routine ───────────────────────────────────────────


def validate_config():
    """Validates that all critical infrastructure tokens are present before boot."""
    missing_criticals = []

    if not DERIV_AUTH_TOKEN:
        missing_criticals.append("DERIV_PAT or DERIV_OAUTH_TOKEN")

    if TELEGRAM_TOKEN and not TELEGRAM_CHAT_ID:
        log.warning(
            "TELEGRAM_TOKEN provided, but TELEGRAM_CHAT_ID is missing. Alerts will not send.")

    if missing_criticals:
        log.critical(
            f"FATAL: Missing critical environment variables: {', '.join(missing_criticals)}")
        log.critical(
            "Please check your .env file. Shutting down to prevent erratic behavior.")
        sys.exit(1)


# Run validation immediately upon import
validate_config()
