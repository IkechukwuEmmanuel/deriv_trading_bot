"""
database.py — Professional Grade SQLite Manager
────────────────────────────────────────────────
Architecture Upgrades:
  1. WAL Mode: Enables concurrent reads/writes without locking.
  2. Tick Pruning: Automatically deletes old market ticks to prevent database bloat.
  3. Safe Transactions: Timeouts and error logging ensure DB stability.
"""

import json
import logging
from datetime import datetime, date, timedelta
import aiosqlite

from config import DB_PATH

log = logging.getLogger("database")

# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,
    market          TEXT    NOT NULL,
    contract_type   TEXT    NOT NULL,
    algorithm       TEXT    NOT NULL,
    signal          TEXT,
    stake           REAL    NOT NULL,
    entry_price     REAL,
    exit_price      REAL,
    pnl             REAL,
    result          TEXT,           -- WIN | LOSS | CANCELLED
    balance_after   REAL,
    contract_id     INTEGER,
    duration_ticks  INTEGER,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS ticks (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT    NOT NULL,
    market  TEXT    NOT NULL,
    price   REAL    NOT NULL,
    epoch   INTEGER
);

-- Index for faster tick history retrieval and pruning
CREATE INDEX IF NOT EXISTS idx_ticks_market_id ON ticks(market, id DESC);
CREATE INDEX IF NOT EXISTS idx_ticks_ts ON ticks(ts);

CREATE TABLE IF NOT EXISTS algo_stats (
    algorithm       TEXT PRIMARY KEY,
    total_trades    INTEGER DEFAULT 0,
    wins            INTEGER DEFAULT 0,
    losses          INTEGER DEFAULT 0,
    total_pnl       REAL    DEFAULT 0.0,
    best_streak     INTEGER DEFAULT 0,
    current_streak  INTEGER DEFAULT 0,
    last_updated    TEXT
);

CREATE TABLE IF NOT EXISTS daily_summary (
    trade_date      TEXT PRIMARY KEY,
    total_trades    INTEGER DEFAULT 0,
    wins            INTEGER DEFAULT 0,
    losses          INTEGER DEFAULT 0,
    total_pnl       REAL    DEFAULT 0.0,
    opening_balance REAL,
    closing_balance REAL
);

CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
"""


# ─────────────────────────────────────────────────────────────────────────────
# DB class
# ─────────────────────────────────────────────────────────────────────────────

class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._db: aiosqlite.Connection | None = None
        self._last_prune_time = None

    async def connect(self):
        # timeout=10.0 allows queries to wait up to 10s if the DB is briefly locked
        self._db = await aiosqlite.connect(self.path, timeout=10.0)
        self._db.row_factory = aiosqlite.Row
        
        # ── Professional DB Tuning ──
        # WAL mode allows simultaneous readers and writers (crucial for analytics.py)
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA synchronous=NORMAL;")
        await self._db.execute("PRAGMA cache_size=-64000;") # 64MB cache
        
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self):
        if self._db:
            await self._db.close()

    # ── Trades ────────────────────────────────────────────────────────────

    async def insert_trade(self, **kwargs) -> int:
        kwargs.setdefault("ts", datetime.utcnow().isoformat())
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join("?" for _ in kwargs)
        vals = list(kwargs.values())
        
        try:
            async with self._db.execute(
                f"INSERT INTO trades ({cols}) VALUES ({placeholders})", vals
            ) as cur:
                row_id = cur.lastrowid
            await self._db.commit()
            return row_id
        except aiosqlite.Error as e:
            log.error("Failed to insert trade: %s", e)
            raise

    async def update_trade(self, trade_id: int, **kwargs):
        sets = ", ".join(f"{k}=?" for k in kwargs)
        vals = list(kwargs.values()) + [trade_id]
        try:
            await self._db.execute(f"UPDATE trades SET {sets} WHERE id=?", vals)
            await self._db.commit()
        except aiosqlite.Error as e:
            log.error("Failed to update trade %s: %s", trade_id, e)

    async def get_trades(self, limit: int = 20, market: str = None,
                         contract_type: str = None) -> list[dict]:
        q = "SELECT * FROM trades WHERE 1=1"
        params = []
        if market:
            q += " AND market=?"; params.append(market)
        if contract_type:
            q += " AND contract_type=?"; params.append(contract_type)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        
        async with self._db.execute(q, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_today_trades(self) -> list[dict]:
        today = date.today().isoformat()
        async with self._db.execute(
            "SELECT * FROM trades WHERE ts LIKE ? ORDER BY id DESC",
            (f"{today}%",)
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_today_pnl(self) -> float:
        today = date.today().isoformat()
        async with self._db.execute(
            "SELECT COALESCE(SUM(pnl),0) FROM trades WHERE ts LIKE ? AND pnl IS NOT NULL",
            (f"{today}%",)
        ) as cur:
            row = await cur.fetchone()
        return float(row[0]) if row else 0.0

    async def get_trade_count_today(self) -> int:
        today = date.today().isoformat()
        async with self._db.execute(
            "SELECT COUNT(*) FROM trades WHERE ts LIKE ?", (f"{today}%",)
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    # ── Ticks ─────────────────────────────────────────────────────────────

    async def insert_tick(self, market: str, price: float, epoch: int):
        try:
            await self._db.execute(
                "INSERT INTO ticks (ts, market, price, epoch) VALUES (?,?,?,?)",
                (datetime.utcnow().isoformat(), market, price, epoch)
            )
            await self._db.commit()
            
            # Prune ticks periodically (roughly once per hour to save DB operations)
            now = datetime.utcnow()
            if not self._last_prune_time or (now - self._last_prune_time).total_seconds() > 3600:
                await self._prune_old_ticks()
                self._last_prune_time = now
                
        except aiosqlite.Error as e:
            log.error("Failed to insert tick: %s", e)

    async def _prune_old_ticks(self):
        """Deletes ticks older than 24 hours to prevent infinite database bloat."""
        cutoff_time = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        try:
            async with self._db.execute("DELETE FROM ticks WHERE ts < ?", (cutoff_time,)) as cur:
                deleted = cur.rowcount
            await self._db.commit()
            if deleted > 0:
                log.info("Pruned %s old ticks from database.", deleted)
        except aiosqlite.Error as e:
            log.error("Failed to prune old ticks: %s", e)

    async def get_tick_history(self, market: str, limit: int = 200) -> list[float]:
        async with self._db.execute(
            "SELECT price FROM ticks WHERE market=? ORDER BY id DESC LIMIT ?",
            (market, limit)
        ) as cur:
            rows = await cur.fetchall()
        return [r[0] for r in reversed(rows)]

    # ── Algorithm stats ───────────────────────────────────────────────────

    async def update_algo_stats(self, algorithm: str, won: bool, pnl: float):
        try:
            async with self._db.execute(
                "SELECT * FROM algo_stats WHERE algorithm=?", (algorithm,)
            ) as cur:
                row = await cur.fetchone()

            now = datetime.utcnow().isoformat()
            if row is None:
                streak = 1 if won else -1
                await self._db.execute(
                    """INSERT INTO algo_stats
                       (algorithm,total_trades,wins,losses,total_pnl,best_streak,current_streak,last_updated)
                       VALUES (?,1,?,?,?,?,?,?)""",
                    (algorithm, int(won), int(not won), pnl,
                     streak if won else 0, streak, now)
                )
            else:
                row = dict(row)
                prev_streak = row["current_streak"]
                if won:
                    new_streak = prev_streak + 1 if prev_streak > 0 else 1
                else:
                    new_streak = prev_streak - 1 if prev_streak < 0 else -1
                best = max(row["best_streak"], new_streak)
                await self._db.execute(
                    """UPDATE algo_stats SET
                       total_trades=total_trades+1,
                       wins=wins+?,
                       losses=losses+?,
                       total_pnl=total_pnl+?,
                       best_streak=?,
                       current_streak=?,
                       last_updated=?
                       WHERE algorithm=?""",
                    (int(won), int(not won), pnl, best, new_streak, now, algorithm)
                )
            await self._db.commit()
        except aiosqlite.Error as e:
            log.error("Failed to update algo stats for %s: %s", algorithm, e)

    async def get_algo_stats(self) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM algo_stats ORDER BY total_trades DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_best_algorithm(self, min_trades: int = 30) -> str | None:
        """Return algorithm with best win rate (min_trades threshold)."""
        async with self._db.execute(
            """SELECT algorithm,
                      CAST(wins AS REAL)/total_trades AS win_rate
               FROM algo_stats
               WHERE total_trades >= ?
               ORDER BY win_rate DESC
               LIMIT 1""",
            (min_trades,)
        ) as cur:
            row = await cur.fetchone()
        return row["algorithm"] if row else None

    # ── Settings ──────────────────────────────────────────────────────────

    async def set_setting(self, key: str, value):
        val = json.dumps(value)
        try:
            await self._db.execute(
                "INSERT INTO settings (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, val)
            )
            await self._db.commit()
        except aiosqlite.Error as e:
            log.error("Failed to save setting %s: %s", key, e)

    async def get_setting(self, key: str, default=None):
        async with self._db.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ) as cur:
            row = await cur.fetchone()
        return json.loads(row[0]) if row else default

    # ── Export ────────────────────────────────────────────────────────────

    async def export_csv(self, path: str = "trades_export.csv"):
        import csv
        trades = await self.get_trades(limit=10000)
        if not trades:
            return None
        
        try:
            with open(path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=trades[0].keys())
                writer.writeheader()
                writer.writerows(trades)
            return path
        except IOError as e:
            log.error("Failed to export CSV: %s", e)
            return None

    # ── Lifetime summary ──────────────────────────────────────────────────

    async def get_lifetime_summary(self) -> dict:
        async with self._db.execute(
            """SELECT
               COUNT(*)                                       AS total_trades,
               SUM(CASE WHEN result='WIN'  THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) AS losses,
               COALESCE(SUM(pnl),0)                           AS total_pnl,
               COALESCE(MIN(pnl),0)                           AS worst_trade,
               COALESCE(MAX(pnl),0)                           AS best_trade
            FROM trades WHERE pnl IS NOT NULL"""
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else {}

    async def get_daily_summary(self, limit: int = 30) -> list[dict]:
        async with self._db.execute(
            """SELECT substr(ts,1,10) AS day,
                       COUNT(*) AS trades,
                       SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) AS losses,
                       COALESCE(SUM(pnl),0) AS total_pnl
                FROM trades
                WHERE pnl IS NOT NULL
                GROUP BY day
                ORDER BY day DESC
                LIMIT ?""",
            (limit,)
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]