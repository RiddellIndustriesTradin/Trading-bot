"""
Position Sizing Calculator
Implements 1% risk per trade with dynamic stop loss distance.
"""

import logging
from typing import Dict, Tuple
from decimal import Decimal, ROUND_DOWN

logger = logging.getLogger(__name__)


class PositionSizer:
    """Calculate position size based on 1% risk rule."""
    
    def __init__(self, risk_per_trade: float = 0.01):
        """
        Initialize position sizer.
        
        Args:
            risk_per_trade: Risk percentage per trade (default 0.01 = 1%)
        """
        self.risk_per_trade = risk_per_trade
    
    def calculate(self, 
                 account_equity: float, 
                 entry_price: float, 
                 stop_loss: float) -> Dict:
        """
        Calculate position size for a trade.
        
        Formula:
            quantity = (account_equity * risk_per_trade) / (entry_price - stop_loss)
        
        Args:
            account_equity: Account balance in USDT
            entry_price: Entry price
            stop_loss: Stop loss price
            
        Returns:
            Dict with quantity, risk_amount, sl_distance
        """
        try:
            # Validate inputs
            if account_equity <= 0:
                raise ValueError(f"Invalid account_equity: {account_equity}")
            if entry_price <= 0:
                raise ValueError(f"Invalid entry_price: {entry_price}")
            if stop_loss <= 0:
                raise ValueError(f"Invalid stop_loss: {stop_loss}")
            
            # Calculate SL distance (absolute)
            sl_distance = abs(entry_price - stop_loss)
            if sl_distance == 0:
                raise ValueError("Stop loss cannot equal entry price")
            
            # Calculate risk amount (1% of equity, HARD CAP)
            risk_amount = account_equity * self.risk_per_trade
            
            # Calculate position size
            # qty = risk_amount / sl_distance
            quantity = risk_amount / sl_distance
            
            # Round down to avoid over-leveraging
            # For USDT pairs, typically 2-4 decimal places
            quantity = float(Decimal(str(quantity)).quantize(
                Decimal('0.0001'), 
                rounding=ROUND_DOWN
            ))
            
            if quantity <= 0:
                raise ValueError(f"Calculated quantity is 0 or negative: {quantity}")
            
            result = {
                "quantity": quantity,
                "risk_amount": round(risk_amount, 2),
                "sl_distance": round(sl_distance, 2),
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "risk_percentage": self.risk_per_trade * 100,
            }
            
            logger.info(
                f"Position sized: {quantity} @ {entry_price}, "
                f"SL: {stop_loss}, Risk: ${risk_amount:.2f} (1%)"
            )
            return result
        
        except Exception as e:
            logger.error(f"Position sizing error: {str(e)}")
            raise
    
    def calculate_take_profit(self, 
                             entry_price: float, 
                             stop_loss: float) -> float:
        """
        Calculate take profit target: entry + (SL_distance × 1.5)
        
        This creates a favorable risk/reward ratio of 1:1.5
        """
        sl_distance = abs(entry_price - stop_loss)
        
        # For LONG trades: TP = entry + (sl_distance * 1.5)
        # For SHORT trades: TP = entry - (sl_distance * 1.5)
        if entry_price > stop_loss:  # LONG
            take_profit = entry_price + (sl_distance * 1.5)
        else:  # SHORT
            take_profit = entry_price - (sl_distance * 1.5)
        
        return round(take_profit, 2)
    
    def calculate_pnl(self, 
                     entry_price: float, 
                     exit_price: float, 
                     quantity: float,
                     side: str) -> Dict:
        """
        Calculate P&L for a closed trade.
        
        Args:
            entry_price: Entry price
            exit_price: Exit price
            quantity: Position size in contracts
            side: "LONG" or "SHORT"
            
        Returns:
            Dict with pnl_usd, pnl_pct
        """
        try:
            if side == "LONG":
                pnl_usd = (exit_price - entry_price) * quantity
            elif side == "SHORT":
                pnl_usd = (entry_price - exit_price) * quantity
            else:
                raise ValueError(f"Invalid side: {side}")
            
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100
            if side == "SHORT":
                pnl_pct = -pnl_pct
            
            result = {
                "pnl_usd": round(pnl_usd, 2),
                "pnl_pct": round(pnl_pct, 2),
                "entry_price": entry_price,
                "exit_price": exit_price,
                "quantity": quantity,
            }
            
            logger.info(f"P&L calculated: ${pnl_usd:.2f} ({pnl_pct:.2f}%)")
            return result
        
        except Exception as e:
            logger.error(f"P&L calculation error: {str(e)}")
            raise
