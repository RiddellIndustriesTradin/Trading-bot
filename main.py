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

# OSF v1.0 modules (Strategy #2 — Options Settlement Flow)
from options_data import OptionsDataClient, OptionsDataError
from osf_handler import OSFHandler, OSFConfig, BotState, parse_signal_dict

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
        
        # Open positions tracker (V-C)
        self.positions_state_file = "positions_state.json"
        self.open_positions = self._load_positions()
        self.last_updated = None
        
        # Variant C strategy params (cached for hot-path access)
        self.sl_pct = self.config['strategy']['sl_pct']  # 0.03
        
        # ─── OSF v1.0 initialization ────────────────────────────────────
        # Separate position dict from V-C per locked decision Q1 (Option C).
        # Sequential lockout enforced in handler — only one strategy holds
        # a position at any time across V-C + OSF combined.
        osf_cfg_raw = self.config.get('osf', {})
        self.osf_enabled = osf_cfg_raw.get('enabled', False)
        
        # Build OSFConfig dataclass from yaml config
        self.osf_config = OSFConfig(
            max_pain_threshold_pct=osf_cfg_raw.get('max_pain_threshold_pct', 0.02),
            oi_threshold_billions=osf_cfg_raw.get('oi_threshold_billions', 3.0),
            risk_per_trade_pct=osf_cfg_raw.get('risk_per_trade', 0.005),
            stop_loss_pct=osf_cfg_raw.get('sl_pct', 0.03),
            symbol=osf_cfg_raw.get('symbol', 'BTCUSD'),
            strategy_id=osf_cfg_raw.get('strategy_id', 'OSF_v1'),
        )
        
        # Build options data client with config-driven API params
        api_cfg = osf_cfg_raw.get('api', {})
        self.options_client = OptionsDataClient(
            timeout_seconds=api_cfg.get('timeout_seconds', 10),
            max_retries=api_cfg.get('max_retries', 3),
            retry_backoff_seconds=api_cfg.get('retry_backoff_seconds', 2),
        )
        
        # OSF handler instance (decision module)
        self.osf_handler = OSFHandler(
            config=self.osf_config,
            options_client=self.options_client,
        )
        
        # OSF positions dict (parallel to V-C's open_positions)
        # Keyed by symbol same as V-C; only ever holds at most 1 entry
        self.osf_positions_state_file = "osf_positions_state.json"
        self.osf_positions = self._load_osf_positions()
        
        logger.info(
            f"✓ Trading Bot initialized — "
            f"Variant C calendar strategy, SL {self.sl_pct*100:.1f}%, "
            f"risk {self.position_sizer.risk_per_trade*100:.2f}%/trade"
        )
        logger.info(
            f"✓ OSF v1.0 strategy {'ENABLED' if self.osf_enabled else 'DISABLED'} — "
            f"max_pain≥{self.osf_config.max_pain_threshold_pct*100:.1f}%, "
            f"OI≥${self.osf_config.oi_threshold_billions:.1f}B, "
            f"risk {self.osf_config.risk_per_trade_pct*100:.2f}%/trade"
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
    
    def _save_osf_positions(self):
        """Persist OSF open positions to JSON file.
        
        Parallels _save_positions() but uses a separate file so V-C and OSF
        state never interfere. Per locked decision Q1 (Option C — total
        separation between strategies).
        """
        try:
            with open(self.osf_positions_state_file, 'w') as f:
                positions_json = {}
                for symbol, trade in self.osf_positions.items():
                    positions_json[symbol] = {
                        'entry_price': float(trade.get('entry_price', 0)),
                        'entry_time': str(trade.get('entry_time', '')),
                        'symbol': trade.get('symbol', ''),
                        'side': trade.get('side', ''),
                        'quantity': float(trade.get('quantity', 0)),
                        'sl_price': float(trade.get('sl_price', 0)),
                        'days_held': int(trade.get('days_held', 0)),
                        'sl_order_id': trade.get('sl_order_id'),
                        # OSF-specific: context dict from decision (max pain, OI, etc)
                        # Preserved across restarts so logs/alerts can reference it
                        'context': trade.get('context', {}),
                    }
                json.dump(positions_json, f, indent=2)
                logger.debug(f"Saved {len(positions_json)} OSF open positions")
        except Exception as e:
            logger.error(f"Failed to save OSF positions state: {e}")
    
    def _load_osf_positions(self):
        """Load OSF open positions from JSON file on startup.
        
        Parallels _load_positions() but uses a separate file.
        """
        if not os.path.exists(self.osf_positions_state_file):
            return {}
        
        try:
            with open(self.osf_positions_state_file, 'r') as f:
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
                        'sl_price': trade_data.get('sl_price'),
                        'days_held': trade_data.get('days_held', 0),
                        'sl_order_id': trade_data.get('sl_order_id'),
                        'context': trade_data.get('context', {}),
                    }
                logger.info(f"Loaded {len(positions)} OSF open positions from state file")
                return positions
        except Exception as e:
            logger.error(f"Failed to load OSF positions state: {e}")
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
    
    def _handle_osf_entry(self, symbol: str, price: float) -> Tuple[Dict, int]:
        """
        Handle OSF_ENTRY_REQUEST signal — Strategy #2 entry evaluation.
        
        Unlike V-C's unconditional Sunday entry, OSF performs conditional
        evaluation:
          1. Sequential lockout check (V-C must not be in position)
          2. Risk gates (drawdown, paused, etc — shared risk_manager)
          3. Deribit API query for max pain + OI at upcoming Friday expiry
          4. Entry condition check (max pain ≥ 2% above price, OI ≥ $3B)
          5. Position sizing using shared formula
          6. Market buy + exchange SL placement
        
        All decision logic lives in osf_handler.OSFHandler.evaluate() —
        this method just wires it into the bot's execution layer.
        
        Returns:
            (response_dict, http_status_code)
        """
        # Master enable check (config-gated, defaults to false)
        if not self.osf_enabled:
            logger.info(f"OSF_ENTRY_REQUEST received but OSF disabled in config")
            self.alerter.alert_osf_skip(
                reason="OSF disabled in config (enabled: false)",
                context={'underlying_price': price},
            )
            return {"status": "rejected", "message": "OSF strategy disabled"}, 403
        
        # Get account balance FIRST (needed for risk gates and sizing)
        success, equity, error = self.get_account_balance()
        if not success:
            logger.error(f"Failed to get balance: {error}")
            self.alerter.alert_error(
                "BALANCE_FETCH_FAILED",
                f"Could not fetch balance for OSF_ENTRY_REQUEST {symbol}: {error}"
            )
            return {"status": "error", "message": "Balance query failed"}, 500
        
        # Check shared risk gates (paused, daily loss limit, hard-stop DD)
        can_trade, reason = self.risk_manager.can_trade(equity)
        if not can_trade:
            logger.warning(f"OSF entry blocked by risk_manager: {reason}")
            self.alerter.alert_osf_skip(reason=reason)
            return {"status": "rejected", "message": reason}, 403
        
        # Build BotState dataclass for osf_handler (it owns decision logic;
        # bot owns state, per locked Q3 architecture decision)
        state = BotState(
            vc_position_open=len(self.open_positions) > 0,
            osf_position_open=len(self.osf_positions) > 0,
            account_equity_usd=equity,
            bot_paused=self.risk_manager.state.get('paused_until_manual_resume', False),
            drawdown_active=self.risk_manager.state.get('drawdown_reduction_active', False),
        )
        
        # Build the signal dataclass the handler expects
        try:
            osf_signal = parse_signal_dict({
                'symbol': symbol,
                'side': 'long',
                'signal_type': 'OSF_ENTRY_REQUEST',
                'price': price,
                'timestamp': datetime.utcnow().isoformat(),
                'strategy': 'OSF_v1',
            })
        except Exception as e:
            logger.error(f"Failed to build OSF signal dataclass: {e}")
            return {"status": "error", "message": f"Signal build failed: {e}"}, 500
        
        # Delegate decision to handler (queries Deribit API, evaluates conditions)
        decision = self.osf_handler.evaluate(osf_signal, state)
        
        logger.info(f"OSF decision: {decision}")
        
        # Decision returned SKIP — alert the user and stop here
        if not decision.should_execute:
            # Differentiate API failure vs business-logic skip in alerts
            if 'unavailable' in decision.reason.lower() or 'api' in decision.reason.lower():
                self.alerter.alert_osf_api_failure(decision.reason)
            else:
                self.alerter.alert_osf_skip(
                    reason=decision.reason,
                    context=decision.context,
                )
            return {"status": "skip", "message": decision.reason}, 200
        
        # Decision says EXECUTE BUY — proceed with order placement
        if decision.action != 'BUY':
            # Defensive: entry path should only ever return BUY or SKIP
            logger.error(f"OSF entry handler got unexpected action: {decision.action}")
            return {"status": "error", "message": f"Unexpected action: {decision.action}"}, 500
        
        qty = decision.quantity_btc
        sl_price = decision.stop_loss_price
        
        # Apply drawdown-reduction multiplier if active (shared with V-C)
        size_multiplier = self.risk_manager.get_position_size_multiplier()
        if size_multiplier < 1.0:
            qty = qty * size_multiplier
            qty = round(qty, 4)  # Match Kraken tick size
            logger.warning(
                f"Drawdown reduction active: OSF position halved to {qty:.6f} BTC"
            )
        
        # Place market entry (long-only, always 'buy')
        try:
            success, order, error = self.kraken.place_market_order(
                symbol=symbol,
                side='buy',
                quantity=qty,
            )
            
            if not success:
                logger.error(f"OSF entry order failed: {error}")
                self.alerter.alert_error(
                    "OSF_ENTRY_ORDER_FAILED",
                    f"OSF_ENTRY_REQUEST {symbol} qty={qty}: {error}"
                )
                return {"status": "error", "message": error}, 500
            
            # Use actual fill price if available; fall back to Pine-reported price
            entry_price = order.get('average') or price
            
            # Recalculate SL based on actual entry fill (slippage adjustment)
            sl_price = round(entry_price * (1 - self.osf_config.stop_loss_pct), 2)
            
            # Build trade record (parallels V-C trade dict structure)
            trade = {
                'entry_price': entry_price,
                'entry_time': datetime.utcnow(),
                'symbol': symbol,
                'side': 'LONG',  # OSF is long-only spot (same as V-C)
                'quantity': qty,
                'sl_price': sl_price,
                'days_held': 0,
                'context': decision.context,  # Preserve max pain/OI for logging
            }
            self.osf_positions[symbol] = trade
            self._save_osf_positions()
            
            # Place exchange-side stop-loss order on Kraken (long → sell on SL)
            sl_success, sl_order, sl_error = self.kraken.place_stop_loss_order(
                symbol=symbol,
                side='sell',
                quantity=qty,
                stop_price=sl_price,
            )
            
            if sl_success:
                trade['sl_order_id'] = sl_order['sl_order_id']
                self._save_osf_positions()
                logger.info(f"✓ OSF exchange SL placed @ {sl_price:.2f}")
            else:
                logger.error(f"⚠️ OSF exchange SL placement failed: {sl_error}")
                trade['sl_order_id'] = None
                self.alerter.alert_risk_event(
                    "warning",
                    f"⚠️ OSF SL Placement Failed for {symbol}: {sl_error}\n"
                    f"Position is OPEN WITHOUT SL. Manual review required."
                )
                self._save_osf_positions()
            
            # Send OSF entry alert with setup context block
            self.alerter.alert_osf_entry(trade, context=decision.context)
            
            # Update shared risk manager (increment daily trade counter)
            # NB: This counter is shared with V-C — max_daily_trades is now 2
            if not self.risk_manager.record_trade_entry():
                logger.warning(
                    "record_trade_entry returned False after OSF entry+SL placement — "
                    "daily limit race condition?"
                )
            
            logger.info(
                f"✓ OSF ENTRY: {symbol} @ {entry_price:.2f} | "
                f"SL: {sl_price:.2f} | Qty: {qty:.6f} | "
                f"Max pain: ${decision.context.get('max_pain_usd', 0):,.0f} "
                f"(+{decision.context.get('distance_pct', 0)*100:.2f}%) | "
                f"OI: ${decision.context.get('oi_billions', 0):.2f}B"
            )
            return {"status": "success", "entry_price": entry_price}, 200
        
        except Exception as e:
            logger.error(f"OSF entry handler exception: {str(e)}")
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
    
    def _handle_osf_exit_time(self, symbol: str) -> Tuple[Dict, int]:
        """
        Handle OSF_EXIT_TIME signal — Strategy #2 scheduled exit trigger.
        
        Pine fires this on Friday close UTC unconditionally; bot filters
        based on whether OSF position is actually open (per locked Q3
        architecture decision — bot is source of truth on position state).
        
        Mirrors _handle_monday_exit pattern:
          - If no OSF position: log + idempotent skip (not an error)
          - If position open: cancel SL, market sell, compute P&L, alert
          - SL-already-filled edge case: detect via Kraken error, treat as
            successful SL_HIT exit (use ticker for P&L estimate)
        
        Returns:
            (response_dict, http_status_code)
        """
        # No OSF position open — idempotent skip (Pine fires every Friday)
        if symbol not in self.osf_positions:
            logger.info(f"OSF_EXIT_TIME received but no open OSF position for {symbol}")
            return {"status": "rejected", "message": "No open OSF position"}, 404
        
        trade = self.osf_positions[symbol]
        exit_reason = 'OSF_EXIT_TIME'  # Default; flipped to OSF_SL_HIT if detected below
        
        try:
            # Cancel exchange SL order if it exists.
            # If SL already filled, cancel will fail — that's the SL_HIT signal.
            if trade.get('sl_order_id'):
                logger.info(f"Cancelling OSF exchange SL order {trade['sl_order_id']}")
                cancel_success, cancel_error = self.kraken.cancel_order(
                    trade['sl_order_id'], symbol
                )
                if not cancel_success:
                    # Common case: SL already filled. Less common: real cancel failure.
                    # We'll detect which by attempting the close below.
                    logger.warning(
                        f"OSF SL cancel returned error (likely SL already filled): {cancel_error}"
                    )
            else:
                logger.warning(
                    f"No sl_order_id in OSF position dict for {symbol} — "
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
                        f"OSF exchange SL already triggered for {symbol} — "
                        f"treating as successful OSF_SL_HIT exit"
                    )
                    exit_reason = 'OSF_SL_HIT'
                    
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
                    # Real error — entry placed but exit failed. Loud alert.
                    self.alerter.alert_error(
                        "OSF_EXIT_ORDER_FAILED",
                        f"Close OSF {symbol} qty={trade['quantity']}: {error}"
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
                            f"OSF exit fill price unavailable for {symbol}, "
                            f"using last ticker: {exit_price}"
                        )
                    else:
                        logger.error(
                            f"OSF exit ticker fallback failed: {ticker_err}, "
                            f"using entry price"
                        )
                        exit_price = trade['entry_price']
                except Exception as e:
                    logger.error(f"OSF exit fallback price lookup failed: {e}")
                    exit_price = trade['entry_price']
            
            # Calculate P&L (long-only)
            pnl_usd = (exit_price - trade['entry_price']) * trade['quantity']
            pnl_pct = ((exit_price - trade['entry_price']) / trade['entry_price']) * 100
            
            # Calculate days held (OSF holds ~2 days: Wed close → Fri close)
            days_held = self._calculate_days_held(trade['entry_time'])
            
            # Update shared risk manager — fetch fresh equity for accurate drawdown calc
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
            
            # Log trade to CSV (shared trade log — both strategies write here)
            self.logger.log_trade(trade)
            
            # Dispatch the right OSF exit alert
            if exit_reason == 'OSF_SL_HIT':
                self.alerter.alert_osf_sl_hit(trade)
            else:
                self.alerter.alert_osf_exit_time(trade)
            
            # Layered loss alerts — risk_manager flags tell us what to fire
            # (shared with V-C; consecutive losses count across both strategies)
            if risk_status.get('losses_warning_just_fired'):
                self.alerter.alert_consecutive_loss_warning(
                    risk_status['consecutive_losses']
                )
            if risk_status.get('circuit_break_just_engaged'):
                self.alerter.alert_circuit_break(
                    self.risk_manager.max_consecutive_losses
                )
            
            # Remove from OSF open positions
            del self.osf_positions[symbol]
            self._save_osf_positions()
            
            logger.info(
                f"✓ OSF EXIT ({exit_reason}): {symbol} @ {exit_price:.2f} | "
                f"P&L: ${pnl_usd:.2f} ({pnl_pct:.2f}%) | days_held: {days_held}"
            )
            return {
                "status": "success",
                "exit_price": exit_price,
                "pnl": pnl_usd,
                "exit_type": exit_reason,
            }, 200
        
        except Exception as e:
            logger.error(f"OSF exit handler exception: {str(e)}")
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
            elif action == 'OSF_ENTRY_REQUEST':
                # OSF Strategy #2 — bot evaluates conditions before executing
                response, status = self._handle_osf_entry(symbol, price)
            elif action == 'OSF_EXIT_TIME':
                # OSF Strategy #2 — Pine fires unconditionally, bot filters
                response, status = self._handle_osf_exit_time(symbol)
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
            "strategy": "variant_c_calendar + OSF_v1",
            "balance": balance,
            "vc_open_positions": len(bot.open_positions),
            "osf_open_positions": len(bot.osf_positions),
            "osf_enabled": bot.osf_enabled,
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
    """Status endpoint — bot health, balance, risk state, OSF state."""
    if bot is None:
        return jsonify({"status": "offline"}), 500
    
    try:
        success, balance, error = bot.get_account_balance()
        risk_status = bot.risk_manager.get_status()
        
        return jsonify({
            "status": "online",
            "strategy": "variant_c_calendar + OSF_v1",
            "balance": balance,
            "vc_open_positions": len(bot.open_positions),
            "osf_open_positions": len(bot.osf_positions),
            "osf_enabled": bot.osf_enabled,
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
