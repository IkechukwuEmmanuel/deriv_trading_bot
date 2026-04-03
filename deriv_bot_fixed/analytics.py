#!/usr/bin/env python3
"""
analytics.py — Production Grade AI Analyzer
────────────────────────────────────────────
Architecture Upgrades:
  1. Native Gemini Integration: Uses official google-genai SDK.
  2. DB Concurrency Safe: Uses connection timeouts to avoid SQLite lock errors.
  3. Structured Prompting: Forces the LLM to return actionable quant analysis.
  4. Environment Security: Loads API keys securely from .env.
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

# ── 2. Secure Configuration ───────────────────────────────────────────────
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

def open_db(path):
    """
    Opens SQLite connection safely. 
    The timeout=10 prevents 'database is locked' errors if the main bot 
    is currently writing to the database.
    """
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn

# ── 3. Data Extraction ────────────────────────────────────────────────────
def fetch_metrics(conn) -> dict:
    """Extracts summary statistics, grouped by algo, market, and day."""
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

        # Algorithm Performance
        metrics['algo'] = [dict(row) for row in cur.execute("""
            SELECT contract_type AS algorithm,
            COUNT(*) AS total_trades,
            SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) AS losses,
            COALESCE(SUM(pnl),0) AS total_pnl,
            100.0*SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END)/COUNT(*) AS win_rate
            FROM trades WHERE result IN ('WIN','LOSS')
            GROUP BY contract_type ORDER BY win_rate DESC
        """).fetchall()]

        # Market Performance
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

# ── 4. AI Analysis Engine ─────────────────────────────────────────────────
def analyze_with_gemini(metrics: dict, output_file: str = None):
    """Sends the JSON metrics to Gemini for a structured quantitative analysis."""
    if not GEMINI_API_KEY:
        log.error("GEMINI_API_KEY not found in environment variables or .env file.")
        sys.exit(1)

    log.info("Initializing Gemini Client...")
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    # We use gemini-2.5-pro for complex data reasoning and analysis tasks
    model_id = 'gemini-2.5-pro'

    system_instruction = """
    You are an expert Quantitative Trading Analyst. 
    Your job is to analyze the provided JSON performance metrics from an automated trading bot operating on the Deriv platform.

    Please provide a concise, professional report formatted in Markdown.
    Your report must include:
    1. Executive Summary: A 2-3 sentence overview of overall profitability and health.
    2. Algorithm Analysis: Identify the best and worst performing strategies based on win rate and total PnL.
    3. Risk Assessment: Highlight any alarming drawdowns, worst trades, or concerning daily trends.
    4. Actionable Recommendations: Give 2-3 specific, data-driven recommendations on how to tune the bot (e.g., "Stop trading DIGITMATCH", "Increase stake on ACCU").
    """

    prompt = f"Analyze the following trading bot metrics:\n```json\n{json.dumps(metrics, indent=2)}\n```"

    log.info(f"Sending metrics to {model_id} for analysis...")
    
    try:
        response = client.models.generate_content(
            model=model_id,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.2, # Low temperature for analytical consistency
            ),
        )
        
        report = response.text
        
        print("\n" + "="*60)
        print("🤖 GEMINI TRADING ANALYSIS REPORT")
        print("="*60 + "\n")
        print(report)
        print("\n" + "="*60)

        if output_file:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(report)
            log.info(f"Analysis report saved to {output_file}")

    except Exception as e:
        log.error("Failed to generate analysis from Gemini: %s", e)


# ── 5. CLI Execution ──────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Export analytics JSON from deriv bot DB and analyze with Gemini")
    parser.add_argument("--db", default="deriv_bot.db", help="Path to SQLite DB")
    parser.add_argument("--out-json", help="Path to save raw JSON metrics")
    parser.add_argument("--out-report", help="Path to save the Markdown text report from Gemini")
    parser.add_argument("--analyze", action="store_true", help="Send data to Gemini for AI analysis")
    parser.add_argument("--dry", action="store_true", help="Print raw JSON summary to console and exit")
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
        # If no flags are provided, default to just printing the JSON
        print(json.dumps(metrics, indent=2))
        log.info("Run with --analyze to generate an AI report, or --help for options.")


if __name__ == '__main__':
    main()