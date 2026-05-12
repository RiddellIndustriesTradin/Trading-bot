# Proppa Kraken Spot v2 — Variant C Bot Migration Spec

**Created:** Sunday 3 May 2026, Brisbane time
**Status:** LOCKED — design source of truth for the bot migration
**Branch:** `variant-c-calendar`
**Repo:** `github.com/RiddellIndustriesTradin/Trading-bot`
**Strategy spec reference:** `Proppa_Kraken_Spot_v2_StrategySpec.md`
**Pine reference:** `Proppa_Kraken_Spot_v2_vC_Backtest.pine`

---

## 1. PURPOSE & SCOPE

### What this doc is

Source-of-truth design document for swapping the retired Supertrend+RSI strategy out of the Proppa Kraken bot infrastructure and replacing it with the **Variant C calendar strategy** (Sunday→Monday BTC weekend hold). This doc locks every code, config, and operational change before any edits are made.

### What this doc is NOT

- Not the strategy validation (that's the spec doc + backtest results)
- Not the Pine script (that's locked in `Proppa_Kraken_Spot_v2_vC_Backtest.pine`)
- Not the implementation (this defines what gets implemented; actual code edits happen after this is approved)

### Scope

Surgical strategy swap. The bot architecture (Kraken API integration, webhook handling, risk gates, alerting, state persistence, deployment topology) stays identical. Only the strategy-specific logic changes.

### Out of scope

- Family 2 (on-chain) deep dive — separate future session
- Multi-pair bot — Variant C is BTC-only per multi-pair test results
- Kraken Futures or perps — long-only spot
- DRY_RUN_MODE engineering — accepted live-only validation per locked decisions

---

## 2. STRATEGY SUMMARY (FOR QUICK REFERENCE)

| Parameter | Value |
|---|---|
| Strategy name | `variant_c_calendar` |
| Asset | KRAKEN:BTCUSD (spot) |
| Direction | LONG ONLY |
| Timeframe | Daily (1D) |
| Timezone | UTC |
| Entry trigger | Sunday close UTC |
| Exit trigger | Monday close UTC OR 3% SL hit (whichever first) |
| Hold period | ~24 hours |
| Stop loss | 3% from entry (hard, exchange-side) |
| Take profit | NONE (Variant C has no TP) |
| Frequency | ~1 trade per week, ~52 per year |
| Backtest gates passed | PF 1.207, DD 10.16%, 121 trades, WR 50.41%, +9.00% over 28mo |

---

## 3. CAPITAL & SIZING

| Parameter | Value |
|---|---|
| Startup capital | $1,000 AUD → ~$735 USD on Kraken (already funded) |
| Quote currency | USD (BTC/USD pair) |
| Funding flow | AUD deposit → manual AUD→USD conversion on Kraken → bot trades USD |
| Risk per trade | 0.5% of account equity (first 30 trades) |
| Scale-up trigger | 30 live trades aligned with backtest expectations → scale to 1.0% |
| Position sizing formula | `qty = (equity × risk_pct) / sl_distance` (existing code, reusable) |
| Drawdown reduction | Auto-halve sizing if DD > 15% (existing risk_manager behaviour, kept) |

---

## 4. FILE-BY-FILE CHANGE LIST

### 4.1 `kraken_api.py` — NO CHANGES ✅

Generic Kraken wrapper. Reusable as-is for Variant C. All methods (`get_balance`, `place_market_order`, `place_stop_loss_order`, `cancel_order`, `get_ticker`) are strategy-agnostic.

**Verification:** No edits required. File stays at current 16KB.

---

### 4.2 `risk_manager.py` — MODERATE CHANGES 🟠

**Changes:**

**Add:** New state field `losses_alert_3_fired` (bool) to track whether the 3-loss informational Telegram has fired (so we don't spam-fire on every subsequent loss while at 3+).

**Modify:** `record_trade_exit()` — add layered consecutive-loss handling:

```python
# Layered consecutive-loss handling (Variant C policy)
if pnl_usd < 0:
    self.state["consecutive_losses"] += 1
    
    # Layer 1: Informational alert at 3 losses (no action)
    if self.state["consecutive_losses"] == 3 and not self.state.get("losses_alert_3_fired"):
        # Caller (main.py) checks state and fires Telegram
        self.state["losses_alert_3_fired"] = True
    
    # Layer 2: Hard pause at 5 losses (manual resume required)
    if self.state["consecutive_losses"] >= self.max_consecutive_losses:
        self.state["paused_until_manual_resume"] = True
        # No more 24H auto-resume — manual flag flip required
else:
    # Reset on any winner
    self.state["consecutive_losses"] = 0
    self.state["losses_alert_3_fired"] = False
```

**Modify:** `can_trade()` — replace 24H auto-resume with manual-resume check:

```python
# Hard pause check — manual resume only
if self.state.get("paused_until_manual_resume"):
    return False, "🛑 PAUSED: 5 consecutive losses. Manual resume required via /resume endpoint."
```

**Add:** New method `manual_resume()` — resets the pause flag and the consecutive loss counter, callable via a Flask endpoint:

```python
def manual_resume(self) -> Tuple[bool, str]:
    """Manually resume trading after a hard-pause circuit break."""
    if not self.state.get("paused_until_manual_resume"):
        return False, "Not currently paused — no resume needed"
    self.state["paused_until_manual_resume"] = False
    self.state["consecutive_losses"] = 0
    self.state["losses_alert_3_fired"] = False
    self._save_state()
    return True, "✓ Trading resumed manually"
```

**Remove:** The existing `paused_until` ISO-datetime field becomes unused (replaced by `paused_until_manual_resume` bool). Leave the field present in state JSON for backward-compat, just stop reading/writing to it.

**Keep:** All other gates (daily trade cap, daily loss cap, drawdown, peak-equity tracking, position-size multiplier).

---

### 4.3 `position_sizing.py` — LIGHT CHANGES 🟢

**Changes:**

**Delete:** `calculate_take_profit()` method (lines ~70-86). Variant C has no TP.

**Delete:** SHORT branch in `calculate_pnl()`:

```python
# OLD:
if side == "LONG":
    pnl_usd = (exit_price - entry_price) * quantity
elif side == "SHORT":
    pnl_usd = (entry_price - exit_price) * quantity

# NEW:
if side != "LONG":
    raise ValueError(f"Variant C is long-only; got side={side}")
pnl_usd = (exit_price - entry_price) * quantity
```

**Keep:** Core `calculate()` method — formula is generic, reusable as-is.

---

### 4.4 `trade_logger.py` — LIGHT CHANGES 🟢

**Decision (per Q-A answered):** Clean schema. `trades.csv` doesn't exist, no historical data to preserve.

**Changes:**

**Replace:** `CSV_HEADERS` list:

```python
# OLD:
CSV_HEADERS = [
    "timestamp", "symbol", "side", "entry_price",
    "sl", "tp", "exit_type", "exit_price",
    "p&l_usd", "p&l_pct", "bars_held",
]

# NEW:
CSV_HEADERS = [
    "timestamp", "symbol", "side", "entry_price",
    "sl_price", "exit_type", "exit_price",
    "p&l_usd", "p&l_pct", "days_held",
]
```

**Removed:** `tp` column.
**Renamed:** `sl` → `sl_price`, `bars_held` → `days_held`.

**Modify:** `log_trade()` `required_fields` and the row-build dict to match new schema.

**Modify:** `read_trades()` numeric-cast section — drop `tp` cast, change `bars_held` → `days_held`.

**Keep:** `get_stats()` method — still computes WR, PF, total P&L the same way regardless of schema columns it doesn't touch.

---

### 4.5 `telegram_alerts.py` — MODERATE CHANGES 🟠

**Decision (per Q-B answered):** Option 1 — rename methods to match Variant C semantics.

**Renames:**

| Old method | New method | Message change |
|---|---|---|
| `alert_entry_long` | `alert_sunday_entry` | "🟢 SUNDAY ENTRY {symbol}\nEntry: ${price}\nSL: ${sl}" (drop TP line) |
| `alert_exit_hardstop` | `alert_sl_hit` | "🛑 SL HIT {symbol}\nExit: ${price}\nP&L: ${pnl}" |
| `alert_exit_timeout` | `alert_monday_exit` | "📅 MONDAY EXIT {symbol}\nExit: ${price}\nDays held: {days}\nP&L: ${pnl}" |

**Deletes (no longer needed):**

- `alert_entry_short` — no shorts in Variant C
- `alert_exit_softstop` — no soft stop logic in Variant C
- `alert_exit_takeprofit` — no TP in Variant C

**New method to add:**

```python
def alert_consecutive_loss_warning(self, count: int) -> bool:
    """Layer 1 informational alert at 3 consecutive losses."""
    message = (
        f"⚠️ <b>HEADS UP</b>\n"
        f"{count} consecutive losses on Variant C.\n"
        f"This is within normal variance (~12% probability).\n"
        f"Strategy still active. Monitor next trade."
    )
    return self._send_message(message)


def alert_circuit_break(self, count: int) -> bool:
    """Layer 2 hard pause alert at max consecutive losses."""
    message = (
        f"🛑 <b>CIRCUIT BREAK</b>\n"
        f"{count} consecutive losses — trading PAUSED.\n"
        f"Manual resume required.\n"
        f"Review trade log before re-enabling.\n"
        f"POST /resume to clear pause."
    )
    return self._send_message(message)
```

**Keep:** `alert_risk_event`, `alert_error`, `alert_status` — generic, reusable.

---

### 4.6 `signal_parser.py` — MODERATE CHANGES 🟠

**Replace:** `VALID_ACTIONS` set:

```python
# OLD:
VALID_ACTIONS = {"LONG", "SHORT", "CLOSE_HARDSTOP", "CLOSE_SOFTSTOP", 
                 "CLOSE_TAKEPROFIT", "CLOSE_TIMEOUT"}

# NEW:
VALID_ACTIONS = {"SUNDAY_ENTRY", "MONDAY_EXIT"}
# Note: SL_HIT is NOT in the alert action set — exchange handles SL fills.
# main.py detects SL_HIT via Kraken position state, not via TV alert.
```

**Replace:** `VALID_SYMBOLS` set:

```python
# OLD:
VALID_SYMBOLS = {"ETHUSDT", "BTCUSDT", "SOLUSDT", "ETHUSD", "BTCUSD", "SOLUSD"}

# NEW:
VALID_SYMBOLS = {"BTCUSD"}  # Variant C is BTC-only
```

**Replace:** Required-fields validation in `parse()`:

```python
# OLD: LONG/SHORT requires price + supertrend (+ optional rsi)
# NEW: SUNDAY_ENTRY requires price only (SL is calculated bot-side from config)

if action == "SUNDAY_ENTRY":
    if price is None:
        return False, None, "SUNDAY_ENTRY requires price"
    try:
        price = float(price)
    except (ValueError, TypeError):
        return False, None, "price must be numeric"

# MONDAY_EXIT requires no fields beyond symbol+action
```

**Drop:** All references to `supertrend` and `rsi` fields from the signal dict and parse logic.

**Delete:** `validate_entry_conditions()` method entirely. Variant C entry has no indicator condition — entry is unconditional on Sunday close UTC.

---

### 4.7 `main.py` — SUBSTANTIAL CHANGES 🔴 (but contained)

**Changes location-by-location:**

#### 4.7.1 `_handle_entry()` — rename and rewrite

Rename from `_handle_entry(symbol, action, price, supertrend, rsi)` to `_handle_sunday_entry(symbol, price)`.

**SL calculation** — replace lines 224-244:

```python
# OLD (Supertrend-derived SL):
sl_distance = abs(price - supertrend)
if sl_distance < 0.01:
    return {"status": "rejected", "message": "SL distance too small"}, 400
if sl_distance / price > 0.10:
    return {"status": "rejected", "message": "Supertrend value fails sanity check"}, 400

position = self.position_sizer.calculate(
    account_equity=equity,
    entry_price=price,
    stop_loss=supertrend
)

# NEW (config-driven 3% SL):
sl_pct = self.config['strategy']['sl_pct']  # 0.03
sl_price = price * (1 - sl_pct)  # LONG only — SL below entry
sl_distance = price - sl_price

position = self.position_sizer.calculate(
    account_equity=equity,
    entry_price=price,
    stop_loss=sl_price
)
```

**Drop:** TP calculation (line 269) and the `take_profit` variable. Trade dict gets `'tp': None` for schema continuity downstream, or omit field entirely depending on logger schema.

**Strip:** Hardcoded `'sell' if action == 'LONG' else 'buy'` ternary — Variant C is always `'buy'` for entry, `'sell'` for exit.

**Trade dict** changes:
```python
trade = {
    'entry_price': entry_price,
    'entry_time': datetime.utcnow(),
    'symbol': symbol,
    'side': 'LONG',  # always
    'quantity': qty,
    'sl_price': sl_price,  # was 'sl': supertrend
    'days_held': 0,        # was 'bars_held'
    # 'tp' field dropped
}
```

**Alerter call** changes:
```python
# OLD: 
if action == 'LONG':
    self.alerter.alert_entry_long(trade)
else:
    self.alerter.alert_entry_short(trade)

# NEW:
self.alerter.alert_sunday_entry(trade)
```

#### 4.7.2 `_handle_exit()` — rename and simplify

Rename from `_handle_exit(symbol, exit_type)` to `_handle_monday_exit(symbol)`.

**Drop:** The 4-way exit type dispatch on lines 415-424. Variant C exits via TV alert (MONDAY_EXIT) or via exchange-side SL fill (handled by the existing "exchange SL already triggered" branch on lines 352-365 — keep that logic, it's perfect for SL_HIT detection).

**New exit dispatch:**
```python
# Detect exit reason: was this a Monday close exit, or was SL already filled?
if 'No open position' in error or 'already closed' in error:
    # Exchange SL fired before our Monday exit signal arrived
    exit_reason = 'SL_HIT'
    self.alerter.alert_sl_hit(trade)
else:
    # Normal Monday close exit
    exit_reason = 'MONDAY_EXIT'
    self.alerter.alert_monday_exit(trade)

trade['exit_type'] = exit_reason
```

**Days-held calculation** (replace `_calculate_bars_held` line 179-188):

```python
def _calculate_days_held(self, entry_time: datetime) -> int:
    """Calculate days held since entry (Variant C is daily timeframe)."""
    if not entry_time:
        return 0
    try:
        duration = datetime.utcnow() - entry_time
        days = int(duration.total_seconds() / 86400)  # 86400 = 1 day in seconds
        return max(0, days)
    except:
        return 0
```

#### 4.7.3 Webhook routing — replace action dispatch

Lines 462-465 and 517-520:

```python
# OLD:
if action in ['LONG', 'SHORT']:
    response, status = self._handle_entry(symbol, action, price, supertrend, rsi)
else:
    response, status = self._handle_exit(symbol, action)

# NEW:
if action == 'SUNDAY_ENTRY':
    response, status = self._handle_sunday_entry(symbol, price)
elif action == 'MONDAY_EXIT':
    response, status = self._handle_monday_exit(symbol)
else:
    response, status = {"status": "rejected", "message": f"Unknown action: {action}"}, 400
```

#### 4.7.4 Risk-manager integration — add layered loss handling

After `record_trade_exit()` call in `_handle_monday_exit`:

```python
risk_status = self.risk_manager.record_trade_exit(pnl_usd, current_equity)

# Layer 1: Informational alert if 3-loss threshold just crossed
if risk_status.get('losses_alert_3_just_fired'):
    self.alerter.alert_consecutive_loss_warning(3)

# Layer 2: Circuit-break alert if hard pause just engaged
if risk_status.get('circuit_break_just_engaged'):
    self.alerter.alert_circuit_break(self.risk_manager.max_consecutive_losses)
```

(Risk manager's `record_trade_exit()` return dict needs to include the two new boolean flags.)

#### 4.7.5 Add `/resume` Flask endpoint

```python
@app.route('/resume', methods=['POST'])
def resume():
    """Manual resume after circuit-break pause."""
    if bot is None:
        return jsonify({"status": "offline"}), 500
    success, message = bot.risk_manager.manual_resume()
    return jsonify({"status": "success" if success else "noop", "message": message}), 200
```

---

### 4.8 `config.yaml` — LIGHT CHANGES 🟢

**New `strategy:` section:**

```yaml
strategy:
  name: "variant_c_calendar"
  sl_pct: 0.03                    # 3% stop loss from entry
  take_profit_enabled: false
  entry_day_utc: "sunday"          # documentation only — TV-side gating is authoritative
  exit_day_utc: "monday"
  hold_period_days: 1
```

**Modify `trading:` section:**

```yaml
trading:
  risk_per_trade: 0.005            # 0.5% — already correct ✅
  max_daily_trades: 1              # was 2 — Variant C is 1 trade/week
  max_consecutive_losses: 5        # circuit-break threshold (manual resume)
  consecutive_losses_warning: 3    # NEW: Layer 1 informational threshold
  max_daily_loss: -0.03            # keep
  max_drawdown: 0.15               # keep — matches Variant C gate
  max_drawdown_hard_stop: 0.20     # keep
```

**Modify `assets:` section:**

```yaml
assets:
  - symbol: BTCUSD
    leverage: 1
    enabled: true
```

(Drop `ETHUSDT` entirely — Variant C is BTC-only.)

**Keep unchanged:** `kraken:`, `telegram:` sections.

---

## 5. TRADINGVIEW ALERT SETUP

### 5.1 New alerts to create on Variant C Pine

Source Pine: `Proppa_Kraken_Spot_v2_vC_Backtest.pine` (already on TradingView per Phase 3).

**Alert 1: SUNDAY_ENTRY**
- **Condition:** Sunday daily candle close (per Pine `dayofweek == 1` and `barstate.isconfirmed`)
- **Frequency:** Once per bar close
- **Message:**
  ```json
  {"symbol": "BTCUSD", "action": "SUNDAY_ENTRY", "price": {{close}}}
  ```
- **Webhook URL:** `https://web-production-504463.up.railway.app/webhook`

**Alert 2: MONDAY_EXIT**
- **Condition:** Monday daily candle close (per Pine `dayofweek == 2` and `barstate.isconfirmed`)
- **Frequency:** Once per bar close
- **Message:**
  ```json
  {"symbol": "BTCUSD", "action": "MONDAY_EXIT", "price": {{close}}}
  ```
- **Webhook URL:** `https://web-production-504463.up.railway.app/webhook`

**No SL_HIT alert** — Kraken's exchange-side SL handles intrabar fills. The bot detects SL fills via the existing "exchange SL already triggered" code path in `_handle_monday_exit` when the Monday exit attempt finds no open position to close.

### 5.2 Alert verification before going live

- Check both alerts fire on the next Sunday (10 May) and Monday (11 May) regardless of whether bot is connected — verify TV-side first
- Webhook URL test: send a manual POST to `/webhook` with a sample payload and confirm bot processes it and either places an order or rejects with a clear reason
- Existing TV alerts from retired bot are already deleted (per Phase 8 retirement doc)

---

## 6. DEPLOYMENT FLOW

### 6.1 Branch & merge strategy

- All changes happen on `variant-c-calendar` branch (already created and pushed)
- Migration commits will be granular: one per file ideally, with descriptive messages
- Tag `v1-supertrend-retired-2026-05-02` already exists at `cfb42f5` for rollback reference
- Merge to `main` only AFTER local validation (Step 6.3 below)

### 6.2 Railway deployment

- Railway is currently auto-deploying from `main` branch (per Apr 28 deploy log)
- While work happens on `variant-c-calendar`, Railway keeps running the inert retired bot — no impact, no alerts firing
- Merging `variant-c-calendar` → `main` triggers Railway rebuild and redeploy
- Bot becomes Variant C-shaped at that moment

### 6.3 Pre-deploy local validation

Before merging to main, run these locally on the `variant-c-calendar` branch:

1. **Lint / smoke test:** `python -c "import main"` — should not throw
2. **Config load test:** instantiate `TradingBot()` locally with a dummy config — should construct cleanly
3. **Webhook handler test (no Kraken calls):** mock the Kraken API and feed a SUNDAY_ENTRY payload to `bot.handle_webhook()` — should reach the order placement step with the correct calculated SL
4. **Signal parser test:** unit-test `SignalParser.parse()` with valid + invalid payloads
5. **Position sizer test:** unit-test `PositionSizer.calculate()` with known equity/price/SL → known qty

If any test fails, fix on branch before merge.

### 6.4 Merge & deploy sequence

```bash
# On variant-c-calendar branch, all tests passing
git checkout main
git merge variant-c-calendar --no-ff -m "feat: migrate to Variant C calendar strategy"
git push origin main
# Railway auto-deploys, ~2-3min
# Watch Railway deploy logs for clean startup
```

### 6.5 Rollback procedure (if anything goes sideways post-deploy)

```bash
# Revert main to retired-version state
git checkout main
git reset --hard v1-supertrend-retired-2026-05-02
git push --force-with-lease origin main
# Railway auto-redeploys retired version
# (Remember: retired version had no TV alerts, so it'll just sit inert)
```

`--force-with-lease` is safer than `--force` — refuses to push if remote has changes we don't have locally.

---

## 7. FIRST-LIVE-TRADE READINESS CHECKLIST

Before allowing the first live trade to fire, ALL of these must be ✅:

- [ ] All file changes from §4 committed to `variant-c-calendar`
- [ ] Local validation tests (§6.3) all pass
- [ ] `variant-c-calendar` merged to `main`
- [ ] Railway redeploy completed cleanly (deploy log shows `✓ Trading Bot initialized`)
- [ ] `/health` endpoint returns 200 with current $735+ USD balance
- [ ] TV alerts created (SUNDAY_ENTRY + MONDAY_EXIT) with correct webhook URL
- [ ] TV alert messages tested manually via "Send test alert" — verify bot receives and parses correctly
- [ ] Manual `/webhook` POST test confirms full path: parse → balance fetch → position size → market order → SL placement (use a $1 dummy trade or test in Kraken sandbox if possible)
- [ ] Telegram alerts working — test with `alert_status` ping
- [ ] Risk manager state file exists and shows clean state
- [ ] First trade allowed window confirmed: Sunday 10 May 2026 close UTC = ~10:00 AM AEST Mon 11 May

---

## 8. POST-DEPLOY MONITORING (FIRST 30 TRADES)

### 8.1 Per-trade verification

For each of the first ~5 trades:
- Verify entry fires within 1 minute of Sunday UTC close
- Verify SL placed on Kraken exchange-side
- Verify exit fires within 1 minute of Monday UTC close (or SL fills intrabar)
- Verify Telegram alerts fire for both entry and exit
- Cross-check P&L vs backtest expectation (live P&L should be in same ballpark as backtest's avg-trade-P&L)

### 8.2 30-trade review gate

After 30 live trades (~7 months), evaluate:

- **Live PF vs backtest PF (1.207):** within 0.2 = aligned; below 1.0 = warning; below 0.9 = strategy retire trigger
- **Live WR vs backtest WR (50.41%):** ±5pp acceptable
- **Live max DD vs backtest max DD (10.16%):** if live DD > 15%, pause and review
- **Trade execution faithfulness:** every entry on Sunday close, every exit on Monday close or SL — no rogue trades

If aligned: scale risk_per_trade from 0.005 → 0.01 (1% per trade), top up account toward $2-5k AUD.

If misaligned: stop, investigate, do NOT scale.

---

## 9. PRE-COMMITTED RETIREMENT TRIGGERS

To avoid future goalpost-shifting, lock these now:

- **Live PF < 0.9 over 30+ trades:** retire Variant C, no second chances
- **Live max DD > 20%:** hard halt (existing risk_manager `max_drawdown_hard_stop` handles this)
- **5 consecutive losses (any time):** circuit-break pause, manual review required
- **Strategy spec deviation discovered post-deploy:** halt, fix, re-deploy, restart 30-trade clock

---

## 10. CHANGELOG SUMMARY (HUMAN-READABLE)

Net effect of this migration:

| Aspect | Before (retired) | After (Variant C) |
|---|---|---|
| Strategy | Supertrend+RSI on ETH 4H | Calendar Sunday→Monday on BTC daily |
| Pair | ETHUSDT | BTCUSD |
| Trigger logic | Indicator-driven (LONG/SHORT) | Time-driven (SUNDAY_ENTRY/MONDAY_EXIT) |
| SL calc | From Supertrend value | Fixed 3% from entry |
| TP | Yes (1.5R) | None |
| Direction | Long + Short | Long only |
| Hold period | Variable (up to 12H+ timeout) | ~24H (Sun close → Mon close) |
| Trade frequency | Multiple per day possible | ~1 per week |
| Consecutive-loss handling | 24H auto-pause at 5 | 3-loss informational + 5-loss manual-resume circuit break |
| TP/Soft-stop alerts | Yes | Removed |
| Architecture | Flask webhook + Kraken CCXT + Railway | Identical |
| Code change scope | — | ~100 net lines across 7 files |

---

## 11. OPEN QUESTIONS (RESOLVED)

| Q | Resolution |
|---|---|
| Q1: Capital amount | $1,000 AUD startup → ~$735 USD on Kraken |
| Q2: Quote currency | BTC/USD, AUD funded then converted manually |
| Q3: Stop-loss execution | Hard SL via Kraken exchange order (same as original) |
| Q4: Repo strategy | In-place modification on `variant-c-calendar` branch |
| Q5: Alert source | TV alerts via webhook (same as original) |
| Q-A: Trade log schema | Clean schema (option b) — `trades.csv` doesn't exist |
| Q-B: Alerter naming | Option 1 — rename methods to match Variant C semantics |
| Q-C: Loss handling | Option 4 — layered (3-loss info, 5-loss manual-resume circuit break) |
| Q-Long-only | Strip SHORT support entirely (no dormant code paths) |

---

## 12. SIGN-OFF

This spec is the source of truth for the migration. Any deviation during implementation should be flagged back to this doc and either:
- Fixed in implementation to match spec, OR
- Spec updated with explicit rationale before code change applied

**Locked:** Sunday 3 May 2026
**Branch:** `variant-c-calendar`
**Next action:** Hand to coding agent (local Mac or Claude Code) for implementation, with this spec as the brief.

🍻

---

**Document end. This spec defines what gets built. Implementation follows.**
