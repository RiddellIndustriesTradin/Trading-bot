"""
Proppa Kraken Crypto Bot — Variant C Calendar Strategy
Production-ready Kraken Spot trading bot with TradingView webhook integration.

Strategy: Variant C — Sunday→Monday BTC weekend hold (long-only spot).
  Entry:  SUNDAY_ENTRY alert (Sunday daily close UTC) → market buy + exchange SL
  Exit:   MONDAY_EXIT alert (Monday daily close UTC) → market sell
          OR exchange-side SL hit intrabar (3% below entry)
  
Backtest: PF 1.207, DD 10.16%, 121 trades, WR 50.41%, +9.00% over 28 months.
See migration spec: Proppa_Kraken_Spot_v2_BotMigrationSpec.md
"""

import os
import sys
import logging
import json
import signal
import threading
from datetime import datetime
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

# Ensure logs directory exists before logging setup.
# Railway filesystem won't have this directory pre-created.
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
    """Main trading bot orchestrator — Variant C calendar strategy."""
    
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
            consecutive_losses_warning=self.config['trading']['consecutive_losses_warning'],
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
        self.open_positions = self._load_positions()
        self.last_updated = None
        
        # Variant C strategy params (cached for hot-path access)
        self.sl_pct = self.config['strategy']['sl_pct']  # 0.03
        
        logger.info(
            f"✓ Trading Bot initialized — "
            f"Variant C calendar strategy, SL {self.sl_pct*100:.1f}%, "
            f"risk {self.position_sizer.risk_per_trade*100:.2f}%/trade"
        )
    
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
            
            # Validate strategy section exists (Variant C addition)
            if 'strategy' not in config:
                raise ValueError("config.yaml missing 'strategy' section (Variant C requires it)")
            if 'sl_pct' not in config['strategy']:
                raise ValueError("config.yaml missing 'strategy.sl_pct'")
            
            logger.info(f"✓ Config loaded from {config_path}")
            return config
        
        except Exception as e:
            logger.error(f"Failed to load config: {str(e)}")
            raise
    
    def _save_positions(self):
        """Persist open positions to JSON file."""
        try:
            with open(self.positions_state_file, 'w') as f:
                positions_json = {}
                for symbol, trade in self.open_positions.items():
                    positions_json[symbol] = {
                        'entry_price': float(trade.get('entry_price', 0)),
                        'entry_time': str(trade.get('entry_time', '')),
                        'symbol': trade.get('symbol', ''),
                        'side': trade.get('side', ''),
                        'quantity': float(trade.get('quantity', 0)),
                        'sl_price': float(trade.get('sl_price', 0)),
                        'days_held': int(trade.get('days_held', 0)),
                        'sl_order_id': trade.get('sl_order_id'),
                    }
                json.dump(positions_json, f, indent=2)
                logger.debug(f"Saved {len(positions_json)} open positions")
        except Exception as e:
            logger.error(f"Failed to save positions state: {e}")
    
    def _load_positions(self):
        """Load open positions from JSON file on startup."""
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
                    except Exception:
                        entry_time = None
                    
                    positions[symbol] = {
                        'entry_price': trade_data.get('entry_price'),
                        'entry_time': entry_time,
                        'symbol': trade_data.get('symbol'),
                        'side': trade_data.get('side'),
                        'quantity': trade_data.get('quantity'),
                        # Schema migration: prefer new sl_price, fall back to legacy sl
                        'sl_price': trade_data.get('sl_price') or trade_data.get('sl'),
                        # Schema migration: prefer new days_held, fall back to legacy bars_held
                        'days_held': trade_data.get('days_held', trade_data.get('bars_held', 0)),
                        'sl_order_id': trade_data.get('sl_order_id'),
                    }
                logger.info(f"Loaded {len(positions)} open positions from state file")
                return positions
        except Exception as e:
            logger.error(f"Failed to load positions state: {e}")
            return {}
    
    def _calculate_days_held(self, entry_time: datetime) -> int:
        """Calculate days held since entry (Variant C is daily timeframe)."""
        if not entry_time:
            return 0
        try:
            duration = datetime.utcnow() - entry_time
            days = int(duration.total_seconds() / 86400)  # 86400 = 1 day in seconds
            return max(0, days)
        except Exception:
            return 0
    
    def get_account_balance(self) -> Tuple[bool, float, str]:
        """Get current account equity."""
        try:
            success, balance, error = self.kraken.get_balance()
            return success, balance, error
        except Exception as e:
            return False, 0, str(e)
    
    def _handle_sunday_entry(self, symbol: str, price: float) -> Tuple[Dict, int]:
        """
        Handle SUNDAY_ENTRY signal — Variant C entry trigger.
        
        Calendar-driven entry: unconditional market buy on Sunday close UTC,
        with exchange-side SL placed at entry × (1 - sl_pct), default 3% below.
        
        Returns:
            (response_dict, http_status_code)
        """
        # Get account balance FIRST (needed for risk gates and sizing)
        success, equity, error = self.get_account_balance()
        if not success:
            logger.error(f"Failed to get balance: {error}")
            self.alerter.alert_error(
                "BALANCE_FETCH_FAILED",
                f"Could not fetch balance for SUNDAY_ENTRY {symbol}: {error}"
            )
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
        
        # Calculate SL: Variant C uses fixed % from entry (config-driven, default 3%).
        # Long-only: SL is below entry.
        sl_price = price * (1 - self.sl_pct)
        sl_distance = price - sl_price
        
        # Position sizing: qty = risk_amount / sl_distance
        try:
            position = self.position_sizer.calculate(
                account_equity=equity,
                entry_price=price,
                stop_loss=sl_price
            )
        except ValueError as ve:
            logger.error(f"Position sizing failed: {ve}")
            return {"status": "rejected", "message": str(ve)}, 400
        
        qty = position["quantity"]
        risk_usd = position["risk_amount"]
        
        if qty <= 0:
            return {"status": "rejected", "message": "Position size invalid"}, 400
        
        # Apply drawdown-reduction multiplier if active
        size_multiplier = self.risk_manager.get_position_size_multiplier()
        if size_multiplier < 1.0:
            qty = qty * size_multiplier
            logger.warning(
                f"Drawdown reduction active: position halved to {qty:.6f} BTC"
            )
        
        # Place market entry (long-only, always 'buy')
        try:
            success, order, error = self.kraken.place_market_order(
                symbol=symbol,
                side='buy',
                quantity=qty
            )
            
            if not success:
                logger.error(f"Entry order failed: {error}")
                self.alerter.alert_error(
                    "ENTRY_ORDER_FAILED",
                    f"SUNDAY_ENTRY {symbol} qty={qty}: {error}"
                )
                return {"status": "error", "message": error}, 500
            
            # Use actual fill price if available; fall back to TV-reported price
            entry_price = order.get('average') or price
            
            # Recalculate SL based on actual entry fill (slippage adjustment)
            sl_price = entry_price * (1 - self.sl_pct)
            sl_distance = entry_price - sl_price
            
            # Build trade record
            trade = {
                'entry_price': entry_price,
                'entry_time': datetime.utcnow(),
                'symbol': symbol,
                'side': 'LONG',  # Variant C is long-only
                'quantity': qty,
                'sl_price': sl_price,
                'days_held': 0,
            }
            self.open_positions[symbol] = trade
            self._save_positions()
            
            # Place exchange-side stop-loss order on Kraken (long → sell on SL)
            sl_success, sl_order, sl_error = self.kraken.place_stop_loss_order(
                symbol=symbol,
                side='sell',
                quantity=qty,
                stop_price=sl_price
            )
            
            if sl_success:
                trade['sl_order_id'] = sl_order['sl_order_id']
                self._save_positions()
                logger.info(f"✓ Exchange SL placed @ {sl_price:.2f}")
            else:
                logger.error(f"⚠️ Exchange SL placement failed: {sl_error}")
                trade['sl_order_id'] = None
                self.alerter.alert_risk_event(
                    "warning",
                    f"⚠️ SL Placement Failed for {symbol}: {sl_error}\n"
                    f"Position is OPEN WITHOUT SL. Manual review required."
                )
                self._save_positions()
            
            # Send Telegram entry alert
            self.alerter.alert_sunday_entry(trade)
            
            # Update risk manager (increment daily trade counter)
            if not self.risk_manager.record_trade_entry():
                logger.warning(
                    "record_trade_entry returned False after entry+SL placement — "
                    "daily limit race condition?"
                )
            
            logger.info(
                f"✓ SUNDAY ENTRY: {symbol} @ {entry_price:.2f} | "
                f"SL: {sl_price:.2f} | Qty: {qty:.6f} | Risk: ${risk_usd:.2f}"
            )
            return {"status": "success", "entry_price": entry_price}, 200
        
        except Exception as e:
            logger.error(f"Entry handler exception: {str(e)}")
            return {"status": "error", "message": str(e)}, 500
    
    def _handle_monday_exit(self, symbol: str) -> Tuple[Dict, int]:
        """
        Handle MONDAY_EXIT signal — Variant C scheduled exit trigger.
        
        If position is still open: market sell to close.
        If exchange SL already triggered: detect via Kraken error and treat
        as a successful SL_HIT exit (use ticker price for P&L calc).
        
        Returns:
            (response_dict, http_status_code)
        """
        if symbol not in self.open_positions:
            return {"status": "rejected", "message": "No open position"}, 404
        
        trade = self.open_positions[symbol]
        exit_reason = 'MONDAY_EXIT'  # Default; flipped to SL_HIT if detected below
        
        try:
            # Cancel exchange SL order if it exists.
            # If SL already filled, cancel will fail — that's the SL_HIT signal.
            if trade.get('sl_order_id'):
                logger.info(f"Cancelling exchange SL order {trade['sl_order_id']}")
                cancel_success, cancel_error = self.kraken.cancel_order(
                    trade['sl_order_id'], symbol
                )
                if not cancel_success:
                    # Common case: SL already filled. Less common: real cancel failure.
                    # We'll detect which by attempting the close below.
                    logger.warning(
                        f"SL cancel returned error (likely SL already filled): {cancel_error}"
                    )
            else:
                logger.warning(
                    f"No sl_order_id in position dict for {symbol} — "
                    f"skipping SL cancel, attempting close anyway"
                )
            
            # Attempt to close the position (long → sell)
            success, order, error = self.kraken.place_market_order(
                symbol, 'sell', trade['quantity']
            )
            
            if not success:
                # If Kraken reports no position, exchange SL already closed it.
                if 'No open position' in error or 'already closed' in error:
                    logger.info(
                        f"Exchange SL already triggered for {symbol} — "
                        f"treating as successful SL_HIT exit"
                    )
                    exit_reason = 'SL_HIT'
                    
                    # Use last ticker price as exit price (best estimate)
                    try:
                        ticker_ok, ticker, ticker_err = self.kraken.get_ticker(symbol)
                        if ticker_ok:
                            exit_price = ticker.get('last', trade['entry_price'])
                        else:
                            # Last resort: use SL price (worst-case for P&L estimate)
                            exit_price = trade['sl_price']
                    except Exception:
                        exit_price = trade['sl_price']
                    
                    order = {'close_price': exit_price}
                    success = True
                else:
                    # Real error — entry order placed but exit failed. Loud alert.
                    self.alerter.alert_error(
                        "EXIT_ORDER_FAILED",
                        f"Close {symbol} qty={trade['quantity']}: {error}"
                    )
                    return {"status": "error", "message": error}, 500
            
            # Determine exit price (fill price preferred, fall back to ticker)
            exit_price = order.get('average') or order.get('close_price')
            
            if not exit_price or exit_price == 0:
                try:
                    ticker_ok, ticker, ticker_err = self.kraken.get_ticker(symbol)
                    if ticker_ok:
                        exit_price = ticker.get('last', trade['entry_price'])
                        logger.warning(
                            f"Exit fill price unavailable for {symbol}, "
                            f"using last ticker: {exit_price}"
                        )
                    else:
                        logger.error(
                            f"Exit ticker fallback failed: {ticker_err}, "
                            f"using entry price"
                        )
                        exit_price = trade['entry_price']
                except Exception as e:
                    logger.error(f"Exit fallback price lookup failed: {e}")
                    exit_price = trade['entry_price']
            
            # Calculate P&L (long-only)
            pnl_usd = (exit_price - trade['entry_price']) * trade['quantity']
            pnl_pct = ((exit_price - trade['entry_price']) / trade['entry_price']) * 100
            
            # Calculate days held
            days_held = self._calculate_days_held(trade['entry_time'])
            
            # Update risk manager — fetch fresh equity for accurate drawdown calc
            balance_ok, current_equity, balance_err = self.kraken.get_balance()
            if not balance_ok:
                logger.warning(
                    f"Could not fetch balance for risk manager update: {balance_err}"
                )
                # Rough fallback estimate
                current_equity = trade['entry_price'] * trade['quantity']
            
            risk_status = self.risk_manager.record_trade_exit(pnl_usd, current_equity)
            
            # Enrich trade dict with exit info for logging and alerts
            trade['timestamp'] = datetime.now().isoformat()
            trade['exit_price'] = exit_price
            trade['exit_type'] = exit_reason
            trade['p&l_usd'] = pnl_usd
            trade['p&l_pct'] = pnl_pct
            trade['days_held'] = days_held
            
            # Log trade to CSV
            self.logger.log_trade(trade)
            
            # Dispatch the right exit alert
            if exit_reason == 'SL_HIT':
                self.alerter.alert_sl_hit(trade)
            else:
                self.alerter.alert_monday_exit(trade)
            
            # Layered loss alerts — risk_manager flags tell us what to fire
            if risk_status.get('losses_warning_just_fired'):
                self.alerter.alert_consecutive_loss_warning(
                    risk_status['consecutive_losses']
                )
            if risk_status.get('circuit_break_just_engaged'):
                self.alerter.alert_circuit_break(
                    self.risk_manager.max_consecutive_losses
                )
            
            # Remove from open positions
            del self.open_positions[symbol]
            self._save_positions()
            
            logger.info(
                f"✓ EXIT ({exit_reason}): {symbol} @ {exit_price:.2f} | "
                f"P&L: ${pnl_usd:.2f} ({pnl_pct:.2f}%) | days_held: {days_held}"
            )
            return {
                "status": "success",
                "exit_price": exit_price,
                "pnl": pnl_usd,
                "exit_type": exit_reason,
            }, 200
        
        except Exception as e:
            logger.error(f"Exit handler exception: {str(e)}")
            return {"status": "error", "message": str(e)}, 500
    
    def _process_signal_async(self, parsed: Dict):
        """
        Background-thread worker for processing a parsed signal.
        
        Called by the Flask /webhook route after it has validated and parsed
        the payload synchronously and returned 200 to TradingView. Runs the
        SUNDAY_ENTRY / MONDAY_EXIT handlers, and turns any failure
        (non-200 response or unhandled exception) into a Telegram alert so
        Ash hears about silent infra failures.
        
        Deliberately does NOT alert on 403 (risk-gate rejection) or 409
        (duplicate position) — those are the system working as designed,
        not infra failures.
        
        Args:
            parsed: dict from signal_parser.parse(payload), already validated.
                    Expected keys: symbol, action, price.
        """
        action = parsed.get('action', 'UNKNOWN')
        symbol = parsed.get('symbol', 'UNKNOWN')
        try:
            price = parsed.get('price') or 0
            
            if action == 'SUNDAY_ENTRY':
                response, status = self._handle_sunday_entry(symbol, price)
            elif action == 'MONDAY_EXIT':
                response, status = self._handle_monday_exit(symbol)
            else:
                # Should be unreachable — signal_parser already validated action set
                response, status = {
                    "status": "rejected",
                    "message": f"Unknown action: {action}"
                }, 400
            
            # Alert on infra failures (5xx) but not on policy rejections (4xx).
            # Specifically: 403 = risk-gate, 409 = duplicate-position — these
            # are the system working correctly. 500 = something genuinely broke.
            if status >= 500:
                msg = response.get('message', 'unknown error') if isinstance(response, dict) else str(response)
                # Note: handlers already fire their own alert_error() at the
                # specific failure site, so this catch-all mostly handles
                # unexpected 500s from elsewhere in the path.
                logger.error(f"Async signal failed: {action} {symbol} status={status} msg={msg}")
        
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"Async signal processing crashed: {action} {symbol}: {e}\n{tb}")
            try:
                self.alerter.alert_error(
                    f"UNHANDLED_{action}",
                    f"{symbol}: {type(e).__name__}: {str(e)[:200]}"
                )
            except Exception as alert_err:
                # If even the alerter blew up, log and move on — don't crash
                # the background thread.
                logger.error(f"Failed to send unhandled-error alert: {alert_err}")


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
            "strategy": "variant_c_calendar",
            "balance": balance,
            "open_positions": len(bot.open_positions),
            "timestamp": datetime.utcnow().isoformat()
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/webhook', methods=['POST'])
def webhook():
    """
    TradingView webhook receiver — ASYNC.
    
    Validates and parses the payload synchronously (~50ms), then spawns a
    daemon thread to do the slow Kraken work. Returns 200 to TV immediately
    so we don't blow TV's 3-5s timeout.
    
    Single-worker gunicorn (--workers 1) makes threading.Thread safe for
    state mutation in handlers. Don't scale workers without revisiting that.
    
    Expected payload (Variant C):
        {"symbol": "BTCUSD", "action": "SUNDAY_ENTRY", "price": 78400.00}
        {"symbol": "BTCUSD", "action": "MONDAY_EXIT",  "price": 78850.00}
    """
    if bot is None:
        return jsonify({"status": "offline", "message": "Bot not initialized"}), 500
    
    try:
        payload = request.get_json()
        if not payload:
            return jsonify({"status": "error", "message": "Empty payload"}), 400
        
        # Parse synchronously — cheap, no network I/O. Reject obvious garbage
        # before we accept the webhook.
        success, parsed, error = bot.signal_parser.parse(payload)
        if not success:
            logger.warning(f"Webhook rejected (parse failed): {error}")
            return jsonify({"status": "rejected", "message": error}), 400
        
        # Hand off the slow part (Kraken API calls, order placement, etc.)
        # to a background thread so we can return 200 to TV in ~50ms.
        thread = threading.Thread(
            target=bot._process_signal_async,
            args=(parsed,),
            daemon=True,
            name=f"signal-{parsed.get('action','?')}-{parsed.get('symbol','?')}"
        )
        thread.start()
        
        return jsonify({
            "status": "accepted",
            "action": parsed.get('action'),
            "symbol": parsed.get('symbol'),
        }), 200
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/resume', methods=['POST'])
def resume():
    """
    Manual resume after a circuit-break pause.
    
    Clears the paused_until_manual_resume flag and resets the consecutive
    loss counter. Idempotent — safe to POST multiple times.
    """
    if bot is None:
        return jsonify({"status": "offline", "message": "Bot not initialized"}), 500
    
    try:
        success, message = bot.risk_manager.manual_resume()
        if success:
            bot.alerter.alert_manual_resume()
        return jsonify({
            "status": "success" if success else "noop",
            "message": message
        }), 200
    except Exception as e:
        logger.error(f"Resume endpoint error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/status', methods=['GET'])
def status():
    """Status endpoint — bot health, balance, risk state."""
    if bot is None:
        return jsonify({"status": "offline"}), 500
    
    try:
        success, balance, error = bot.get_account_balance()
        risk_status = bot.risk_manager.get_status()
        
        return jsonify({
            "status": "online",
            "strategy": "variant_c_calendar",
            "balance": balance,
            "open_positions": len(bot.open_positions),
            "trades_today": risk_status.get('trades_today', 0),
            "consecutive_losses": risk_status.get('consecutive_losses', 0),
            "paused": risk_status.get('paused', False),
            "position_size_multiplier": risk_status.get('position_size_multiplier', 1.0),
            "drawdown_reduction_active": risk_status.get('drawdown_reduction_active', False),
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

# DEPLOYMENT MARKER: variant-c-calendar-2026-05-03
