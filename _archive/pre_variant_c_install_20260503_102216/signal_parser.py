"""
TradingView Webhook Signal Parser
Extracts and validates signals from webhook payloads.
"""

import json
import logging
from typing import Dict, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

VALID_ACTIONS = {
    "LONG",
    "SHORT",
    "CLOSE_HARDSTOP",
    "CLOSE_SOFTSTOP",
    "CLOSE_TAKEPROFIT",
    "CLOSE_TIMEOUT"
}

VALID_SYMBOLS = {"ETHUSDT", "BTCUSDT", "SOLUSDT", "ETHUSD", "BTCUSD", "SOLUSD"}  # Extensible — both USDT and USD pairs


class SignalParser:
    """Parse and validate TradingView webhook signals."""
    
    @staticmethod
    def parse(payload: Dict) -> Tuple[bool, Optional[Dict], str]:
        """
        Parse webhook payload.
        
        Args:
            payload: Webhook JSON payload
            
        Returns:
            (is_valid, signal_dict, error_message)
        """
        try:
            # Extract required fields
            symbol = payload.get("symbol", "").upper()
            action = payload.get("action", "").upper()
            
            # Optional fields for entry signals
            price = payload.get("price")
            supertrend = payload.get("supertrend")
            rsi = payload.get("rsi")
            
            # Validate required fields
            if not symbol or symbol not in VALID_SYMBOLS:
                return False, None, f"Invalid or missing symbol: {symbol}"
            
            if not action or action not in VALID_ACTIONS:
                return False, None, f"Invalid or missing action: {action}"
            
            # Validate entry signals have required fields
            if action in ["LONG", "SHORT"]:
                if price is None or supertrend is None:
                    return False, None, "LONG/SHORT requires price and supertrend"
                
                try:
                    price = float(price)
                    supertrend = float(supertrend)
                    if rsi is not None:
                        rsi = float(rsi)
                except (ValueError, TypeError):
                    return False, None, "price, supertrend, or rsi must be numeric"
            
            # Build signal dict
            signal = {
                "symbol": symbol,
                "action": action,
                "timestamp": datetime.utcnow().isoformat(),
                "price": price,
                "supertrend": supertrend,
                "rsi": rsi,
            }
            
            logger.info(f"✓ Valid signal parsed: {action} {symbol} @ {price}")
            return True, signal, ""
        
        except Exception as e:
            logger.error(f"Signal parse error: {str(e)}")
            return False, None, f"Parse error: {str(e)}"
    
    @staticmethod
    def validate_entry_conditions(signal: Dict, price: float, 
                                 supertrend: float, rsi: float) -> Tuple[bool, str]:
        """
        Validate entry conditions per strategy rules.
        
        Entry conditions:
        - LONG: price > supertrend AND rsi > 50
        - SHORT: price < supertrend AND rsi < 50
        """
        action = signal["action"]
        
        if action == "LONG":
            if price <= supertrend:
                return False, f"LONG invalid: price {price} <= supertrend {supertrend}"
            if rsi <= 50:
                return False, f"LONG invalid: RSI {rsi} <= 50"
            return True, "LONG conditions met"
        
        elif action == "SHORT":
            if price >= supertrend:
                return False, f"SHORT invalid: price {price} >= supertrend {supertrend}"
            if rsi >= 50:
                return False, f"SHORT invalid: RSI {rsi} >= 50"
            return True, "SHORT conditions met"
        
        # Exit signals are always valid
        return True, f"{action} exit signal valid"
