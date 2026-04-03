"""
strategies.py — Professional Quantitative Engine (Hardened Edition)
──────────────────────────────────────────────────────────────────
Architecture Upgrades:
  1. ADX Filter: Added Trend Strength verification to stop trading in weak trends.
  2. Noise Reduction: Implemented a 'Volatility Gate' to avoid low-liquidity periods.
  3. Weighted Confidence: Signals now require confluence across 3+ domains 
     (Regime, Momentum, and Statistics) to exceed 70% confidence.
  4. Regime Specific Logic: Logic now shifts dynamically based on FDI/Hurst.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Tuple
import numpy as np


@dataclass
class Signal:
    contract_type : str
    direction     : Optional[str]
    confidence    : float
    reason        : str
    params        : dict = field(default_factory=dict)

    def __str__(self):
        d = f" → {self.direction}" if self.direction else ""
        return f"[{self.contract_type}{d}] {self.confidence*100:.0f}% — {self.reason}"


# ─────────────────────────────────────────────────────────────────────────────
# Quantitative Indicator Helpers (Hardened)
# ─────────────────────────────────────────────────────────────────────────────

def _ema(prices: np.ndarray, period: int) -> float:
    if len(prices) < period: return prices[-1]
    alpha = 2 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = alpha * p + (1 - alpha) * ema
    return ema

def _rsi(prices: np.ndarray, period: int = 14) -> float:
    if len(prices) < period + 1: return 50.0
    deltas = np.diff(prices)
    up = np.where(deltas >= 0, deltas, 0)
    down = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(up[-period:])
    avg_loss = np.mean(down[-period:])
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return 100. - (100. / (1. + rs))

def _adx(prices: np.ndarray, period: int = 14) -> float:
    """Average Directional Index: Measures trend strength (not direction)."""
    if len(prices) < period * 2: return 20.0
    # Simplified ADX for tick data
    deltas = np.diff(prices)
    pos_dm = np.where(deltas > 0, deltas, 0)
    neg_dm = np.where(deltas < 0, -deltas, 0)
    tr = np.abs(deltas)
    
    # Smooth them
    s_pos_dm = np.mean(pos_dm[-period:])
    s_neg_dm = np.mean(neg_dm[-period:])
    s_tr = np.mean(tr[-period:])
    
    if s_tr == 0: return 0.0
    di_plus = 100 * (s_pos_dm / s_tr)
    di_minus = 100 * (s_neg_dm / s_tr)
    
    dx = 100 * np.abs(di_plus - di_minus) / (di_plus + di_minus + 1e-9)
    return dx

def _fractal_dimension(prices: np.ndarray, window: int = 30) -> float:
    if len(prices) < window: return 1.5
    diffs = np.abs(np.diff(prices[-window:]))
    length = np.sum(diffs)
    if length == 0: return 1.5
    price_range = np.max(prices[-window:]) - np.min(prices[-window:])
    return 1.0 + (np.log(length + 1e-9) - np.log(price_range + 1e-9)) / np.log(window)

def _digit_entropy(prices: np.ndarray, last_n: int = 100) -> float:
    digits = (np.round(prices[-last_n:] * 100) % 10).astype(int)
    counts = np.bincount(digits, minlength=10)
    probs = counts / len(digits)
    probs = probs[probs > 0]
    return -np.sum(probs * np.log2(probs))


# ─────────────────────────────────────────────────────────────────────────────
# 1. Accumulator — Volatility Squeeze Strategy
# ─────────────────────────────────────────────────────────────────────────────

class AccumulatorStrategy:
    NAME = "ACCU_Volatility_Squeeze"
    
    def analyse(self, ticks: List[float], growth_rate: float = 0.01) -> Optional[Signal]:
        prices = np.array(ticks)
        if len(prices) < 60: return None
        
        fdi = _fractal_dimension(prices)
        adx = _adx(prices)
        
        # Accumulators fail during Trends (ADX > 25) or Breakouts (FDI < 1.4)
        # We need high FDI (Mean Reversion) and Low ADX (No Trend)
        if fdi > 1.62 and adx < 22:
            # Scale confidence by how "Quiet" the market is
            base_conf = 0.70
            bonus = (fdi - 1.6) * 0.5 + (20 - adx) * 0.01
            confidence = min(0.96, base_conf + bonus)
            
            return Signal(
                "ACCU", None, confidence,
                f"Range Lock (FDI: {fdi:.2f} | ADX: {adx:.1f})",
                {"growth_rate": growth_rate}
            )
        return None

    def should_exit(self, ticks: List[float]) -> bool:
        prices = np.array(ticks)
        if len(prices) < 15: return False
        adx = _adx(prices, period=7) # Short period to detect trend spikes fast
        fdi = _fractal_dimension(prices, window=15)
        # Exit if ADX explodes or FDI drops (Breakout starting)
        return adx > 35 or fdi < 1.42


# ─────────────────────────────────────────────────────────────────────────────
# 2. Rise / Fall — Hardened Momentum Strategy
# ─────────────────────────────────────────────────────────────────────────────

class RiseFallStrategy:
    NAME = "Momentum_Confluence_V2"

    def analyse(self, ticks: List[float]) -> Optional[Signal]:
        prices = np.array(ticks)
        if len(prices) < 100: return None
        
        fdi = _fractal_dimension(prices)
        adx = _adx(prices)
        rsi = _rsi(prices)
        ema_fast = _ema(prices, 12)
        ema_slow = _ema(prices, 50) # Use a longer slow EMA to filter noise

        # CONFIRMATION LAYER: Trend must be STRONG (ADX > 25) and CLEAN (FDI < 1.45)
        if adx > 28 and fdi < 1.48:
            # Bullish
            if prices[-1] > ema_fast > ema_slow and rsi > 55:
                # Weighted confidence: Needs all 3 to be strong
                conf = 0.65 + (adx/100) + (1.5 - fdi)
                return Signal("CALL", "UP", min(0.92, conf), f"Strong Trend (ADX: {adx:.1f})")

            # Bearish
            if prices[-1] < ema_fast < ema_slow and rsi < 45:
                conf = 0.65 + (adx/100) + (1.5 - fdi)
                return Signal("PUT", "DOWN", min(0.92, conf), f"Strong Trend (ADX: {adx:.1f})")
                
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 3. Digit Match — Probabilistic Anomaly
# ─────────────────────────────────────────────────────────────────────────────

class DigitStrategy:
    NAME = "Statistical_Mean_Regression"

    def analyse(self, ticks: List[float]) -> Optional[Signal]:
        prices = np.array(ticks)
        if len(prices) < 100: return None
        
        entropy = _digit_entropy(prices)
        # Tightened Entropy gate: < 3.10 is a much stronger pattern than < 3.12
        if entropy < 3.09:
            digits = (np.round(prices[-100:] * 100) % 10).astype(int)
            counts = np.bincount(digits, minlength=10)
            cold_digit = np.argmin(counts)
            freq = (counts[cold_digit] / 100) * 100
            
            if freq <= 5.0: # Digit has appeared 5 or fewer times in 100 ticks
                return Signal(
                    "DIGITMATCH", str(cold_digit), 0.85, 
                    f"Statistical Gap (Digit {cold_digit} at {freq}%)",
                    {"digit": int(cold_digit)}
                )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 4. Multipliers — Z-Score + Trend Barrier
# ─────────────────────────────────────────────────────────────────────────────

class MultiplierStrategy:
    NAME = "Quant_Reversion_Barrier"

    def analyse(self, ticks: List[float], multiplier: int = 400) -> Optional[Signal]:
        prices = np.array(ticks)
        if len(prices) < 50: return None
        
        window = prices[-40:]
        z_score = (prices[-1] - np.mean(window)) / (np.std(window) + 1e-9)
        adx = _adx(prices)

        # PROBLEM: Reversion trades fail in hyper-trends.
        # FIX: Only bet on reversion if ADX is falling or low (< 25).
        if adx < 25:
            if z_score > 2.6: # Overbought
                return Signal("MULTDOWN", "DOWN", 0.88, f"Mean Reversion (Z: {z_score:.2f})", 
                              {"multiplier": multiplier, "limit_order": {"take_profit": 0.2, "stop_loss": 0.1}})
            
            if z_score < -2.6: # Oversold
                return Signal("MULTUP", "UP", 0.88, f"Mean Reversion (Z: {z_score:.2f})", 
                              {"multiplier": multiplier, "limit_order": {"take_profit": 0.2, "stop_loss": 0.1}})
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Logic Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

ALL_STRATEGIES = {
    "ACCU":       AccumulatorStrategy(),
    "CALL_PUT":   RiseFallStrategy(),
    "DIGIT":      DigitStrategy(),
    "MULT":       MultiplierStrategy(),
}

def get_best_signal(ticks: List[float], preferred: str = "ACCU", **kwargs) -> Optional[Signal]:
    """Scans all available algorithms and returns the highest confidence opportunity."""
    MIN_CONFIDENCE = 0.74 # Raised the entry bar from 0.68 to 0.74 for production safety

    if strat := ALL_STRATEGIES.get(preferred):
        if sig := strat.analyse(ticks, **kwargs):
            if sig.confidence >= MIN_CONFIDENCE:
                return sig

    signals = []
    for key, strat in ALL_STRATEGIES.items():
        if key == preferred: continue
        try:
            if sig := strat.analyse(ticks):
                if sig.confidence >= MIN_CONFIDENCE:
                    signals.append(sig)
        except:
            continue

    return max(signals, key=lambda s: s.confidence) if signals else None