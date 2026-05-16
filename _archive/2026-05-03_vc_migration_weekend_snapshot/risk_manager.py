"""
Risk Manager — Variant C Calendar Strategy
Enforces daily trading limits, loss caps, drawdown protection, and the
layered consecutive-loss policy.

Layered consecutive-loss handling (Variant C policy):
  Layer 1: At consecutive_losses_warning (default 3) — informational alert
           via Telegram. No trading action. ~12% probability event at 50% WR.
  Layer 2: At max_consecutive_losses (default 5) — hard pause requiring
           manual /resume endpoint to clear. ~3% probability event.
           Replaces the old 24H auto-resume — Variant C trades weekly so
           24H is meaningless; manual review is the right safety brake.
"""

import logging
import json
import os
from datetime import datetime
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

RISK_STATE_FILE = "risk_state.json"


class RiskManager:
    """Enforce risk limits per trading session."""
    
    def __init__(self, 
                 max_daily_trades: int = 1,
                 max_consecutive_losses: int = 5,
                 consecutive_losses_warning: int = 3,
                 max_daily_loss: float = -0.03,
                 max_drawdown: float = 0.15,
                 max_drawdown_hard_stop: float = 0.20,
                 state_file: str = RISK_STATE_FILE):
        """
        Initialize risk manager.
        
        Args:
            max_daily_trades: Max trades per day UTC (Variant C: 1)
            max_consecutive_losses: Hard pause threshold — manual resume required
            consecutive_losses_warning: Layer 1 informational threshold
            max_daily_loss: Max loss % per day (-3% = -0.03)
            max_drawdown: Drawdown % threshold to halve position sizing (15%)
            max_drawdown_hard_stop: Drawdown % for total halt (20%)
            state_file: Path to persistent state JSON
        """
        # Sanity: warning threshold must be below hard threshold
        if consecutive_losses_warning >= max_consecutive_losses:
            raise ValueError(
                f"consecutive_losses_warning ({consecutive_losses_warning}) "
                f"must be less than max_consecutive_losses ({max_consecutive_losses})"
            )
        
        self.max_daily_trades = max_daily_trades
        self.max_consecutive_losses = max_consecutive_losses
        self.consecutive_losses_warning = consecutive_losses_warning
        self.max_daily_loss = max_daily_loss
        self.max_drawdown = max_drawdown
        self.max_drawdown_hard_stop = max_drawdown_hard_stop
        self.state_file = state_file
        
        self.state = self._load_state()
    
    def _load_state(self) -> Dict:
        """Load persistent state from file."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                logger.debug(f"Loaded risk state: {state}")
                # Migrate legacy state files: ensure new fields exist
                state.setdefault("paused_until_manual_resume", False)
                state.setdefault("losses_warning_fired", False)
                return state
            except Exception as e:
                logger.error(f"Failed to load state: {str(e)}")
        
        # Initialize new state
        today = datetime.utcnow().strftime("%Y-%m-%d")
        return {
            "date": today,
            "trades_today": 0,
            "daily_pnl": 0,
            "consecutive_losses": 0,
            "paused_until_manual_resume": False,  # Variant C: manual resume flag
            "losses_warning_fired": False,        # Variant C: dedupes 3-loss alert
            "paused_until": None,                  # Legacy field, kept for backward-compat
            "peak_equity": 0,                      # Set on first equity update
            "drawdown_reduction_active": False,
            "winners_since_drawdown": 0,
        }
    
    def _save_state(self):
        """Persist state to file."""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {str(e)}")
    
    def _reset_daily(self):
        """Reset daily counters at UTC midnight."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        
        if self.state["date"] != today:
            logger.info(f"Daily reset: {self.state['date']} → {today}")
            self.state["date"] = today
            self.state["trades_today"] = 0
            self.state["daily_pnl"] = 0
            self._save_state()
    
    def can_trade(self, current_equity: float) -> Tuple[bool, str]:
        """
        Check if trading is allowed.
        
        Returns:
            (can_trade, reason)
        """
        self._reset_daily()
        
        # Layer 2 hard pause: manual resume required
        if self.state.get("paused_until_manual_resume"):
            return False, (
                f"🛑 PAUSED: {self.state['consecutive_losses']} consecutive losses. "
                f"Manual /resume required."
            )
        
        # Check daily trade limit
        if self.state["trades_today"] >= self.max_daily_trades:
            return False, f"❌ Daily trade limit ({self.max_daily_trades}) reached"
        
        # Check daily loss limit (only meaningful if we've actually lost something today)
        if self.state["daily_pnl"] < 0 and self.state["daily_pnl"] <= (current_equity * self.max_daily_loss):
            return False, f"❌ Daily loss limit ({self.max_daily_loss*100:.0f}%) reached: ${self.state['daily_pnl']:.2f}"
        
        # Check hard stop drawdown
        drawdown_pct = self._calculate_drawdown(current_equity)
        if drawdown_pct >= self.max_drawdown_hard_stop:
            return False, f"🛑 HARD STOP: Drawdown {drawdown_pct*100:.1f}% ≥ {self.max_drawdown_hard_stop*100:.0f}%"
        
        return True, "✓ Trading allowed"
    
    def _calculate_drawdown(self, current_equity: float) -> float:
        """Calculate drawdown from peak."""
        # Default peak to 0 so unfunded/uninitialized state returns 0% drawdown.
        # Real peak gets set by record_trade_exit() once equity is actually tracked.
        peak = self.state.get("peak_equity", 0)
        if peak == 0:
            return 0
        return (peak - current_equity) / peak
    
    def record_trade_entry(self) -> bool:
        """
        Record new trade entry. Increments daily counter.
        
        Returns:
            True if entry was recorded, False if limit exceeded
        """
        self._reset_daily()
        
        if self.state["trades_today"] >= self.max_daily_trades:
            logger.warning(f"Cannot record: daily limit ({self.max_daily_trades}) reached")
            return False
        
        self.state["trades_today"] += 1
        self._save_state()
        logger.info(f"Trade #{self.state['trades_today']} recorded (max: {self.max_daily_trades})")
        return True
    
    def record_trade_exit(self, pnl_usd: float, current_equity: float) -> Dict:
        """
        Record trade exit. Updates daily P&L, loss streak, equity peak.
        Implements layered consecutive-loss handling.
        
        Args:
            pnl_usd: P&L in USD
            current_equity: Current account equity
            
        Returns:
            Dict with state updates and event flags:
                - daily_pnl, consecutive_losses, drawdown, paused, reduced_position_sizing
                - losses_warning_just_fired (bool): True if Layer 1 fired this exit
                - circuit_break_just_engaged (bool): True if Layer 2 just engaged
              Caller (main.py) checks these flags to dispatch the right Telegram alerts.
        """
        self._reset_daily()
        
        self.state["daily_pnl"] += pnl_usd
        
        # Update equity peak
        if current_equity > self.state.get("peak_equity", 0):
            self.state["peak_equity"] = current_equity
        
        # Event flags for caller — default False, set True only on the trade where
        # the threshold was crossed. Lets main.py fire alerts exactly once per event.
        losses_warning_just_fired = False
        circuit_break_just_engaged = False
        
        # ─── Layered consecutive-loss handling ───────────────────────────
        if pnl_usd < 0:
            self.state["consecutive_losses"] += 1
            logger.warning(f"Loss recorded. Streak: {self.state['consecutive_losses']}")
            
            # Layer 1: Informational warning at consecutive_losses_warning threshold.
            # Fires exactly once per streak — losses_warning_fired flag dedupes
            # so we don't spam-alert on every subsequent loss while at 3+.
            if (self.state["consecutive_losses"] >= self.consecutive_losses_warning
                    and not self.state.get("losses_warning_fired")):
                self.state["losses_warning_fired"] = True
                losses_warning_just_fired = True
                logger.warning(
                    f"⚠️ Layer 1 warning: {self.state['consecutive_losses']} "
                    f"consecutive losses (threshold: {self.consecutive_losses_warning})"
                )
            
            # Layer 2: Hard pause at max_consecutive_losses. Manual resume required.
            # Engages exactly once — paused_until_manual_resume already True is a no-op.
            if (self.state["consecutive_losses"] >= self.max_consecutive_losses
                    and not self.state.get("paused_until_manual_resume")):
                self.state["paused_until_manual_resume"] = True
                circuit_break_just_engaged = True
                logger.critical(
                    f"🛑 CIRCUIT BREAK: {self.state['consecutive_losses']} consecutive losses "
                    f"(threshold: {self.max_consecutive_losses}). Trading PAUSED — manual resume required."
                )
        else:
            # Winner — reset loss streak and warning-fired flag
            if self.state["consecutive_losses"] > 0:
                logger.info(f"Loss streak broken at {self.state['consecutive_losses']}")
            self.state["consecutive_losses"] = 0
            self.state["losses_warning_fired"] = False
            self.state["winners_since_drawdown"] = self.state.get("winners_since_drawdown", 0) + 1
            
            # Check drawdown recovery
            if self.state.get("drawdown_reduction_active"):
                if self.state["winners_since_drawdown"] >= 5:
                    logger.info(f"✓ 5 winners since drawdown. Resuming normal position sizing")
                    self.state["drawdown_reduction_active"] = False
                    self.state["winners_since_drawdown"] = 0
        
        # Check drawdown threshold
        drawdown = self._calculate_drawdown(current_equity)
        if drawdown >= self.max_drawdown and not self.state.get("drawdown_reduction_active"):
            logger.warning(
                f"⚠️ Drawdown {drawdown*100:.1f}% ≥ {self.max_drawdown*100:.0f}%. "
                f"Halving position sizing."
            )
            self.state["drawdown_reduction_active"] = True
            self.state["winners_since_drawdown"] = 0
        
        self._save_state()
        
        return {
            "daily_pnl": round(self.state["daily_pnl"], 2),
            "consecutive_losses": self.state["consecutive_losses"],
            "paused": self.state.get("paused_until_manual_resume", False),
            "drawdown": round(drawdown * 100, 2),
            "reduced_position_sizing": self.state.get("drawdown_reduction_active", False),
            # Event flags for alerter dispatch
            "losses_warning_just_fired": losses_warning_just_fired,
            "circuit_break_just_engaged": circuit_break_just_engaged,
        }
    
    def manual_resume(self) -> Tuple[bool, str]:
        """
        Manually resume trading after a circuit-break pause.
        Resets the pause flag and the consecutive loss counter.
        
        Returns:
            (success, message)
        """
        if not self.state.get("paused_until_manual_resume"):
            return False, "Not currently paused — no resume needed"
        
        previous_streak = self.state.get("consecutive_losses", 0)
        self.state["paused_until_manual_resume"] = False
        self.state["consecutive_losses"] = 0
        self.state["losses_warning_fired"] = False
        self._save_state()
        
        logger.info(f"✓ Trading resumed manually (was paused after {previous_streak} losses)")
        return True, f"✓ Trading resumed (loss streak of {previous_streak} cleared)"
    
    def get_position_size_multiplier(self) -> float:
        """
        Get position sizing multiplier based on drawdown status.
        
        Returns:
            1.0 = normal, 0.5 = reduced (drawdown active)
        """
        if self.state.get("drawdown_reduction_active"):
            return 0.5
        return 1.0
    
    def get_status(self) -> Dict:
        """Get current risk status."""
        return {
            "date": self.state["date"],
            "trades_today": self.state["trades_today"],
            "daily_pnl": round(self.state["daily_pnl"], 2),
            "consecutive_losses": self.state["consecutive_losses"],
            "paused": self.state.get("paused_until_manual_resume", False),
            "position_size_multiplier": self.get_position_size_multiplier(),
            "drawdown_reduction_active": self.state.get("drawdown_reduction_active", False),
        }
