"""
Risk Manager
Enforces daily trading limits, loss caps, and drawdown protection.
"""

import logging
import json
import os
from datetime import datetime, timedelta
from typing import Dict, Tuple, List

logger = logging.getLogger(__name__)

RISK_STATE_FILE = "risk_state.json"


class RiskManager:
    """Enforce risk limits per trading session."""
    
    def __init__(self, 
                 max_daily_trades: int = 2,
                 max_consecutive_losses: int = 5,
                 max_daily_loss: float = -0.03,
                 max_drawdown: float = 0.15,
                 max_drawdown_hard_stop: float = 0.20,
                 state_file: str = RISK_STATE_FILE):
        """
        Initialize risk manager.
        
        Args:
            max_daily_trades: Max trades per day (UTC)
            max_consecutive_losses: Max losses before 24H pause
            max_daily_loss: Max loss % per day (-3% = -0.03)
            max_drawdown: Drawdown % before 0.5% position sizing
            max_drawdown_hard_stop: Drawdown % for total halt (20%)
            state_file: Path to persistent state JSON
        """
        self.max_daily_trades = max_daily_trades
        self.max_consecutive_losses = max_consecutive_losses
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
            "paused_until": None,
            "peak_equity": 100000,  # Baseline
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
        
        # Check pause status
        if self.state["paused_until"]:
            pause_until = datetime.fromisoformat(self.state["paused_until"])
            if datetime.utcnow() < pause_until:
                hours_left = (pause_until - datetime.utcnow()).total_seconds() / 3600
                return False, f"⏸️ Trading paused for {hours_left:.1f}h (consecutive losses limit hit)"
            else:
                self.state["paused_until"] = None
                self.state["consecutive_losses"] = 0
                self._save_state()
        
        # Check daily trade limit
        if self.state["trades_today"] >= self.max_daily_trades:
            return False, f"❌ Daily trade limit ({self.max_daily_trades}) reached"
        
        # Check daily loss limit
        if self.state["daily_pnl"] <= (current_equity * self.max_daily_loss):
            return False, f"❌ Daily loss limit (-3%) reached: ${self.state['daily_pnl']:.2f}"
        
        # Check hard stop drawdown
        drawdown_pct = self._calculate_drawdown(current_equity)
        if drawdown_pct >= self.max_drawdown_hard_stop:
            return False, f"🛑 HARD STOP: Drawdown {drawdown_pct:.1f}% ≥ {self.max_drawdown_hard_stop*100:.0f}%"
        
        return True, "✓ Trading allowed"
    
    def _calculate_drawdown(self, current_equity: float) -> float:
        """Calculate drawdown from peak."""
        peak = self.state.get("peak_equity", 100000)
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
        
        Args:
            pnl_usd: P&L in USDT
            current_equity: Current account equity
            
        Returns:
            Dict with state updates
        """
        self._reset_daily()
        
        self.state["daily_pnl"] += pnl_usd
        
        # Update equity peak
        if current_equity > self.state.get("peak_equity", 0):
            self.state["peak_equity"] = current_equity
        
        # Track consecutive losses
        if pnl_usd < 0:
            self.state["consecutive_losses"] += 1
            logger.warning(f"Loss recorded. Streak: {self.state['consecutive_losses']}")
            
            # Trigger pause if max consecutive losses hit
            if self.state["consecutive_losses"] >= self.max_consecutive_losses:
                pause_until = datetime.utcnow() + timedelta(hours=24)
                self.state["paused_until"] = pause_until.isoformat()
                logger.critical(
                    f"⏸️ Trading PAUSED for 24H due to {self.max_consecutive_losses} consecutive losses"
                )
        else:
            self.state["consecutive_losses"] = 0
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
            logger.warning(f"⚠️ Drawdown {drawdown*100:.1f}% ≥ {self.max_drawdown*100:.0f}%. Reducing to 0.5% position sizing")
            self.state["drawdown_reduction_active"] = True
            self.state["winners_since_drawdown"] = 0
        
        self._save_state()
        
        return {
            "daily_pnl": round(self.state["daily_pnl"], 2),
            "consecutive_losses": self.state["consecutive_losses"],
            "paused": self.state["paused_until"] is not None,
            "drawdown": round(drawdown * 100, 2),
            "reduced_position_sizing": self.state.get("drawdown_reduction_active", False),
        }
    
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
            "paused": self.state["paused_until"] is not None,
            "position_size_multiplier": self.get_position_size_multiplier(),
        }
