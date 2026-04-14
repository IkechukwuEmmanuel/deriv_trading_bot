#!/usr/bin/env python3
"""
analytics.py — Production Grade AI Analyzer (Variance Arbitrage Edition)
────────────────────────────────────────────────────────────────────────
Architecture Upgrades:
  1. Runtime Env Resolution: Fetches API key at execution to prevent import-time pathing failures.
  2. Contextual System Prompt: LLM is now strictly instructed to analyze Accumulator Variance Arbitrage.
  3. DB Concurrency Safe: Uses connection timeouts to avoid SQLite lock errors.
  4. Native Gemini Integration: Uses official google-genai SDK.
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

# Ensure we use the official GenAI SDK
try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Error: The google-genai library is missing.")
    print("Please install it by running: pip install google-genai")
    sys.exit(1)

# ── 1. Professional Logging Setup ─────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("analytics")


def _get_api_key():
    """Safely resolves the API key at runtime regardless of execution context."""
    # Force resolve the .env file relative to where this script physically lives
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=env_path, override=True)
    return os.getenv("GEMINI_API_KEY")


def open_db(path):
    """
    Opens SQLite connection safely. 
    The timeout=10 prevents 'database is locked' errors if the main bot 
    is currently writing to the database.
    """
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn

# ── 2. Data Extraction ────────────────────────────────────────────────────


def fetch_metrics(conn) -> dict:
    """Extracts summary statistics, heavily focused on Accumulator PnL distributions."""
    cur = conn.cursor()
    metrics = {}

    try:
        # Overall Performance
        metrics['overall'] = dict(cur.execute("""
            SELECT COUNT(*) AS total_trades,
            SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) AS losses,
            COALESCE(SUM(pnl),0) AS total_pnl,
            AVG(pnl) AS avg_pnl,
            MAX(pnl) AS best_trade,
            MIN(pnl) AS worst_trade
            FROM trades WHERE pnl IS NOT NULL
        """).fetchone() or {})

        # Market Performance (To see if 1HZ25V is outperforming R_10)
        metrics['market'] = [dict(row) for row in cur.execute("""
            SELECT market, COUNT(*) AS total_trades, COALESCE(SUM(pnl),0) AS total_pnl,
            AVG(pnl) AS avg_pnl,
            100.0*SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END)/COUNT(*) AS win_rate
            FROM trades WHERE result IN ('WIN','LOSS')
            GROUP BY market ORDER BY total_pnl DESC
        """).fetchall()]

        # Daily Performance (Last 30 Days)
        metrics['daily'] = [dict(row) for row in cur.execute("""
            SELECT substr(ts,1,10) AS day, COUNT(*) AS trades,
            SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) AS losses,
            COALESCE(SUM(pnl),0) AS pnl
            FROM trades WHERE pnl IS NOT NULL
            GROUP BY day ORDER BY day DESC
            LIMIT 30
        """).fetchall()]

    except sqlite3.Error as e:
        log.error("Database query failed: %s", e)
        raise

    return metrics

# ── 3. AI Analysis Engine ─────────────────────────────────────────────────


def analyze_with_gemini(metrics: dict, output_file: str = None):
    """Sends the JSON metrics to Gemini for a structured quantitative analysis."""
    api_key = _get_api_key()

    if not api_key:
        log.error("GEMINI_API_KEY not found. Check your .env file.")
        # We don't sys.exit() here because this might be called async from Telegram
        raise ValueError("GEMINI_API_KEY is missing.")

    log.info("Initializing Gemini Client...")
    client = genai.Client(api_key=api_key)

    model_id = 'gemini-2.5-pro'

    # Updated System Instruction to reflect Systems Thinking and Variance Arbitrage
    system_instruction = """
    You are an expert Quantitative Risk Architect analyzing a high-frequency Accumulator trading bot operating on Deriv's Synthetic Indices. 
    The bot strictly uses a 'Variance Arbitrage' strategy: it enters during extreme micro-volatility squeezes and uses a live Z-score algorithm to execute emergency probability evasions before a barrier breach.

    Provide a professional, ruthlessly objective report formatted in Markdown. 
    You must include:
    1. System Health Summary: A concise overview of the bot's Expected Value (EV) and overall profitability.
    2. Edge Verification: Analyze the win/loss ratio against the average PnL. Determine if the probability evasion algorithm is successfully mitigating total wipeouts, or if the bot is bleeding capital through negative expected value.
    3. Market Regime Analysis: Evaluate which synthetic indices (markets) are providing the best statistical edge.
    4. Hard Architecture Recommendations: Provide 2 specific, mathematical recommendations to tune the bot (e.g., "Tighten the EWMV ratio entry parameter," "Increase the risk tolerance Z-score for evasion," "Switch to a 1-second index").
    """

    prompt = f"Analyze the following Variance Arbitrage metrics:\n```json\n{json.dumps(metrics, indent=2)}\n```"

    log.info(f"Sending metrics to {model_id} for analysis...")

    try:
        response = client.models.generate_content(
            model=model_id,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.1,  # Minimized temperature for strict analytical output
            ),
        )

        report = response.text

        print("\n" + "="*60)
        print("🤖 QUANTITATIVE ANALYSIS REPORT")
        print("="*60 + "\n")
        print(report)
        print("\n" + "="*60)

        if output_file:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(report)
            log.info(f"Analysis report saved to {output_file}")

    except Exception as e:
        log.error("Failed to generate analysis from Gemini: %s", e)
        raise

# ── 4. CLI Execution ──────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Export analytics JSON from deriv bot DB and analyze with Gemini")
    parser.add_argument("--db", default="deriv_bot.db",
                        help="Path to SQLite DB")
    parser.add_argument("--out-json", help="Path to save raw JSON metrics")
    parser.add_argument(
        "--out-report", help="Path to save the Markdown text report from Gemini")
    parser.add_argument("--analyze", action="store_true",
                        help="Send data to Gemini for AI analysis")
    parser.add_argument("--dry", action="store_true",
                        help="Print raw JSON summary to console and exit")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        log.error(f"Database not found: {db_path}")
        sys.exit(1)

    log.info(f"Extracting metrics from {db_path}...")
    conn = open_db(db_path)
    try:
        metrics = fetch_metrics(conn)
    finally:
        conn.close()

    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2)
        log.info(f"Wrote raw metrics JSON to {args.out_json}")

    if args.dry:
        print(json.dumps(metrics, indent=2))
        sys.exit(0)

    if args.analyze:
        analyze_with_gemini(metrics, args.out_report)
    elif not args.out_json:
        print(json.dumps(metrics, indent=2))
        log.info(
            "Run with --analyze to generate an AI report, or --help for options.")


if __name__ == '__main__':
    main()
