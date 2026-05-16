"""
Trade Logger — Variant C Calendar Strategy
Logs all trades to CSV for audit trail and live-vs-backtest comparison.
Append-only, never overwrites.

Schema is Variant C-specific (no take-profit field, days_held instead of bars_held)
because trades.csv did not exist at migration time and there's no backward
compatibility constraint with retired-bot trade logs.
"""

import csv
import logging
import os
from typing import Dict, List

logger = logging.getLogger(__name__)

TRADES_CSV = "trades.csv"
CSV_HEADERS = [
    "timestamp",
    "symbol",
    "side",          # always "LONG" for Variant C, kept for schema clarity
    "entry_price",
    "sl_price",      # renamed from "sl" — clearer it's a price not a flag
    "exit_type",     # "MONDAY_EXIT" or "SL_HIT"
    "exit_price",
    "p&l_usd",
    "p&l_pct",
    "days_held",     # renamed from "bars_held" — Variant C is daily timeframe
]


class TradeLogger:
    """Log trades to CSV for audit and analysis."""
    
    def __init__(self, filepath: str = TRADES_CSV):
        """
        Initialize logger.
        
        Args:
            filepath: Path to CSV file (relative or absolute)
        """
        self.filepath = filepath
        self._ensure_csv()
    
    def _ensure_csv(self):
        """Create CSV file with headers if it doesn't exist."""
        if not os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
                    writer.writeheader()
                logger.info(f"Created new trade log: {self.filepath}")
            except Exception as e:
                logger.error(f"Failed to create CSV: {str(e)}")
                raise
    
    def log_trade(self, trade: Dict) -> bool:
        """
        Log a completed trade (append to CSV).
        
        Args:
            trade: Trade dict with required fields:
                - timestamp: ISO format datetime
                - symbol: Trading pair (e.g., BTCUSD)
                - side: "LONG" (Variant C is long-only)
                - entry_price: Entry fill price (Sunday)
                - sl_price: Stop loss price (entry × 0.97 for Variant C)
                - exit_type: "MONDAY_EXIT" or "SL_HIT"
                - exit_price: Exit fill price
                - p&l_usd: Profit/loss in USD
                - p&l_pct: Profit/loss percentage
                - days_held: Number of daily candles held (typically 1)
                
        Returns:
            True if logged successfully, False otherwise
        """
        try:
            # Validate required fields
            required_fields = [
                "timestamp", "symbol", "side", "entry_price",
                "sl_price", "exit_type", "exit_price",
                "p&l_usd", "p&l_pct"
            ]
            for field in required_fields:
                if field not in trade:
                    logger.error(f"Missing required field: {field}")
                    return False
            
            # Prepare row
            row = {
                "timestamp": trade["timestamp"],
                "symbol": trade["symbol"],
                "side": trade["side"],
                "entry_price": round(trade["entry_price"], 2),
                "sl_price": round(trade["sl_price"], 2),
                "exit_type": trade["exit_type"],
                "exit_price": round(trade["exit_price"], 2),
                "p&l_usd": round(trade["p&l_usd"], 2),
                "p&l_pct": round(trade["p&l_pct"], 4),
                "days_held": trade.get("days_held", 0),
            }
            
            # Append to CSV
            with open(self.filepath, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
                writer.writerow(row)
            
            logger.info(
                f"✓ Trade logged: {row['side']} {row['symbol']} "
                f"@ {row['entry_price']} → {row['exit_price']} "
                f"({row['exit_type']}), P&L: ${row['p&l_usd']} ({row['p&l_pct']}%)"
            )
            return True
        
        except Exception as e:
            logger.error(f"Failed to log trade: {str(e)}")
            return False
    
    def read_trades(self, symbol: str = None) -> List[Dict]:
        """
        Read all trades from CSV.
        
        Args:
            symbol: Optional filter by symbol
            
        Returns:
            List of trade dictionaries
        """
        trades = []
        try:
            if not os.path.exists(self.filepath):
                return trades
            
            with open(self.filepath, 'r', newline='') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if symbol and row["symbol"] != symbol:
                        continue
                    
                    # Convert numeric fields
                    row["entry_price"] = float(row["entry_price"])
                    row["sl_price"] = float(row["sl_price"])
                    row["exit_price"] = float(row["exit_price"])
                    row["p&l_usd"] = float(row["p&l_usd"])
                    row["p&l_pct"] = float(row["p&l_pct"])
                    row["days_held"] = int(row["days_held"])
                    
                    trades.append(row)
            
            logger.debug(f"Read {len(trades)} trades from log")
            return trades
        
        except Exception as e:
            logger.error(f"Failed to read trade log: {str(e)}")
            return []
    
    def get_stats(self, symbol: str = None) -> Dict:
        """
        Calculate trade statistics. Used for live-vs-backtest comparison
        and the 30-trade review gate.
        
        Args:
            symbol: Optional filter by symbol
            
        Returns:
            Dict with stats: win_rate, profit_factor, total_pnl, etc.
            For Variant C 30-trade gate, watch:
                - profit_factor vs backtest 1.207 (within 0.2 = aligned)
                - win_rate vs backtest 50.41% (±5pp acceptable)
        """
        trades = self.read_trades(symbol)
        if not trades:
            return {"total_trades": 0}
        
        wins = [t for t in trades if t["p&l_usd"] > 0]
        losses = [t for t in trades if t["p&l_usd"] < 0]
        breakeven = [t for t in trades if t["p&l_usd"] == 0]
        
        total_pnl = sum(t["p&l_usd"] for t in trades)
        gross_profit = sum(t["p&l_usd"] for t in wins) if wins else 0
        gross_loss = abs(sum(t["p&l_usd"] for t in losses)) if losses else 0
        
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        win_rate = len(wins) / len(trades) * 100 if trades else 0
        
        stats = {
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "breakeven": len(breakeven),
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "total_pnl": round(total_pnl, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "avg_winner": round(gross_profit / len(wins), 2) if wins else 0,
            "avg_loser": round(gross_loss / len(losses), 2) if losses else 0,
        }
        
        logger.info(f"Stats: {stats}")
        return stats
