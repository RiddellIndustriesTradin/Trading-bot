"""
Telegram Alerts — Variant C Calendar Strategy
Send trade notifications and risk events via Telegram bot.

Variant C-specific alert set:
  Trade lifecycle:    sunday_entry, monday_exit, sl_hit
  Layered losses:     consecutive_loss_warning (3), circuit_break (5)
  Operational:        manual_resume, risk_event, error, status
"""

import logging
import requests
from typing import Dict

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
    
    # ─── Trade lifecycle alerts ──────────────────────────────────────────
    
    def alert_sunday_entry(self, trade: Dict) -> bool:
        """Alert for Sunday entry (Variant C entry trigger)."""
        symbol = trade["symbol"]
        price = trade["entry_price"]
        sl = trade["sl_price"]
        qty = trade.get("quantity", 0)
        
        message = (
            f"🟢 <b>SUNDAY ENTRY</b> {symbol}\n"
            f"Entry: ${price:.2f}\n"
            f"SL: ${sl:.2f} (3% below entry)\n"
            f"Qty: {qty:.6f} BTC\n"
        )
        return self._send_message(message)
    
    def alert_monday_exit(self, trade: Dict) -> bool:
        """Alert for Monday close exit (Variant C scheduled exit)."""
        symbol = trade["symbol"]
        exit_price = trade["exit_price"]
        days_held = trade.get("days_held", 0)
        pnl = trade["p&l_usd"]
        pnl_pct = trade["p&l_pct"]
        
        pnl_emoji = "📉" if pnl < 0 else "📈"
        
        message = (
            f"📅 <b>MONDAY EXIT</b> {symbol}\n"
            f"Exit: ${exit_price:.2f}\n"
            f"Days held: {days_held}\n"
            f"{pnl_emoji} P&L: ${pnl:.2f} ({pnl_pct:.2f}%)\n"
        )
        return self._send_message(message)
    
    def alert_sl_hit(self, trade: Dict) -> bool:
        """Alert for stop-loss hit (Kraken exchange-side fill)."""
        symbol = trade["symbol"]
        exit_price = trade["exit_price"]
        pnl = trade["p&l_usd"]
        pnl_pct = trade["p&l_pct"]
        
        message = (
            f"🛑 <b>SL HIT</b> {symbol}\n"
            f"Exit: ${exit_price:.2f}\n"
            f"📉 P&L: ${pnl:.2f} ({pnl_pct:.2f}%)\n"
        )
        return self._send_message(message)
    
    # ─── Layered consecutive-loss alerts ─────────────────────────────────
    
    def alert_consecutive_loss_warning(self, count: int) -> bool:
        """
        Layer 1: Informational alert at 3 consecutive losses.
        
        At 50% WR, 3 in a row is ~12% probability — within normal variance.
        No action taken; just a heads-up to monitor.
        """
        message = (
            f"⚠️ <b>HEADS UP</b>\n"
            f"{count} consecutive losses on Variant C.\n"
            f"This is within normal variance (~12% probability at 50% WR).\n"
            f"Strategy still active. Monitor next trade.\n"
        )
        return self._send_message(message)
    
    def alert_circuit_break(self, count: int) -> bool:
        """
        Layer 2: Hard pause alert at max consecutive losses (default 5).
        
        At 50% WR, 5 in a row is ~3% probability — statistically rare enough
        to warrant manual review before resuming trading.
        """
        message = (
            f"🛑 <b>CIRCUIT BREAK</b>\n"
            f"{count} consecutive losses — trading PAUSED.\n"
            f"Manual resume required.\n"
            f"Review trade log before re-enabling.\n"
            f"<code>POST /resume</code> to clear pause.\n"
        )
        return self._send_message(message)
    
    def alert_manual_resume(self) -> bool:
        """Confirm manual resume from circuit-break pause."""
        message = (
            f"▶️ <b>RESUMED</b>\n"
            f"Trading resumed manually.\n"
            f"Consecutive loss counter reset.\n"
        )
        return self._send_message(message)
    
    # ─── Operational alerts ──────────────────────────────────────────────
    
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
            f"Consecutive losses: {status.get('consecutive_losses', 0)}\n"
            f"Drawdown: {status.get('drawdown', 0):.1f}%\n"
        )
        return self._send_message(message)

    # ──── OSF lifecycle alerts ────

    def alert_osf_skip(self, reason: str, context: dict = None) -> bool:
        """Alert for OSF signal skipped (disabled / risk gate / decision SKIP)."""
        context = context or {}
        reason_lower = reason.lower()

        # Case 1: OSF disabled in config — terse, no context needed
        if "disabled" in reason_lower:
            message = (
                f"⚪ <b>OSF SKIP</b>\n"
                f"OSF strategy disabled in config\n"
            )
            return self._send_message(message)

        # Case 2: Risk gate triggered — surface the risk reason prominently
        if "risk" in reason_lower or "gate" in reason_lower:
            message = (
                f"⚪ <b>OSF SKIP — Risk Gate</b>\n"
                f"{reason}\n"
            )
            return self._send_message(message)

        # Case 3: Decision SKIP from osf_handler — show options-data context
        message = f"⚪ <b>OSF SKIP</b>\n{reason}\n"
        if context:
            max_pain = context.get("max_pain_usd")
            oi = context.get("oi_billions")
            dist = context.get("distance_pct")
            underlying = context.get("underlying_price")
            if underlying is not None:
                message += f"Price: ${underlying:,.2f}\n"
            if max_pain is not None:
                message += f"Max pain: ${max_pain:,.0f}\n"
            if oi is not None:
                message += f"OI: ${oi:.2f}B\n"
            if dist is not None:
                message += f"Distance: {dist*100:+.2f}%\n"
        return self._send_message(message)

    def alert_osf_api_failure(self, reason: str) -> bool:
        """Alert for OSF options-data API failure (Deribit unreachable, garbage data, etc.)."""
        message = (
            f"⚠️ <b>OSF API FAILURE</b>\n"
            f"{reason}\n"
            f"Signal skipped, no trade placed.\n"
        )
        return self._send_message(message)

    def alert_osf_entry(self, trade: dict, context: dict = None) -> bool:
        """Alert for OSF entry execution (conditional entry — Strategy #2)."""
        context = context or {}
        symbol = trade["symbol"]
        price = trade["entry_price"]
        sl = trade["sl_price"]
        qty = trade.get("quantity", 0)
        message = (
            f"🟢 <b>OSF ENTRY</b> {symbol}\n"
            f"Entry: ${price:,.2f}\n"
            f"SL: ${sl:,.2f} (3% below entry)\n"
            f"Qty: {qty:.6f} BTC\n"
        )
        if context:
            max_pain = context.get("max_pain_usd")
            oi = context.get("oi_billions")
            dist = context.get("distance_pct")
            if max_pain is not None:
                message += f"Max pain: ${max_pain:,.0f}\n"
            if oi is not None:
                message += f"OI: ${oi:.2f}B\n"
            if dist is not None:
                message += f"Distance: {dist*100:+.2f}%\n"
        return self._send_message(message)

    def alert_osf_sl_hit(self, trade: dict) -> bool:
        """Alert for OSF stop loss hit (exchange SL filled)."""
        symbol = trade["symbol"]
        exit_price = trade["exit_price"]
        pnl = trade["p&l_usd"]
        pnl_pct = trade["p&l_pct"]
        days_held = trade.get("days_held", 0)
        message = (
            f"🔻 <b>OSF SL HIT</b> {symbol}\n"
            f"Exit: ${exit_price:,.2f}\n"
            f"Days held: {days_held}\n"
            f"📉 P&L: ${pnl:,.2f} ({pnl_pct:+.2f}%)\n"
        )
        return self._send_message(message)

    def alert_osf_exit_time(self, trade: dict) -> bool:
        """Alert for OSF time-based exit (OSF_EXIT_TIME signal — Strategy #2 scheduled exit)."""
        symbol = trade["symbol"]
        exit_price = trade["exit_price"]
        pnl = trade["p&l_usd"]
        pnl_pct = trade["p&l_pct"]
        days_held = trade.get("days_held", 0)
        pnl_emoji = "📉" if pnl < 0 else "📈"
        message = (
            f"⏱️ <b>OSF EXIT (TIME)</b> {symbol}\n"
            f"Exit: ${exit_price:,.2f}\n"
            f"Days held: {days_held}\n"
            f"{pnl_emoji} P&L: ${pnl:,.2f} ({pnl_pct:+.2f}%)\n"
        )
        return self._send_message(message)
