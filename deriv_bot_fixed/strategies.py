"""
strategies.py — Quantitative Engine (Accumulator Variance Arbitrage - Strict Model)
─────────────────────────────────────────────────────────────────────────────────
Architecture:
  1. Macro Regime: Exponentially Weighted Moving Variance (EWMV) for zero-latency regime detection.
  2. Micro Entry: Directional reversal verification to maximize initial barrier distance.
  3. Dynamic Exit: Live Z-score calculation against Deriv's physical barriers (Evasion Probability).
     * Now features EWMV smoothing to prevent false-positive denominator explosions.
  4. Regime Filter: Fractal Dimension Index (FDI) gating for mean-reversion validation.
"""

from dataclasses import dataclass, field
from typing import Optional, List
import numpy as np


@dataclass
class Signal:
    contract_type: str
    direction: Optional[str]
    confidence: float
    reason: str
    params: dict = field(default_factory=dict)

    def __str__(self):
        d = f" → {self.direction}" if self.direction else ""
        return f"[{self.contract_type}{d}] {self.confidence*100:.0f}% — {self.reason}"


# ─────────────────────────────────────────────────────────────────────────────
# Quantitative Indicator Helpers (First-Principles Math)
# ─────────────────────────────────────────────────────────────────────────────

def _fractal_dimension(prices: np.ndarray, window: int = 30) -> float:
    """
    Measures market noise vs. trend. 
    High FDI (> 1.45) mathematically confirms a mean-reverting (choppy) environment,
    which is mandatory for Accumulator survival.
    """
    if len(prices) < window:
        return 1.5
    diffs = np.abs(np.diff(prices[-window:]))
    length = np.sum(diffs)
    if length == 0:
        return 1.5
    price_range = np.max(prices[-window:]) - np.min(prices[-window:])
    return 1.0 + (np.log(length + 1e-9) - np.log(price_range + 1e-9)) / np.log(window)


def _ewmv(returns: np.ndarray, alpha: float = 0.15) -> float:
    """
    Calculates Exponentially Weighted Moving Variance.
    Unlike a simple average, EWMV exponentially decays older tick data, 
    allowing the bot to detect sudden structural volatility shifts without overreacting 
    to isolated tick noise.
    """
    if len(returns) < 10:
        return 0.001

    # Initialize variance baseline
    var = np.var(returns[:10])

    # Apply exponential decay to recent ticks
    for r in returns[10:]:
        var = (1 - alpha) * var + alpha * (r ** 2)

    return var + 1e-9


# ─────────────────────────────────────────────────────────────────────────────
# Core Strategy: Accumulator Variance Arbitrage
# ─────────────────────────────────────────────────────────────────────────────

class AccumulatorStrategy:
    NAME = "ACCU_Variance_Arbitrage_Strict"

    def analyse(self, ticks: List[float], growth_rate: float = 0.01) -> Optional[Signal]:
        """
        ENTRY LOGIC: Evaluates Macro Regime (EWMV + FDI) and Micro Entry (Reversal).
        """
        prices = np.array(ticks)
        if len(prices) < 100:
            return None

        returns = np.diff(prices)

        # 1. Macro Regime Check (Variance Squeeze)
        # We need the current fast variance to be significantly lower than the historical baseline.
        current_variance = _ewmv(returns[-20:], alpha=0.20)
        historical_variance = _ewmv(returns[-100:], alpha=0.05)

        # 2. Regime Choppiness Check (FDI)
        fdi = _fractal_dimension(prices, window=30)

        # Strict logic gates
        is_quiet = current_variance < (historical_variance * 0.85)
        is_ranging = fdi > 1.45

        # 3. Micro Entry Optimization (Centering)
        # Verify the very last tick reversed direction against the previous tick.
        # This ensures we enter as close to the center of the newly formed barrier as possible.
        is_reversal = np.sign(prices[-1] - prices[-2]) != np.sign(prices[-2] - prices[-3])

        if is_quiet and is_ranging and is_reversal:
            base_conf = 0.75
            # Bonus confidence scales with how deep the variance squeeze is
            bonus = ((historical_variance - current_variance) / historical_variance) * 0.2
            confidence = min(0.98, base_conf + bonus)

            return Signal(
                "ACCU", None, confidence,
                f"Strict Squeeze & Centered (Vol Ratio: {current_variance/historical_variance:.2f} | FDI: {fdi:.2f})",
                {"growth_rate": growth_rate}
            )

        return None

    def should_exit(self, ticks: List[float], current_spot: float, high_barrier: float, low_barrier: float, ticks_since_open: int) -> bool:
        """
        EXIT LOGIC: Algorithmic Probability Evasion.
        Must execute on every millisecond a tick updates while the contract is active.
        """
        # 1. MECHANICAL DEBOUNCE
        # Forbid emergency exits for the first 3 ticks to bypass execution and initialization noise.
        # If the entry conditions were met, give the system time to play out the first few sequences.
        if ticks_since_open < 3:
            return False

        prices = np.array(ticks)
        if len(prices) < 20:
            return False

        # 2. SMOOTHED VOLATILITY (Preventing the Denominator Paradox)
        # Using the square root of the EWMV ensures a single volatile tick doesn't artificially 
        # compress the Z-score and trigger a false-positive exit.
        returns = np.diff(prices[-15:])
        current_std = np.sqrt(_ewmv(returns, alpha=0.15))

        # Calculate absolute physical distance to the knockout lines
        dist_to_high = abs(high_barrier - current_spot)
        dist_to_low = abs(current_spot - low_barrier)
        closest_barrier_dist = min(dist_to_high, dist_to_low)

        # Probability Math: How many smoothed standard deviations away is the danger zone?
        z_score = closest_barrier_dist / current_std

        # EPSILON (Risk Tolerance):
        # If the barrier is closer than 0.85 smoothed standard deviations, the probability
        # of the next tick destroying the accumulator exceeds safety limits.
        RISK_TOLERANCE_Z = 0.85

        # Trigger instant exit if risk threshold breached
        return z_score < RISK_TOLERANCE_Z


# ─────────────────────────────────────────────────────────────────────────────
# Execution Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

ACCUMULATOR_ENGINE = AccumulatorStrategy()


def get_entry_signal(ticks: List[float], growth_rate: float = 0.01) -> Optional[Signal]:
    """Scans the tick stream for a mathematically sound entry point."""
    MIN_CONFIDENCE = 0.50

    sig = ACCUMULATOR_ENGINE.analyse(ticks, growth_rate)
    if sig and sig.confidence >= MIN_CONFIDENCE:
        return sig

    return None


def check_exit_condition(ticks: List[float], current_spot: float, high_barrier: float, low_barrier: float, ticks_since_open: int = 0) -> bool:
    """Calculates live probability density to trigger an emergency exit. Returns True to Sell."""
    return ACCUMULATOR_ENGINE.should_exit(ticks, current_spot, high_barrier, low_barrier, ticks_since_open)