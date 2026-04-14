"""
main.py — Production Grade Orchestrator (Accumulator Strict Edition)
────────────────────────────────────────────────────────────────────
Architecture Upgrades:
  1. App/Orchestrator Pattern: Separates lifecycle from business logic.
  2. TradeController: Encapsulates all trading rules and state.
  3. Mutex Locking: asyncio.Lock prevents double-entry race conditions.
  4. Live Barrier Evasion: Exit logic moved to on_trade_update to process live Z-scores.
  5. Graceful Shutdown: Hooks into system signals to safely close DB/WS.
"""

import asyncio
import logging
import signal
import sys
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler

from config import (
    DEFAULT_MARKET, DEFAULT_CONTRACT, DEFAULT_STAKE,
    DAILY_TARGET, DAILY_STOPLOSS, #ACCU_PROFIT_TARGET,
    ACCU_GROWTH_RATE, DB_PATH
)
from database import Database
from deriv import DerivEngine
from strategies import get_entry_signal, check_exit_condition
from telegram_bot import TelegramController

# ── 1. Professional Logging Setup ─────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler("bot.log", maxBytes=5*1024*1024,
                            backupCount=5, encoding="utf-8")
    ]
)
log = logging.getLogger("main")


@dataclass
class BotState:
    market: str = DEFAULT_MARKET
    currency: str = "USD"  # Required for Telegram UI formatting
    contract_type: str = "ACCU"  # Exclusively set to ACCU
    stake: float = DEFAULT_STAKE
    daily_target: float = DAILY_TARGET
    daily_stoploss: float = DAILY_STOPLOSS
    auto_mode: bool = True
    trading: bool = False
    paused: bool = False
    open_contract_id: int | None = None
    open_trade_db_id: int | None = None
    open_pnl: float = 0.0
    open_entry_price: float = 0.0
    open_contract_type: str = ""
    engine: DerivEngine | None = None
    db: Database | None = None
    telegram: TelegramController | None = None


# ── 2. Trading Controller (Business Logic) ───────────────────────────────
class TradeController:
    """Encapsulates all decision making and trade execution logic."""

    def __init__(self, state: BotState):
        self.state = state
        # Async lock mathematically prevents double-entry race conditions
        self.trade_lock = asyncio.Lock()

    async def on_tick(self, market: str, price: float, epoch: int):
        s = self.state
        if market != s.market or not s.engine or not s.db:
            return

        ticks = s.engine.get_ticks(market)
        if len(ticks) < 30:
            return

        # ── Daily Limits ──
        daily_pnl = await s.db.get_today_pnl()

        if s.trading and daily_pnl >= s.daily_target:
            await self._halt_trading("🎯 <b>Daily target reached!</b>", daily_pnl)
            return

        if s.trading and daily_pnl <= s.daily_stoploss:
            await self._halt_trading("🛑 <b>Daily stop-loss hit!</b>", daily_pnl)
            return

        # If a contract is open, or trading is halted, skip signal generation
        if s.open_contract_id or not s.trading or s.paused:
            return

        # ── Signal Generation (Variance Arbitrage) ──
        signal = get_entry_signal(ticks, growth_rate=ACCU_GROWTH_RATE)

        if signal:
            # Safely acquire the lock before opening to prevent spamming
            if not self.trade_lock.locked():
                async with self.trade_lock:
                    # Double-check inside lock to ensure state didn't change
                    if not s.open_contract_id:
                        await self.open_trade(signal)

    async def _halt_trading(self, message: str, daily_pnl: float):
        self.state.trading = False
        await self.state.db.set_setting("trading", False)
        if self.state.telegram:
            await self.state.telegram.push(
                f"{message}\n"
                f"P&L: <code>${daily_pnl:.2f}</code>\n"
                f"Bot stopped for today. Use /start tomorrow."
            )

    async def open_trade(self, signal):
        s = self.state
        ct = signal.contract_type
        log.info("Opening trade: %s", signal)

        # Build proposal kwargs exclusively for Accumulators
        proposal_kwargs = {"growth_rate": signal.params.get(
            "growth_rate", ACCU_GROWTH_RATE)}

        # Register intent to DB
        ticks = s.engine.get_ticks(s.market)
        db_id = await s.db.insert_trade(
            market=s.market,
            contract_type=ct,
            algorithm=signal.reason[:80],
            signal=str(signal.direction),
            stake=s.stake,
            entry_price=ticks[-1] if ticks else 0,
        )

        s.open_trade_db_id = db_id
        s.open_contract_type = ct

        # Execute API Call
        try:
            buy = await s.engine.full_trade(s.market, ct, s.stake, **proposal_kwargs)
            s.open_contract_id = buy["contract_id"]
            s.open_entry_price = buy.get("buy_price", s.stake)
            log.info("Contract opened: %s", buy["contract_id"])

            if s.telegram:
                await s.telegram.push(
                    f"📥 <b>Trade opened</b>\n"
                    f"Type: <code>Accumulator</code> | Stake: <code>${s.stake:.2f}</code>\n"
                    f"Trigger: {signal.reason}"
                )
        except Exception as e:
            log.error("Trade open failed: %s", e)
            await s.db.update_trade(db_id, result="CANCELLED", notes=str(e))
            s.open_trade_db_id = None
            s.open_contract_id = None
            s.open_contract_type = ""
            if s.telegram:
                await s.telegram.push(f"⚠️ Trade failed to open: {e}")

    async def close_trade(self, reason: str = "MANUAL"):
        cid = self.state.open_contract_id
        if not cid:
            return

        # INSTANT STATE LOCK: Prevent network race conditions.
        # We wipe the ID from state BEFORE awaiting the API call.
        # This stops the next tick from triggering a duplicate sell.
        self.state.open_contract_id = None

        log.info("Closing contract %s — reason: %s", cid, reason)
        try:
            await self.state.engine.sell_contract(cid, price=0)
        except Exception as e:
            log.error("Sell failed: %s", e)

    async def on_trade_update(self, msg: dict):
        mtype = msg.get("msg_type")
        s = self.state

        if mtype == "proposal_open_contract":
            poc = msg.get("proposal_open_contract", {})
            if not poc:
                return

            pnl = float(poc.get("profit", 0))
            s.open_pnl = pnl

            # 1. Standard Target Exit
            #if s.open_contract_type == "ACCU" and pnl >= ACCU_PROFIT_TARGET and s.open_contract_id:
                #log.info("Target +$%.2f reached — selling.", pnl)
                #await self.close_trade(reason="TARGET_MET")
                #return

            # 2. Probability Evasion Exit (Zero-Latency Barrier Check)
            if s.open_contract_type == "ACCU" and s.open_contract_id and not poc.get("is_sold"):
                try:
                    current_spot = float(poc["current_spot"])
                    high_barrier = float(poc["high_barrier"])
                    low_barrier = float(poc["low_barrier"])
                    tick_count = int(poc.get("tick_count", 0))
                except (KeyError, TypeError):
                    pass  # Skip if payload doesn't have barriers yet
                else:
                    ticks = s.engine.get_ticks(s.market)
                    should_evade = check_exit_condition(
                        ticks, current_spot, high_barrier, low_barrier, tick_count
                    )

                    if should_evade:
                        log.warning(f"DANGER DETECTED: Variance Arbitrage triggered emergency exit!")
                        await self.close_trade(reason="PROBABILITY_EVASION")
                        return

            # 3. Contract Resolution
            if poc.get("is_sold") or poc.get("status") == "sold":
                result = "WIN" if pnl > 0 else "LOSS"
                bal = float(poc.get("balance_after", s.engine.balance))
                await self._finalize_trade(pnl, result, bal, poc.get("exit_tick_display_value"))

        elif mtype == "sell":
            sell = msg.get("sell", {})
            bal = float(sell.get("balance_after", s.engine.balance))
            pnl = float(sell.get("sold_for", 0)) - s.open_entry_price
            await self._finalize_trade(pnl, "WIN" if pnl > 0 else "LOSS", bal, None)

    async def _finalize_trade(self, pnl: float, result: str, bal: float, exit_price):
        s = self.state
        if s.open_trade_db_id:
            await s.db.update_trade(s.open_trade_db_id, pnl=pnl, result=result, exit_price=exit_price, balance_after=bal)
            await s.db.update_algo_stats(s.open_contract_type, won=(result == "WIN"), pnl=pnl)

        icon = "✅" if result == "WIN" else "❌"
        if s.telegram:
            await s.telegram.push(f"{icon} <b>Trade closed: {result}</b>\n"
                                  f"P&L: <code>{pnl:+.2f}</code> | Balance: <code>${bal:.2f}</code>")

        # Reset state securely
        s.open_contract_id = None
        s.open_trade_db_id = None
        s.open_pnl = 0.0
        s.open_contract_type = ""


# ── 3. Main Application Orchestrator ──────────────────────────────────────
class DerivBotApp:
    """Manages system lifecycle, DI (Dependency Injection), and graceful shutdown."""

    def __init__(self):
        self.db = Database(DB_PATH)
        self.state = BotState(db=self.db)
        self.controller = TradeController(self.state)
        self.engine = None
        self.tg = None
        self._shutdown_event = asyncio.Event()

    async def setup(self):
        await self.db.connect()
        log.info("Database connected.")

        # Load persisted settings
        self.state.market = await self.db.get_setting("market", DEFAULT_MARKET)
        self.state.stake = await self.db.get_setting("stake", DEFAULT_STAKE)
        self.state.daily_target = await self.db.get_setting("daily_target", DAILY_TARGET)
        self.state.daily_stoploss = await self.db.get_setting("daily_stoploss", DAILY_STOPLOSS)
        self.state.trading = await self.db.get_setting("trading", False)

        # Initialize Deriv Engine
        self.engine = DerivEngine(
            db=self.db,
            on_tick=self.controller.on_tick,
            on_trade_update=self.controller.on_trade_update,
            default_market=self.state.market,
        )
        self.state.engine = self.engine

        # Initialize Telegram
        try:
            self.tg = TelegramController(self.state)
            self.state.telegram = self.tg
        except ValueError as e:
            log.warning("Telegram offline: %s", e)

    async def _startup_notify(self):
        """Waits for engine to connect, then fires an init message."""
        connected = await self.engine.wait_connected(timeout=30)
        if self.tg and connected:
            await self.tg.push(
                "🤖 <b>Deriv Bot Online (Accumulator Strict)</b>\n"
                f"Market: <code>{self.state.market}</code>\n"
                f"Trading: {'ON ✅' if self.state.trading else 'OFF'}"
            )

    def trigger_shutdown(self, sig):
        """Catches system signals to flip the shutdown event gracefully."""
        log.warning(
            "Caught signal %s. Initiating graceful shutdown...", sig.name)
        self._shutdown_event.set()

    async def run(self):
        await self.setup()

        # Attach system signal handlers (Works on Linux/Mac/Docker)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.trigger_shutdown, sig)
            except NotImplementedError:
                pass  # Windows fallback (relies on KeyboardInterrupt)

        # Launch background tasks
        tasks = [
            asyncio.create_task(self.engine.run_forever()),
            asyncio.create_task(self._startup_notify())
        ]
        if self.tg:
            tasks.append(asyncio.create_task(self.tg.run()))

        log.info("Bot is active. Waiting for operations...")

        # Keep application alive until shutdown is triggered
        await self._shutdown_event.wait()

        await self.teardown(tasks)

    async def teardown(self, tasks):
        """Gracefully close all connections and cancel tasks."""
        log.info("Shutting down services...")
        self.state.trading = False  # Halt operations immediately

        if self.tg:
            await self.tg.push("🛑 Bot shutting down...")
            await self.tg.stop()

        if self.engine:
            await self.engine.disconnect()

        if self.db:
            await self.db.close()

        for t in tasks:
            t.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)
        log.info("Shutdown complete.")


if __name__ == "__main__":
    app = DerivBotApp()
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        pass  # Expected on Windows
    except Exception as e:
        log.critical("Application crashed: %s", e)