"""
Position Sizing Calculator — Variant C Calendar Strategy
Implements long-only risk-based position sizing with fixed % stop loss distance.

Variant C runs at 0.5% risk per trade for first 30 live trades, scaling to 1.0%
after backtest-alignment is confirmed. SL is a fixed 3% from entry (config-driven),
not a dynamic indicator value.
"""

import logging
from typing import Dict
from decimal import Decimal, ROUND_DOWN

logger = logging.getLogger(__name__)


class PositionSizer:
    """Calculate position size based on configurable risk % rule. Long-only."""
    
    def __init__(self, risk_per_trade: float = 0.005):
        """
        Initialize position sizer.
        
        Args:
            risk_per_trade: Risk percentage per trade (default 0.005 = 0.5%)
                            Variant C uses 0.5% for first 30 trades, 1.0% after.
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
        
        For Variant C (long-only, BTC/USD):
            entry_price > stop_loss always (SL is below entry by 3%)
            stop_loss = entry_price * (1 - 0.03)
        
        Args:
            account_equity: Account balance in USD
            entry_price: Entry price (Sunday close)
            stop_loss: Stop loss price (entry × 0.97 for Variant C)
            
        Returns:
            Dict with quantity, risk_amount, sl_distance, entry_price, stop_loss, risk_percentage
        """
        try:
            # Validate inputs
            if account_equity <= 0:
                raise ValueError(f"Invalid account_equity: {account_equity}")
            if entry_price <= 0:
                raise ValueError(f"Invalid entry_price: {entry_price}")
            if stop_loss <= 0:
                raise ValueError(f"Invalid stop_loss: {stop_loss}")
            
            # Variant C is long-only: SL must be below entry
            if stop_loss >= entry_price:
                raise ValueError(
                    f"Variant C is long-only: stop_loss ({stop_loss}) "
                    f"must be below entry_price ({entry_price})"
                )
            
            # Calculate SL distance
            sl_distance = entry_price - stop_loss
            if sl_distance == 0:
                raise ValueError("Stop loss cannot equal entry price")
            
            # Calculate risk amount (config-driven %, HARD CAP)
            risk_amount = account_equity * self.risk_per_trade
            
            # Calculate position size: qty = risk_amount / sl_distance
            quantity = risk_amount / sl_distance
            
            # Round down to avoid over-leveraging.
            # 0.0001 BTC precision matches Kraken's BTC minimum order size.
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
                f"Position sized: {quantity} BTC @ {entry_price}, "
                f"SL: {stop_loss}, Risk: ${risk_amount:.2f} "
                f"({self.risk_per_trade*100:.2f}%)"
            )
            return result
        
        except Exception as e:
            logger.error(f"Position sizing error: {str(e)}")
            raise
    
    def calculate_pnl(self, 
                     entry_price: float, 
                     exit_price: float, 
                     quantity: float,
                     side: str = "LONG") -> Dict:
        """
        Calculate P&L for a closed trade.
        
        Variant C is long-only — only LONG side is supported.
        
        Args:
            entry_price: Entry price
            exit_price: Exit price
            quantity: Position size in BTC
            side: Must be "LONG" (Variant C is long-only)
            
        Returns:
            Dict with pnl_usd, pnl_pct, entry_price, exit_price, quantity
            
        Raises:
            ValueError: if side != "LONG"
        """
        try:
            if side != "LONG":
                raise ValueError(
                    f"Variant C is long-only; got side={side}. "
                    f"Strategy spec lock: spot 1x, no shorts."
                )
            
            pnl_usd = (exit_price - entry_price) * quantity
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100
            
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
