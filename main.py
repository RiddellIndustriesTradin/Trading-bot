"""
Proppa Kraken Crypto Bot
Production-ready Kraken Spot trading bot with TradingView webhook integration.
Supertrend + RSI strategy with full risk management.
"""

import os
import sys
import logging
import json
import signal
from datetime import datetime, timedelta
from typing import Dict, Tuple

from flask import Flask, request, jsonify
import yaml
from dotenv import load_dotenv

# Import bot modules
from signal_parser import SignalParser
from kraken_api import KrakenAPI
from position_sizing import PositionSizer
from risk_manager import RiskManager
from telegram_alerts import TelegramAlerter
from trade_logger import TradeLogger

# Load environment variables
load_dotenv()

# Ensure logs directory exists before logging setup
# Railway filesystem won't have this directory pre-created
os.makedirs('logs', exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('logs/bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False


class TradingBot:
    """Main trading bot orchestrator."""
    
    def __init__(self, config_path: str = 'config.yaml'):
        """
        Initialize bot.
        
        Args:
            config_path: Path to configuration YAML
        """
        self.config = self._load_config(config_path)
        
        # Initialize components
        self.kraken = KrakenAPI(
            api_key=self.config['kraken']['api_key'],
            api_secret=self.config['kraken']['api_secret'],
            sandbox=self.config['kraken'].get('sandbox', False),
        )
        
        self.position_sizer = PositionSizer(
            risk_per_trade=self.config['trading']['risk_per_trade']
        )
        
        self.risk_manager = RiskManager(
            max_daily_trades=self.config['trading']['max_daily_trades'],
            max_consecutive_losses=self.config['trading']['max_consecutive_losses'],
            max_daily_loss=self.config['trading']['max_daily_loss'],
            max_drawdown=self.config['trading']['max_drawdown'],
            max_drawdown_hard_stop=self.config['trading']['max_drawdown_hard_stop'],
        )
        
        self.alerter = TelegramAlerter(
            bot_token=self.config['telegram']['bot_token'],
            chat_id=self.config['telegram']['chat_id'],
        )
        
        self.logger = TradeLogger('trades.csv')
        self.signal_parser = SignalParser()
        
        # Open positions tracker
        self.positions_state_file = "positions_state.json"
        self.open_positions = self._load_positions()  # Load on startup
        self.last_updated = None
        
        logger.info("✓ Trading Bot initialized")
    
    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from YAML."""
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            
            # Load API keys from environment
            config['kraken']['api_key'] = os.getenv('KRAKEN_API_KEY', config['kraken'].get('api_key'))
            config['kraken']['api_secret'] = os.getenv('KRAKEN_API_SECRET', config['kraken'].get('api_secret'))
            config['telegram']['bot_token'] = os.getenv('TELEGRAM_BOT_TOKEN', config['telegram'].get('bot_token'))
            config['telegram']['chat_id'] = os.getenv('TELEGRAM_CHAT_ID', config['telegram'].get('chat_id'))
            
            # Validate required fields
            if not config['kraken']['api_key']:
                raise ValueError("KRAKEN_API_KEY not set")
            if not config['kraken']['api_secret']:
                raise ValueError("KRAKEN_API_SECRET not set")
            
            logger.info(f"✓ Config loaded from {config_path}")
            return config
        
        except Exception as e:
            logger.error(f"Failed to load config: {str(e)}")
            raise
    
    def _save_positions(self):
        """Persist open positions to JSON file"""
        try:
            with open(self.positions_state_file, 'w') as f:
                # Convert positions dict to JSON-serializable format
                positions_json = {}
                for symbol, trade in self.open_positions.items():
                    positions_json[symbol] = {
                        'entry_price': float(trade.get('entry_price', 0)),
                        'entry_time': str(trade.get('entry_time', '')),
                        'symbol': trade.get('symbol', ''),
                        'side': trade.get('side', ''),
                        'quantity': float(trade.get('quantity', 0)),
                        'sl': float(trade.get('sl', 0)),
                        'tp': float(trade.get('tp', 0)),
                        'bars_held': int(trade.get('bars_held', 0)),
                        'sl_order_id': trade.get('sl_order_id'),
                    }
                json.dump(positions_json, f, indent=2)
                logger.debug(f"Saved {len(positions_json)} open positions")
        except Exception as e:
            logger.error(f"Failed to save positions state: {e}")
    
    def _load_positions(self):
        """Load open positions from JSON file on startup"""
        if not os.path.exists(self.positions_state_file):
            return {}
        
        try:
            with open(self.positions_state_file, 'r') as f:
                positions_json = json.load(f)
                positions = {}
                for symbol, trade_data in positions_json.items():
                    # Parse entry_time from ISO format string back to datetime
                    entry_time_str = trade_data.get('entry_time')
                    try:
                        entry_time = datetime.fromisoformat(entry_time_str) if entry_time_str else None
                    except:
                        entry_time = None
                    
                    positions[symbol] = {
                        'entry_price': trade_data.get('entry_price'),
                        'entry_time': entry_time,
                        'symbol': trade_data.get('symbol'),
                        'side': trade_data.get('side'),
                        'quantity': trade_data.get('quantity'),
                        'sl': trade_data.get('sl'),
                        'tp': trade_data.get('tp'),
                        'bars_held': trade_data.get('bars_held', 0),
                        'sl_order_id': trade_data.get('sl_order_id'),
                    }
                logger.info(f"Loaded {len(positions)} open positions from state file")
                return positions
        except Exception as e:
            logger.error(f"Failed to load positions state: {e}")
            return {}
    
    def _calculate_bars_held(self, entry_time: datetime) -> int:
        """Calculate bars held since entry (4H candles)."""
        if not entry_time:
            return 0
        try:
            duration = datetime.utcnow() - entry_time
            bars = int(duration.total_seconds() / 14400)  # 14400 = 4 hours in seconds
            return max(0, bars)
        except:
            return 0
    
    def get_account_balance(self) -> Tuple[bool, float, str]:
        """Get current account equity."""
        try:
            success, balance, error = self.kraken.get_balance()
            return success, balance, error
        except Exception as e:
            return False, 0, str(e)
    
    def _handle_entry(self, symbol: str, action: str, price: float, supertrend: float, rsi: float) -> Tuple[Dict, int]:
        """
        Handle entry signal.
        
        Returns:
            (response_dict, http_status_code)
        """
        # Get account balance FIRST (needed for risk gates)
        success, equity, error = self.get_account_balance()
        if not success:
            logger.error(f"Failed to get balance: {error}")
            return {"status": "error", "message": "Balance query failed"}, 500
        
        # Check risk gates (needs current equity)
        can_trade, reason = self.risk_manager.can_trade(equity)
        if not can_trade:
            logger.warning(f"Entry blocked: {reason}")
            return {"status": "rejected", "message": reason}, 403
        
        # Check for duplicate position
        normalized_symbol = symbol.replace('/', '').replace(':USDT', '')
        if any(normalized_symbol in pos_sym for pos_sym in self.open_positions.keys()):
            return {"status": "rejected", "message": "Position already open"}, 409
        
        # Calculate position size
        sl_distance = abs(price - supertrend)
        if sl_distance < 0.01:
            return {"status": "rejected", "message": "SL distance too small"}, 400
        
        # Supertrend sanity check — reject if SL >10% from entry price
        # Catches wrong-plot-value scenarios before placing orders on Kraken
        if sl_distance / price > 0.10:
            logger.error(
                f"Supertrend sanity check failed: "
                f"price={price}, supertrend={supertrend}, "
                f"distance={sl_distance/price:.2%}"
            )
            return {
                "status": "rejected",
                "message": "Supertrend value fails sanity check (>10% from entry)"
            }, 400
        
        position = self.position_sizer.calculate(
            account_equity=equity,
            entry_price=price,
            stop_loss=supertrend
        )
        qty = position["quantity"]
        risk_usd = position["risk_amount"]
        
        if qty <= 0:
            return {"status": "rejected", "message": "Position size invalid"}, 400
        
        # Place market entry
        try:
            success, order, error = self.kraken.place_market_order(
                symbol=symbol,
                side='buy' if action == 'LONG' else 'sell',
                quantity=qty
            )
            
            if not success:
                logger.error(f"Entry order failed: {error}")
                return {"status": "error", "message": error}, 500
            
            entry_price = order.get('average') or price
            
            # Recalculate TP based on actual entry
            sl_distance = abs(entry_price - supertrend)
            take_profit = entry_price + (sl_distance * 1.5) if action == 'LONG' else entry_price - (sl_distance * 1.5)
            
            # Log successful entry
            trade = {
                'entry_price': entry_price,
                'entry_time': datetime.utcnow(),
                'symbol': symbol,
                'side': action,
                'quantity': qty,
                'sl': supertrend,
                'tp': take_profit,
                'bars_held': 0,
            }
            self.open_positions[symbol] = trade
            self._save_positions()
            
            # Place exchange-level stop loss order on Kraken
            sl_side = 'sell' if action == 'LONG' else 'buy'
            sl_success, sl_order, sl_error = self.kraken.place_stop_loss_order(
                symbol=symbol,
                side=sl_side,
                quantity=qty,
                stop_price=supertrend
            )
            
            if sl_success:
                trade['sl_order_id'] = sl_order['sl_order_id']
                self._save_positions()
                logger.info(f"✓ Exchange SL placed @ {supertrend}")
            else:
                logger.error(f"⚠️ Exchange SL placement failed: {sl_error}")
                trade['sl_order_id'] = None
                self.alerter.alert_risk_event("SL_PLACEMENT_FAILED", f"⚠️ SL Placement Failed: {sl_error}")
                self._save_positions()
            
            # Send Telegram alert
            if action == 'LONG':
                self.alerter.alert_entry_long(trade)
            else:
                self.alerter.alert_entry_short(trade)
            
            # Update risk manager
            if not self.risk_manager.record_trade_entry():
                logger.warning("record_trade_entry returned False after entry+SL - daily limit race?")
            
            logger.info(f"✓ Entry: {action} {symbol} @ {entry_price} | SL: {supertrend} | TP: {take_profit}")
            return {"status": "success", "entry_price": entry_price}, 200
        
        except Exception as e:
            logger.error(f"Entry handler exception: {str(e)}")
            return {"status": "error", "message": str(e)}, 500
    
    def _handle_exit(self, symbol: str, exit_type: str) -> Tuple[Dict, int]:
        """
        Handle exit signal.
        
        Returns:
            (response_dict, http_status_code)
        """
        if symbol not in self.open_positions:
            return {"status": "rejected", "message": "No open position"}, 404
        
        trade = self.open_positions[symbol]
        
        try:
            # Cancel exchange SL order if it exists
            if trade.get('sl_order_id'):
                logger.info(f"Cancelling exchange SL order {trade['sl_order_id']}")
                cancel_success, cancel_error = self.kraken.cancel_order(
                    trade['sl_order_id'], symbol
                )
                if not cancel_success:
                    logger.warning(f"Failed to cancel SL: {cancel_error}")
            
            # Close position
            # Exit is always a sell on spot (long-only mode)
            success, order, error = self.kraken.place_market_order(symbol, 'sell', trade['quantity'])
            
            if not success:
                # If Kraken already triggered the SL, position is already closed
                if 'No open position' in error or 'already closed' in error:
                    logger.info(f"Exchange SL already triggered for {symbol} — treating as successful exit")
                    # Use last ticker price as exit price
                    try:
                        ticker = self.kraken.get_ticker(symbol)
                        exit_price = ticker.get('last', trade['entry_price'])
                    except:
                        exit_price = trade['entry_price']
                    order = {'close_price': exit_price}
                    success = True
                else:
                    return {"status": "error", "message": error}, 500
            
            exit_price = order.get('average') or order.get('close_price')
            
            # Fallback if price is 0 or None
            if not exit_price or exit_price == 0:
                try:
                    ticker = self.kraken.get_ticker(symbol)
                    exit_price = ticker.get('last', trade['entry_price'])
                    logger.warning(f"Exit: fill price unavailable for {symbol}, using last price: {exit_price}")
                except Exception as e:
                    logger.error(f"Exit: failed to get fallback price: {e}, using entry price")
                    exit_price = trade['entry_price']
            
            # Calculate P&L
            pnl_usd = (exit_price - trade['entry_price']) * trade['quantity']
            pnl_pct = ((exit_price - trade['entry_price']) / trade['entry_price']) * 100
            
            # Calculate bars held
            bars_held = self._calculate_bars_held(trade['entry_time'])
            
            # Log trade
            self.logger.log_trade(
                symbol=symbol,
                side=trade['side'],
                entry_price=trade['entry_price'],
                exit_price=exit_price,
                quantity=trade['quantity'],
                pnl_usd=pnl_usd,
                pnl_pct=pnl_pct,
                exit_type=exit_type,
                bars_held=bars_held
            )
            
            # Update risk manager
            balance_ok, current_equity, balance_err = self.kraken.get_balance()
            if not balance_ok:
                logger.warning(f"Could not fetch balance for risk manager update: {balance_err}")
                current_equity = trade['entry_price'] * trade['quantity']  # rough fallback
            self.risk_manager.record_trade_exit(pnl_usd, current_equity)
            
            # Send Telegram alert
            # Enrich trade dict with exit info, then dispatch to right alerter variant
            trade['exit_price'] = exit_price
            trade['p&l_usd'] = pnl_usd
            trade['p&l_pct'] = pnl_pct
            trade['bars_held'] = bars_held
            if exit_type == 'CLOSE_HARDSTOP':
                self.alerter.alert_exit_hardstop(trade)
            elif exit_type == 'CLOSE_SOFTSTOP':
                self.alerter.alert_exit_softstop(trade)
            elif exit_type == 'CLOSE_TAKEPROFIT':
                self.alerter.alert_exit_takeprofit(trade)
            elif exit_type == 'CLOSE_TIMEOUT':
                self.alerter.alert_exit_timeout(trade)
            else:
                logger.warning(f"Unknown exit_type for alerter dispatch: {exit_type}")
            
            # Remove from open positions
            del self.open_positions[symbol]
            self._save_positions()
            
            logger.info(f"✓ Exit: {symbol} @ {exit_price} | P&L: {pnl_usd:.2f} USD ({pnl_pct:.2f}%) | {exit_type}")
            return {"status": "success", "exit_price": exit_price, "pnl": pnl_usd}, 200
        
        except Exception as e:
            logger.error(f"Exit handler exception: {str(e)}")
            return {"status": "error", "message": str(e)}, 500
    
    def handle_webhook(self, payload: Dict) -> Tuple[Dict, int]:
        """
        Main webhook handler for TradingView alerts.
        
        Payload format:
        {
            "symbol": "ETHUSDT",
            "action": "LONG|SHORT|CLOSE_HARDSTOP|CLOSE_SOFTSTOP|CLOSE_TAKEPROFIT|CLOSE_TIMEOUT",
            "price": 2500.50,
            "supertrend": 2450.00,
            "rsi": 65.5
        }
        """
        try:
            # Parse signal
            success, parsed, error = self.signal_parser.parse(payload)
            if not success:
                return {"status": "rejected", "message": error}, 400
            
            symbol = payload.get('symbol')
            action = payload.get('action')
            price = float(payload.get('price', 0))
            supertrend = float(payload.get('supertrend', 0))
            rsi = float(payload.get('rsi', 0))
            
            # Route to handler
            if action in ['LONG', 'SHORT']:
                return self._handle_entry(symbol, action, price, supertrend, rsi)
            else:
                return self._handle_exit(symbol, action)
        
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"Webhook exception: {str(e)}\n{tb}")
            return {"status": "error", "message": str(e), "traceback": tb}, 500


# Initialize bot (global)
bot = None

try:
    bot = TradingBot()
except Exception as e:
    logger.error(f"Failed to initialize bot: {str(e)}")
    bot = None


# Flask routes
@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    if bot is None:
        return jsonify({"status": "offline", "message": "Bot not initialized"}), 500
    
    try:
        success, balance, error = bot.get_account_balance()
        if not success:
            return jsonify({"status": "error", "message": error}), 500
        
        return jsonify({
            "status": "online",
            "balance": balance,
            "open_positions": len(bot.open_positions),
            "timestamp": datetime.utcnow().isoformat()
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/webhook', methods=['POST'])
def webhook():
    """TradingView webhook receiver."""
    if bot is None:
        return jsonify({"status": "offline", "message": "Bot not initialized"}), 500
    
    try:
        payload = request.get_json()
        if not payload:
            return jsonify({"status": "error", "message": "Empty payload"}), 400
        
        response, status = bot.handle_webhook(payload)
        return jsonify(response), status
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/status', methods=['GET'])
def status():
    """Status endpoint."""
    if bot is None:
        return jsonify({"status": "offline"}), 500
    
    try:
        success, balance, error = bot.get_account_balance()
        
        return jsonify({
            "status": "online",
            "balance": balance,
            "open_positions": len(bot.open_positions),
            "daily_trades": bot.risk_manager.daily_trade_count,
            "consecutive_losses": bot.risk_manager.consecutive_losses,
            "timestamp": datetime.utcnow().isoformat()
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# Graceful shutdown
def shutdown_handler(signum, frame):
    """Handle graceful shutdown."""
    logger.info("Shutdown signal received. Cleaning up...")
    sys.exit(0)


signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
# DEPLOYMENT MARKER: 177efe7-test-1777106604
