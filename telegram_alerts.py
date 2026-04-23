"""
Telegram Alerts
Send trade notifications and alerts via Telegram bot.
"""

import logging
import requests
from typing import Dict, Optional

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot"


class TelegramAlerter:
    """Send Telegram notifications for trades and events."""
    
    def __init__(self, bot_token: str, chat_id: str):
        """
        Initialize Telegram alerter.
        
        Args:
            bot_token: Telegram bot token
            chat_id: Telegram chat ID
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"{TELEGRAM_API}{bot_token}/sendMessage"
    
    def _send_message(self, text: str) -> bool:
        """
        Send raw message to Telegram.
        
        Args:
            text: Message text (plain or HTML)
            
        Returns:
            True if sent successfully
        """
        try:
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
            }
            response = requests.post(self.api_url, json=payload, timeout=5)
            
            if response.status_code == 200:
                logger.debug(f"✓ Telegram message sent")
                return True
            else:
                logger.error(f"Telegram error {response.status_code}: {response.text}")
                return False
        
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {str(e)}")
            return False
    
    def alert_entry_long(self, trade: Dict) -> bool:
        """Alert for LONG entry."""
        symbol = trade["symbol"]
        price = trade["entry_price"]
        sl = trade["sl"]
        tp = trade["tp"]
        
        message = (
            f"🟢 <b>LONG ENTRY</b> {symbol}\n"
            f"Entry: ${price:.2f}\n"
            f"SL: ${sl:.2f}\n"
            f"TP: ${tp:.2f}\n"
        )
        return self._send_message(message)
    
    def alert_entry_short(self, trade: Dict) -> bool:
        """Alert for SHORT entry."""
        symbol = trade["symbol"]
        price = trade["entry_price"]
        sl = trade["sl"]
        tp = trade["tp"]
        
        message = (
            f"🔴 <b>SHORT ENTRY</b> {symbol}\n"
            f"Entry: ${price:.2f}\n"
            f"SL: ${sl:.2f}\n"
            f"TP: ${tp:.2f}\n"
        )
        return self._send_message(message)
    
    def alert_exit_hardstop(self, trade: Dict) -> bool:
        """Alert for HARD STOP exit."""
        symbol = trade["symbol"]
        exit_price = trade["exit_price"]
        pnl = trade["p&l_usd"]
        pnl_pct = trade["p&l_pct"]
        
        pnl_emoji = "📉" if pnl < 0 else "📈"
        
        message = (
            f"🛑 <b>HARD STOP</b> {symbol}\n"
            f"Exit: ${exit_price:.2f}\n"
            f"{pnl_emoji} P&L: ${pnl:.2f} ({pnl_pct:.2f}%)\n"
        )
        return self._send_message(message)
    
    def alert_exit_softstop(self, trade: Dict) -> bool:
        """Alert for SOFT STOP exit (RSI exhausted)."""
        symbol = trade["symbol"]
        exit_price = trade["exit_price"]
        pnl = trade["p&l_usd"]
        pnl_pct = trade["p&l_pct"]
        
        pnl_emoji = "📉" if pnl < 0 else "📈"
        
        message = (
            f"⚠️ <b>SOFT STOP</b> {symbol}\n"
            f"RSI Exhausted Exit: ${exit_price:.2f}\n"
            f"{pnl_emoji} P&L: ${pnl:.2f} ({pnl_pct:.2f}%)\n"
        )
        return self._send_message(message)
    
    def alert_exit_takeprofit(self, trade: Dict) -> bool:
        """Alert for TAKE PROFIT exit."""
        symbol = trade["symbol"]
        tp = trade["tp"]
        exit_price = trade["exit_price"]
        pnl = trade["p&l_usd"]
        pnl_pct = trade["p&l_pct"]
        
        message = (
            f"💰 <b>TAKE PROFIT</b> {symbol}\n"
            f"TP Hit: ${tp:.2f}\n"
            f"Exit: ${exit_price:.2f}\n"
            f"✅ P&L: ${pnl:.2f} ({pnl_pct:.2f}%)\n"
        )
        return self._send_message(message)
    
    def alert_exit_timeout(self, trade: Dict) -> bool:
        """Alert for TIMEOUT exit (12H+ held)."""
        symbol = trade["symbol"]
        exit_price = trade["exit_price"]
        bars_held = trade.get("bars_held", 0)
        pnl = trade["p&l_usd"]
        pnl_pct = trade["p&l_pct"]
        
        pnl_emoji = "📉" if pnl < 0 else "📈"
        
        message = (
            f"⏱️ <b>TIMEOUT</b> {symbol}\n"
            f"Held: {bars_held} bars (12H+)\n"
            f"Exit: ${exit_price:.2f}\n"
            f"{pnl_emoji} P&L: ${pnl:.2f} ({pnl_pct:.2f}%)\n"
        )
        return self._send_message(message)
    
    def alert_risk_event(self, event_type: str, message: str) -> bool:
        """
        Alert for risk management events.
        
        Args:
            event_type: "pause" | "drawdown" | "hardstop" | "warning"
            message: Event message
        """
        emoji_map = {
            "pause": "⏸️",
            "drawdown": "⚠️",
            "hardstop": "🛑",
            "warning": "⚡",
        }
        
        emoji = emoji_map.get(event_type, "📢")
        full_message = f"{emoji} <b>RISK EVENT</b>\n{message}"
        return self._send_message(full_message)
    
    def alert_error(self, error_type: str, error_msg: str) -> bool:
        """Alert for critical errors."""
        message = f"❌ <b>ERROR: {error_type}</b>\n{error_msg}"
        return self._send_message(message)
    
    def alert_status(self, status: Dict) -> bool:
        """Alert with bot status/stats."""
        message = (
            f"📊 <b>BOT STATUS</b>\n"
            f"Trades today: {status.get('trades_today', 0)}\n"
            f"Daily P&L: ${status.get('daily_pnl', 0):.2f}\n"
            f"Drawdown: {status.get('drawdown', 0):.1f}%\n"
        )
        return self._send_message(message)
