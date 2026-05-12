"""
TradingView Webhook Signal Parser — Variant C Calendar Strategy
Extracts and validates signals from webhook payloads.

Variant C is a calendar strategy: entry/exit are time-driven, not indicator-driven.
SUNDAY_ENTRY fires on Sunday daily close UTC. MONDAY_EXIT fires on Monday daily
close UTC. SL fills are handled exchange-side by Kraken — no SL_HIT alert from TV.
"""

import logging
from typing import Dict, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

# Variant C action set — calendar-driven only
# Entry: SUNDAY_ENTRY (Sunday close UTC)
# Exit:  MONDAY_EXIT (Monday close UTC)
# SL fills are detected via Kraken position state in main.py, not via TV alert
VALID_ACTIONS = {
    "SUNDAY_ENTRY",
    "MONDAY_EXIT",
}

# Variant C is BTC-only per multi-pair backtest results.
# ETH and SOL failed gates on Variant C — see migration spec §11.
VALID_SYMBOLS = {"BTCUSD"}


class SignalParser:
    """Parse and validate TradingView webhook signals for Variant C."""
    
    @staticmethod
    def parse(payload: Dict) -> Tuple[bool, Optional[Dict], str]:
        """
        Parse webhook payload.
        
        Expected payload format:
            {"symbol": "BTCUSD", "action": "SUNDAY_ENTRY", "price": 78400.00}
            {"symbol": "BTCUSD", "action": "MONDAY_EXIT",  "price": 78850.00}
        
        Args:
            payload: Webhook JSON payload
            
        Returns:
            (is_valid, signal_dict, error_message)
        """
        try:
            # Extract required fields
            symbol = payload.get("symbol", "").upper()
            action = payload.get("action", "").upper()
            price = payload.get("price")
            
            # Validate symbol
            if not symbol or symbol not in VALID_SYMBOLS:
                return False, None, f"Invalid or missing symbol: {symbol}"
            
            # Validate action
            if not action or action not in VALID_ACTIONS:
                return False, None, f"Invalid or missing action: {action}"
            
            # Validate price field
            # SUNDAY_ENTRY requires price (used for entry order + SL calc).
            # MONDAY_EXIT requires price for logging — bot will use Kraken
            # ticker for actual exit fill price, but TV-reported price is
            # logged for backtest cross-reference.
            if price is None:
                return False, None, f"{action} requires price field"
            
            try:
                price = float(price)
            except (ValueError, TypeError):
                return False, None, "price must be numeric"
            
            if price <= 0:
                return False, None, f"price must be positive, got {price}"
            
            # Build signal dict
            signal = {
                "symbol": symbol,
                "action": action,
                "timestamp": datetime.utcnow().isoformat(),
                "price": price,
            }
            
            logger.info(f"✓ Valid signal parsed: {action} {symbol} @ {price}")
            return True, signal, ""
        
        except Exception as e:
            logger.error(f"Signal parse error: {str(e)}")
            return False, None, f"Parse error: {str(e)}"
