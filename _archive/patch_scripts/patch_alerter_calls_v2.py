"""V2: Use repr-derived exact bytes for alert_risk_event line"""

with open('main.py', 'r') as f:
    content = f.read()

# Exact line content from repr: 16 spaces + the full call + emoji as 2 codepoints
OLD_RISK = '                self.alerter.alert_risk_event(f"\u26a0\ufe0fSL Placement Failed: {sl_error}")'
NEW_RISK = '                self.alerter.alert_risk_event("SL_PLACEMENT_FAILED", f"\u26a0\ufe0fSL Placement Failed: {sl_error}")'

OLD_ENTRY = """            # Send Telegram alert
            self.alerter.alert_entry(
                symbol=symbol,
                side=action,
                entry_price=entry_price,
                stop_loss=supertrend,
                take_profit=take_profit,
                quantity=qty
            )"""

NEW_ENTRY = """            # Send Telegram alert
            if action == 'LONG':
                self.alerter.alert_entry_long(trade)
            else:
                self.alerter.alert_entry_short(trade)"""

errors = []
if OLD_RISK not in content:
    errors.append("alert_risk_event OLD block not found")
elif content.count(OLD_RISK) > 1:
    errors.append(f"alert_risk_event OLD matches {content.count(OLD_RISK)} times")
if OLD_ENTRY not in content:
    errors.append("alert_entry OLD block not found")
elif content.count(OLD_ENTRY) > 1:
    errors.append(f"alert_entry OLD matches {content.count(OLD_ENTRY)} times")

if errors:
    print("ERRORS — aborting without changes:")
    for e in errors:
        print(f"  - {e}")
    exit(1)

with open('main.py.bak_alerter_v2', 'w') as f:
    with open('main.py', 'r') as orig:
        f.write(orig.read())

new_content = content.replace(OLD_RISK, NEW_RISK).replace(OLD_ENTRY, NEW_ENTRY)

with open('main.py', 'w') as f:
    f.write(new_content)

print("✅ Patched main.py")
print("   Fix 1: alert_risk_event now passes event_type + message")
print("   Fix 2: alert_entry replaced with dispatch to alert_entry_long/short")
print("   Backup at: main.py.bak_alerter_v2")
