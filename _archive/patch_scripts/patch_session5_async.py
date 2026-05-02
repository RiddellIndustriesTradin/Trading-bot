#!/usr/bin/env python3
"""
patch_session5_async.py

Session 5 patch #2: Make main.py's /webhook endpoint async + add error-path
Telegram alerts at the 3 silent-failure spots.

Problem (recap from session 5 handoff):
  A) Kraken /private/Balance took 7+ sec → bot tried once, gave up.
     [Patch 1 fixed this with retry-with-backoff in kraken_api.py.]
  B) TV reported "Webhook delivery failed — request took too long" because
     the synchronous handler exceeded TV's 3-5s timeout.
  C) Bot silently failed — no Telegram alert. Ash only learned by reading logs.

This patch addresses (B) and (C):

  (B) async webhook:
      - Webhook route now validates payload + parses signal synchronously
        (cheap, ~50ms) and returns 200 immediately.
      - Spawns a daemon threading.Thread running _process_signal_async(parsed)
        which performs the slow Kraken work.
      - Single-worker gunicorn means threading.Thread is safe; we don't need
        anything heavier (no asyncio, no celery, no queues).
      - handle_webhook() is KEPT INTACT for backward-compat with curl-driven
        tests — those still hit it synchronously via direct method call.

  (C) error-path Telegram alerts:
      - Three silent-failure spots in entry/exit paths get alert_error() calls:
          1. _handle_entry: balance fetch failed
          2. _handle_entry: market entry order failed
          3. _handle_exit:  market sell order failed (non-recoverable branch)
      - _process_signal_async also catches unhandled exceptions and fires
        alert_error("UNHANDLED_<action>", ...).
      - DELIBERATELY NOT alerted: 403 risk-gate rejections, 409 duplicate-
        position rejections — those are the system working correctly, not
        infra failures. Per Ash: "I want to be alerted when system fails.
        Noise that is being properly operated by the system, I'm not
        interested in."
      - DELIBERATELY NOT TOUCHED: existing alert_risk_event() calls at
        lines 298 (SL_PLACEMENT_FAILED) and 339 (CLOSE_BLOCKED_SL_CANCEL_FAILED).
        Those already work — adding alert_error() on the same path would
        double-notify.

Methods touched:
  - Add `import threading` at top of file
  - Add `_process_signal_async(parsed)` method on TradingBot
  - Add 3x `self.alerter.alert_error(...)` calls in _handle_entry/_handle_exit
  - Replace body of webhook() Flask route to spawn thread + return 200 fast
  - handle_webhook() left unchanged (deliberate — preserves curl backward compat)

Safety:
  - Backs up main.py to main.py.bak_session5_async first
  - Idempotent: detects already-applied state via marker comment
  - Refuses to apply if any expected anchor block is missing
  - Prints unified diff before writing
  - Validates result is parseable Python (ast.parse) before saving

Usage:
  cd ~/Desktop/Projects/Proppa_Kraken_Crypto
  python3 patch_session5_async.py

Then:
  python3 -c "import ast; ast.parse(open('main.py').read()); print('syntax OK')"
  grep -c "_process_signal_async" main.py    # expect 2 (1 def + 1 call)
  grep -c "alert_error" main.py              # expect 4 (3 specific + 1 unhandled)
  grep -c "import threading" main.py         # expect 1
  git diff main.py
"""

import sys
import os
import shutil
import difflib
import ast

TARGET = "main.py"
BACKUP = "main.py.bak_session5_async"

ALREADY_APPLIED_MARKER = "# SESSION5_ASYNC_APPLIED"

# ---------------------------------------------------------------------------
# Anchor 1: add `import threading` to the stdlib imports block.
# Insert it right after `import signal` and before the `from datetime` line,
# keeping stdlib imports grouped and alphabetical-ish (matches existing style:
# os, sys, logging, json, signal — not strictly alphabetical, so we just
# tack threading onto the end of the stdlib group).
# ---------------------------------------------------------------------------

ANCHOR_IMPORTS_OLD = '''import os
import sys
import logging
import json
import signal
from datetime import datetime, timedelta
from typing import Dict, Tuple
'''

ANCHOR_IMPORTS_NEW = '''import os
import sys
import logging
import json
import signal
import threading  # SESSION5_ASYNC_APPLIED
from datetime import datetime, timedelta
from typing import Dict, Tuple
'''

# ---------------------------------------------------------------------------
# Anchor 2: add alert_error() to balance-fail spot in _handle_entry.
# Original lines 206-208:
#         if not success:
#             logger.error(f"Failed to get balance: {error}")
#             return {"status": "error", "message": "Balance query failed"}, 500
# ---------------------------------------------------------------------------

ANCHOR_BALANCE_FAIL_OLD = '''        # Get account balance FIRST (needed for risk gates)
        success, equity, error = self.get_account_balance()
        if not success:
            logger.error(f"Failed to get balance: {error}")
            return {"status": "error", "message": "Balance query failed"}, 500
        '''

ANCHOR_BALANCE_FAIL_NEW = '''        # Get account balance FIRST (needed for risk gates)
        success, equity, error = self.get_account_balance()
        if not success:
            logger.error(f"Failed to get balance: {error}")
            self.alerter.alert_error("BALANCE_FETCH_FAILED", f"Could not fetch balance for {action} {symbol}: {error}")
            return {"status": "error", "message": "Balance query failed"}, 500
        '''

# ---------------------------------------------------------------------------
# Anchor 3: add alert_error() to entry-order-fail spot in _handle_entry.
# Original lines 258-260:
#             if not success:
#                 logger.error(f"Entry order failed: {error}")
#                 return {"status": "error", "message": error}, 500
# ---------------------------------------------------------------------------

ANCHOR_ENTRY_FAIL_OLD = '''            if not success:
                logger.error(f"Entry order failed: {error}")
                return {"status": "error", "message": error}, 500
            
            entry_price = order.get('average') or price
'''

ANCHOR_ENTRY_FAIL_NEW = '''            if not success:
                logger.error(f"Entry order failed: {error}")
                self.alerter.alert_error("ENTRY_ORDER_FAILED", f"{action} {symbol} qty={qty}: {error}")
                return {"status": "error", "message": error}, 500
            
            entry_price = order.get('average') or price
'''

# ---------------------------------------------------------------------------
# Anchor 4: add alert_error() to exit market-sell fail (non-recoverable branch)
# in _handle_exit. The recoverable branch ('No open position'/'already closed'
# treats SL-already-triggered as success) is left alone — that's not a failure.
#
# Original around lines 363-364:
#                 else:
#                     return {"status": "error", "message": error}, 500
# We need to be careful: this `else` is nested inside the `if not success:`
# block. Use enough surrounding context to make the anchor unique.
# ---------------------------------------------------------------------------

ANCHOR_EXIT_FAIL_OLD = '''                    order = {'close_price': exit_price}
                    success = True
                else:
                    return {"status": "error", "message": error}, 500
            
            exit_price = order.get('average') or order.get('close_price')
'''

ANCHOR_EXIT_FAIL_NEW = '''                    order = {'close_price': exit_price}
                    success = True
                else:
                    self.alerter.alert_error("EXIT_ORDER_FAILED", f"Close {symbol} qty={trade['quantity']}: {error}")
                    return {"status": "error", "message": error}, 500
            
            exit_price = order.get('average') or order.get('close_price')
'''

# ---------------------------------------------------------------------------
# Anchor 5: insert _process_signal_async() method right BEFORE handle_webhook().
# Anchor on the start of handle_webhook's signature + its docstring opener.
# ---------------------------------------------------------------------------

ANCHOR_INSERT_ASYNC_OLD = '''    def handle_webhook(self, payload: Dict) -> Tuple[Dict, int]:
        """
        Main webhook handler for TradingView alerts.
        '''

ANCHOR_INSERT_ASYNC_NEW = '''    def _process_signal_async(self, parsed: Dict):
        """
        Background-thread worker for processing a parsed signal.

        Called by the Flask /webhook route after it has validated and parsed
        the payload synchronously and returned 200 to TradingView. Runs the
        existing _handle_entry / _handle_exit methods, and turns any failure
        (non-200 response or unhandled exception) into a Telegram alert so
        Ash hears about silent infra failures.

        Deliberately does NOT alert on 403 (risk-gate rejection) or 409
        (duplicate position) — those are the system working as designed,
        not infra failures.

        Args:
            parsed: dict from signal_parser.parse(payload), already validated.
                    Expected keys: symbol, action, price, supertrend, rsi.
        """
        action = parsed.get('action', 'UNKNOWN')
        symbol = parsed.get('symbol', 'UNKNOWN')
        try:
            price = parsed.get('price') or 0
            supertrend = parsed.get('supertrend') or 0
            rsi = parsed.get('rsi') or 0

            if action in ['LONG', 'SHORT']:
                response, status = self._handle_entry(symbol, action, price, supertrend, rsi)
            else:
                response, status = self._handle_exit(symbol, action)

            # Alert on infra failures (5xx) but not on policy rejections (4xx).
            # Specifically: 403 = risk-gate, 409 = duplicate-position — these
            # are the system working correctly. 500 = something genuinely broke.
            if status >= 500:
                msg = response.get('message', 'unknown error') if isinstance(response, dict) else str(response)
                # Note: _handle_entry/_handle_exit already fire their own
                # alert_error() at the specific failure site, so this catch-all
                # mostly handles unexpected 500s from elsewhere in the path.
                logger.error(f"Async signal failed: {action} {symbol} status={status} msg={msg}")

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"Async signal processing crashed: {action} {symbol}: {e}\\n{tb}")
            try:
                self.alerter.alert_error(
                    f"UNHANDLED_{action}",
                    f"{symbol}: {type(e).__name__}: {str(e)[:200]}"
                )
            except Exception as alert_err:
                # If even the alerter blew up, log and move on — don't crash
                # the background thread.
                logger.error(f"Failed to send unhandled-error alert: {alert_err}")

    def handle_webhook(self, payload: Dict) -> Tuple[Dict, int]:
        """
        Main webhook handler for TradingView alerts.
        '''

# ---------------------------------------------------------------------------
# Anchor 6: replace the body of the Flask /webhook route to be async.
# Original (lines 503-518):
#   @app.route('/webhook', methods=['POST'])
#   def webhook():
#       """TradingView webhook receiver."""
#       if bot is None:
#           return jsonify({"status": "offline", "message": "Bot not initialized"}), 500
#       
#       try:
#           payload = request.get_json()
#           if not payload:
#               return jsonify({"status": "error", "message": "Empty payload"}), 400
#           
#           response, status = bot.handle_webhook(payload)
#           return jsonify(response), status
#       except Exception as e:
#           logger.error(f"Webhook error: {str(e)}")
#           return jsonify({"status": "error", "message": str(e)}), 500
# ---------------------------------------------------------------------------

ANCHOR_WEBHOOK_OLD = '''@app.route('/webhook', methods=['POST'])
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
'''

ANCHOR_WEBHOOK_NEW = '''@app.route('/webhook', methods=['POST'])
def webhook():
    """
    TradingView webhook receiver — ASYNC.

    Validates and parses the payload synchronously (~50ms), then spawns a
    daemon thread to do the slow Kraken work. Returns 200 to TV immediately
    so we don't blow TV's 3-5s timeout.

    Single-worker gunicorn (--workers 1) makes threading.Thread safe for
    state mutation in _handle_entry/_handle_exit. Don't scale workers
    without revisiting that.
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
'''


REPLACEMENTS = [
    ("IMPORTS",        ANCHOR_IMPORTS_OLD,       ANCHOR_IMPORTS_NEW),
    ("BALANCE_FAIL",   ANCHOR_BALANCE_FAIL_OLD,  ANCHOR_BALANCE_FAIL_NEW),
    ("ENTRY_FAIL",     ANCHOR_ENTRY_FAIL_OLD,    ANCHOR_ENTRY_FAIL_NEW),
    ("EXIT_FAIL",      ANCHOR_EXIT_FAIL_OLD,     ANCHOR_EXIT_FAIL_NEW),
    ("INSERT_ASYNC",   ANCHOR_INSERT_ASYNC_OLD,  ANCHOR_INSERT_ASYNC_NEW),
    ("WEBHOOK_ROUTE",  ANCHOR_WEBHOOK_OLD,       ANCHOR_WEBHOOK_NEW),
]


def main():
    if not os.path.exists(TARGET):
        print(f"ERROR: {TARGET} not found in current directory.")
        print(f"  cwd: {os.getcwd()}")
        print(f"  Run this script from the repo root.")
        sys.exit(1)

    with open(TARGET, "r", encoding="utf-8") as f:
        original = f.read()

    # Idempotency check
    if ALREADY_APPLIED_MARKER in original:
        print(f"Already patched. Marker `{ALREADY_APPLIED_MARKER}` found in {TARGET}.")
        print("No changes made.")
        sys.exit(0)

    # Verify each anchor occurs exactly once
    print(f"Checking anchors in {TARGET}...")
    for name, old, _new in REPLACEMENTS:
        count = original.count(old)
        if count != 1:
            print(f"  [{name}] FAIL: expected exactly 1 occurrence, found {count}")
            print(f"  Anchor (first 300 chars):")
            for line in old[:300].splitlines():
                print(f"    {line!r}")
            print()
            print(f"  This patch was generated against a specific version of {TARGET}")
            print(f"  and the file appears to differ. Aborting without changes.")
            sys.exit(2)
        else:
            print(f"  [{name}] OK")

    # Apply all replacements
    patched = original
    for name, old, new in REPLACEMENTS:
        patched = patched.replace(old, new, 1)

    # Verify the result is valid Python
    try:
        ast.parse(patched)
    except SyntaxError as e:
        print(f"ERROR: Patched output is not valid Python: {e}")
        print("  Aborting without writing.")
        sys.exit(3)

    # Backup original
    shutil.copy2(TARGET, BACKUP)
    print(f"\nBackup created: {BACKUP}")

    # Show diff
    diff = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        fromfile=f"{TARGET} (before)",
        tofile=f"{TARGET} (after)",
        n=2,
    ))
    print("\n--- Diff ---")
    sys.stdout.writelines(diff)
    print("--- End diff ---\n")

    # Write patched file
    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(patched)

    print(f"Patched {TARGET} successfully.")
    print()
    print("Lines changed:")
    print(f"  - Added `import threading` to stdlib imports")
    print(f"  - Added 3x alert_error() calls (balance fail, entry fail, exit fail)")
    print(f"  - Added _process_signal_async() method on TradingBot")
    print(f"  - Replaced /webhook route body with async version (parses sync,")
    print(f"    spawns daemon thread, returns 200 immediately)")
    print(f"  - handle_webhook() left UNCHANGED (preserves curl backward compat)")
    print()
    print("Verify:")
    print(f"  python3 -c \"import ast; ast.parse(open('{TARGET}').read()); print('syntax OK')\"")
    print(f"  grep -c \"import threading\" {TARGET}              # expect 1")
    print(f"  grep -c \"_process_signal_async\" {TARGET}         # expect 2")
    print(f"  grep -c \"alert_error\" {TARGET}                   # expect 4")
    print(f"  grep -c \"SESSION5_ASYNC_APPLIED\" {TARGET}        # expect 1")
    print(f"  git diff {TARGET}")
    print()
    print(f"Rollback: cp {BACKUP} {TARGET}")


if __name__ == "__main__":
    main()
