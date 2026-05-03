"""V3: ASCII-only matching, replace by line number"""

with open('main.py', 'r') as f:
    lines = f.readlines()

# Find the alert_risk_event call by ASCII substring (avoid emoji)
risk_line_idx = None
for i, line in enumerate(lines):
    if 'self.alerter.alert_risk_event(' in line and 'SL_PLACEMENT_FAILED' not in line:
        risk_line_idx = i
        break

if risk_line_idx is None:
    print("ERROR: alert_risk_event call not found (or already patched)")
    exit(1)

# Get the original line, preserve indentation, replace just the args
original = lines[risk_line_idx]
print(f"Found at line {risk_line_idx + 1}: {repr(original)}")

# Surgical: insert "SL_PLACEMENT_FAILED", before the f"
# The line looks like:  XXXXX self.alerter.alert_risk_event(f"...")XXXXX
# We want to turn alert_risk_event(  into  alert_risk_event("SL_PLACEMENT_FAILED",
# The pattern alert_risk_event(f" can be replaced uniformly:
new_line = original.replace(
    'self.alerter.alert_risk_event(f"',
    'self.alerter.alert_risk_event("SL_PLACEMENT_FAILED", f"'
)
if new_line == original:
    print("ERROR: Replacement didn't change the line. Check source.")
    exit(1)
print(f"New line: {repr(new_line)}")

# Now find the alert_entry block — using ASCII substring
entry_block_start = None
for i, line in enumerate(lines):
    if line.strip() == 'self.alerter.alert_entry(':
        entry_block_start = i
        break

if entry_block_start is None:
    print("ERROR: alert_entry( call not found (or already patched)")
    exit(1)

# Find the closing ) — should be 7 lines after (symbol, side, entry_price, stop_loss, take_profit, quantity, ))
entry_block_end = None
for i in range(entry_block_start, min(entry_block_start + 12, len(lines))):
    if lines[i].strip() == ')':
        entry_block_end = i
        break

if entry_block_end is None:
    print("ERROR: alert_entry closing ) not found within 12 lines")
    exit(1)

print(f"alert_entry block: lines {entry_block_start + 1} to {entry_block_end + 1}")

# Determine indentation of the alert_entry( line (preserve it)
indent = lines[entry_block_start][:len(lines[entry_block_start]) - len(lines[entry_block_start].lstrip())]

# Build replacement lines
new_entry_lines = [
    f"{indent}if action == 'LONG':\n",
    f"{indent}    self.alerter.alert_entry_long(trade)\n",
    f"{indent}else:\n",
    f"{indent}    self.alerter.alert_entry_short(trade)\n",
]

# Backup
with open('main.py.bak_alerter_v3', 'w') as f:
    f.writelines(lines)

# Apply patches: replace the risk_event line, then replace the entry block
# Important: replace entry block FIRST (changes line count), then we know risk_line_idx may shift if entry was before it.
# Check ordering:
if entry_block_start < risk_line_idx:
    # entry comes before risk — replace entry first, then risk index stays valid relative to new lines
    new_lines = (
        lines[:entry_block_start]
        + new_entry_lines
        + lines[entry_block_end + 1:]
    )
    # Now find the risk line again in new_lines
    for i, line in enumerate(new_lines):
        if 'self.alerter.alert_risk_event(' in line and 'SL_PLACEMENT_FAILED' not in line:
            new_lines[i] = line.replace(
                'self.alerter.alert_risk_event(f"',
                'self.alerter.alert_risk_event("SL_PLACEMENT_FAILED", f"'
            )
            break
else:
    # risk comes before entry — replace risk first
    new_lines = list(lines)
    new_lines[risk_line_idx] = new_line
    # entry indices haven't shifted
    new_lines = (
        new_lines[:entry_block_start]
        + new_entry_lines
        + new_lines[entry_block_end + 1:]
    )

with open('main.py', 'w') as f:
    f.writelines(new_lines)

print("✅ Patched main.py")
print("   Fix 1: alert_risk_event now passes event_type + message")
print("   Fix 2: alert_entry replaced with dispatch to alert_entry_long/short")
print("   Backup at: main.py.bak_alerter_v3")
