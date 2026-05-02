"""
Kraken API Wrapper via CCXT
Handles order placement, position queries, and account management.
"""

import logging
import time
from typing import Dict, Optional, Tuple
import ccxt

logger = logging.getLogger(__name__)


class KrakenAPI:
    """Kraken spot exchange wrapper via CCXT."""

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """Convert webhook symbol (ETHUSD, ETHUSDT) to CCXT spot format (ETH/USD, ETH/USDT)."""
        if '/' in symbol:
            return symbol
        for quote in ('USDT', 'USD'):
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return f"{base}/{quote}"
        return symbol

    """CCXT-based Kraken Futures trading interface."""
    
    def __init__(self, api_key: str, api_secret: str, sandbox: bool = False):
        """
        Initialize Kraken API.
        
        Args:
            api_key: Kraken API key
            api_secret: Kraken API secret
            sandbox: Use testnet (if available)
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.sandbox = sandbox
        
        # Initialize CCXT Kraken instance
        try:
            self.exchange = ccxt.kraken({
                'apiKey': api_key,
                'secret': api_secret,
                'sandbox': sandbox,
                'enableRateLimit': True,  # Respect 15 req/sec limit
                'rateLimit': 67,  # ~15 req/sec
            })
            
            logger.info(f"✓ Kraken API initialized (sandbox={sandbox})")
        
        except Exception as e:
            logger.error(f"Failed to initialize Kraken: {str(e)}")
            raise
    
    def get_balance(self) -> Tuple[bool, float, str]:
        """
        Get current account equity.

        Tries USD first (current funding), falls back to USDT (legacy).

        Returns:
            (success, equity, error_msg)
        """
        try:
            balance = self.exchange.fetch_balance()

            # Try USD first (current funding), then USDT (legacy support)
            for quote in ('USD', 'USDT'):
                quote_balance = balance.get(quote, {})
                if quote_balance:
                    equity = float(quote_balance.get('total', 0) or 0)
                    logger.debug(f"Balance: ${equity:.2f} {quote}")
                    return True, equity, ""

            # No matching currency found — return 0 cleanly so caller can decide
            logger.warning("No USD or USDT balance found")
            return True, 0.0, ""

        except Exception as e:
            logger.error(f"Failed to fetch balance: {str(e)}")
            return False, 0.0, str(e)
    
    def get_open_positions(self, symbol: str = None) -> Dict:
        """
        Get open positions.
        
        Args:
            symbol: Optional filter by symbol
            
        Returns:
            Dict of open positions by symbol
        """
        try:
            positions = self.exchange.fetch_positions()
            
            open_positions = {}
            for pos in positions:
                # Only include actual open positions (contracts != 0)
                # contractSize is a static market property (never 0), so don't check it
                if pos.get('contracts', 0) != 0:
                    sym = pos['symbol']
                    
                    # Filter by symbol if requested
                    if symbol and sym != symbol:
                        continue
                    
                    open_positions[sym] = {
                        'symbol': sym,
                        'side': pos['side'],  # 'long' or 'short'
                        'size': pos['contracts'],
                        'entry_price': pos['info'].get('openPrice', 0),
                        'mark_price': pos['markPrice'],
                        'liquidation_price': pos['liquidationPrice'],
                        'unrealized_pnl': pos['unrealizedPnl'],
                    }
            
            logger.debug(f"Open positions: {len(open_positions)}")
            return open_positions
        
        except Exception as e:
            logger.error(f"Failed to fetch positions: {str(e)}")
            raise
    
    def place_market_order(self, 
                          symbol: str, 
                          side: str, 
                          quantity: float) -> Tuple[bool, Dict, str]:
        """
        Place market order.
        
        Args:
            symbol: Trading pair (e.g., 'ETH/USDT')
            side: 'buy' or 'sell'
            quantity: Order size
            
        Returns:
            (success, order_dict, error_msg)
        """
        try:
            # Validate inputs
            if side.upper() not in ['BUY', 'SELL']:
                return False, {}, f"Invalid side: {side}"
            
            if quantity <= 0:
                return False, {}, f"Invalid quantity: {quantity}"
            
            # Normalize symbol to CCXT spot format
            symbol = self._normalize_symbol(symbol)
            
            logger.info(f"Placing {side.upper()} {quantity} {symbol}")
            
            # Place market order
            order = self.exchange.create_market_order(
                symbol=symbol,
                side=side.lower(),
                amount=quantity,
            )
            
            result = {
                'order_id': order['id'],
                'symbol': order['symbol'],
                'side': order['side'],
                'amount': order['amount'],
                'average': order.get('average', 0),
                'cost': order.get('cost', 0),
                'timestamp': order['timestamp'],
            }
            
            logger.info(f"✓ Order placed: {result['order_id']}")
            return True, result, ""
        
        except ccxt.InsufficientFunds as e:
            logger.error(f"Insufficient balance: {str(e)}")
            return False, {}, f"Insufficient balance: {str(e)}"
        
        except ccxt.InvalidOrder as e:
            logger.error(f"Invalid order: {str(e)}")
            return False, {}, f"Invalid order: {str(e)}"
        
        except Exception as e:
            logger.error(f"Order placement failed: {str(e)}")
            return False, {}, f"Order error: {str(e)}"
    
    def close_position(self, symbol: str, side: str = None) -> Tuple[bool, Dict, str]:
        """
        Close open position (market order opposite side).
        
        Args:
            symbol: Trading pair
            side: Optional 'long' or 'short' to verify direction
            
        Returns:
            (success, order_dict, error_msg)
        """
        try:
            # Fetch current position
            positions = self.exchange.fetch_positions([symbol])
            
            if not positions or positions[0]['contracts'] == 0:
                return False, {}, f"No open position for {symbol}"
            
            position = positions[0]
            
            # Determine close order side
            if position['side'] == 'long':
                close_side = 'sell'
            elif position['side'] == 'short':
                close_side = 'buy'
            else:
                return False, {}, f"Unknown position side: {position['side']}"
            
            # Verify side if provided
            if side and side.lower() != position['side']:
                return False, {}, f"Position side mismatch: {side} vs {position['side']}"
            
            # Close with market order
            quantity = position['contracts']
            logger.info(f"Closing {position['side']} {quantity} {symbol}")
            
            order = self.exchange.create_market_order(
                symbol=symbol,
                side=close_side,
                amount=quantity,
            )
            
            result = {
                'order_id': order['id'],
                'symbol': order['symbol'],
                'side': position['side'],
                'closed_amount': order['amount'],
                'close_price': order.get('average', 0),
                'timestamp': order['timestamp'],
            }
            
            logger.info(f"✓ Position closed: {result['order_id']}")
            return True, result, ""
        
        except Exception as e:
            logger.error(f"Failed to close position: {str(e)}")
            return False, {}, f"Close error: {str(e)}"
    
    def place_stop_loss_order(self, symbol: str, side: str, quantity: float, stop_price: float) -> Tuple[bool, Dict, str]:
        """
        Place native stop loss order on Kraken after market fill.
        
        Args:
            symbol: Trading pair (e.g., 'ETHUSDT')
            side: 'sell' for LONG (sell if price drops), 'buy' for SHORT
            quantity: Amount to protect
            stop_price: Stop trigger price
        
        Returns:
            (success: bool, order_data: dict, error: str)
        """
        try:
            # Normalize symbol to CCXT spot format (spot-only bot)
            ccxt_symbol = self._normalize_symbol(symbol)
            
            order = self.exchange.create_order(
                symbol=ccxt_symbol,
                type='stop-loss',
                side=side,
                amount=quantity,
                price=stop_price,
                params={'trading_agreement': 'agree'}
            )
            
            logger.info(f"✓ Exchange SL placed for {symbol}: {side} @ {stop_price} qty={quantity}")
            return True, {'sl_order_id': order['id']}, ""
        
        except Exception as e:
            error_msg = f"Failed to place SL order: {str(e)}"
            logger.error(error_msg)
            return False, {}, error_msg
    
    def cancel_order(self, order_id: str, symbol: str) -> Tuple[bool, str]:
        """
        Cancel open order.
        
        Args:
            order_id: Order ID to cancel
            symbol: Trading pair
            
        Returns:
            (success, error_msg)
        """
        try:
            # Normalize symbol to CCXT spot format (spot-only bot)
            ccxt_symbol = self._normalize_symbol(symbol)
            
            self.exchange.cancel_order(order_id, ccxt_symbol)
            logger.info(f"✓ Order {order_id} cancelled")
            return True, ""
        
        except Exception as e:
            error_msg = f"Failed to cancel order {order_id}: {type(e).__name__}: {repr(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    def get_ticker(self, symbol: str) -> Tuple[bool, Dict, str]:
        """
        Get current ticker.
        
        Args:
            symbol: Trading pair
            
        Returns:
            (success, ticker_dict, error_msg)
        """
        try:
            symbol = self._normalize_symbol(symbol)
            ticker = self.exchange.fetch_ticker(symbol)
            
            result = {
                'symbol': ticker['symbol'],
                'bid': ticker['bid'],
                'ask': ticker['ask'],
                'last': ticker['last'],
                'timestamp': ticker['timestamp'],
            }
            
            return True, result, ""
        
        except Exception as e:
            logger.error(f"Failed to fetch ticker: {str(e)}")
            return False, {}, str(e)
    
    def get_ohlcv(self, 
                  symbol: str, 
                  timeframe: str = '4h', 
                  limit: int = 100) -> Tuple[bool, list, str]:
        """
        Get OHLCV candles.
        
        Args:
            symbol: Trading pair
            timeframe: '1m', '5m', '1h', '4h', etc.
            limit: Number of candles
            
        Returns:
            (success, ohlcv_list, error_msg)
        """
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            return True, ohlcv, ""
        
        except Exception as e:
            logger.error(f"Failed to fetch OHLCV: {str(e)}")
            return False, [], str(e)
    
    def get_leverage(self, symbol: str) -> Tuple[bool, Dict, str]:
        """
        Get leverage settings for symbol.
        
        Returns:
            (success, leverage_dict, error_msg)
        """
        try:
            # CCXT leverage API varies by exchange
            # Kraken may require direct API call
            logger.warning("Leverage check not fully implemented for Kraken via CCXT")
            return True, {'leverage': 1}, ""
        
        except Exception as e:
            logger.error(f"Failed to fetch leverage: {str(e)}")
            return False, {}, str(e)
    
    def set_leverage(self, symbol: str, leverage: float) -> Tuple[bool, str]:
        """
        Set leverage for symbol (1x only per requirements).
        
        Returns:
            (success, error_msg)
        """
        try:
            if leverage != 1:
                return False, f"Only 1x leverage allowed per risk policy"
            
            # CCXT leverage API varies
            logger.info(f"Leverage 1x confirmed for {symbol}")
            return True, ""
        
        except Exception as e:
            logger.error(f"Failed to set leverage: {str(e)}")
            return False, str(e)
