#!/usr/bin/env python3
"""
Diagnostic: find out WHY the B1 pattern isn't matching in main.py.

This script does NOT modify any files. It just reads main.py and prints
exactly what's around the get_ticker call so we can see the real bytes.
"""
import sys

PATH = "main.py"

with open(PATH, 'rb') as f:
    raw = f.read()

# Find the first occurrence of "self.kraken.get_ticker(symbol)"
needle = b"self.kraken.get_ticker(symbol)"
idx = raw.find(needle)
if idx < 0:
    print("FAIL: get_ticker call not found in main.py at all")
    sys.exit(1)

print(f"First 'self.kraken.get_ticker(symbol)' at byte offset {idx}")
print(f"Second occurrence at: {raw.find(needle, idx + 1)}")
print()

# Print 250 bytes before and 250 bytes after the first occurrence,
# with byte values and printable chars.
start = max(0, idx - 250)
end = min(len(raw), idx + 250)
chunk = raw[start:end]

print(f"=== Bytes {start} to {end} around first get_ticker call ===")
print()

# Print each line with its byte offset and a hex+printable representation
# of any non-printable or non-ASCII bytes.
lines = chunk.split(b"\n")
offset = start
for line in lines:
    # show line as repr() so any escape chars are visible
    # also detect non-ASCII bytes
    has_nonascii = any(b > 127 for b in line)
    has_tabs = b"\t" in line
    has_cr = b"\r" in line
    flags = []
    if has_nonascii:
        flags.append("NON-ASCII")
    if has_tabs:
        flags.append("TABS")
    if has_cr:
        flags.append("CR")
    flag_str = f" [{', '.join(flags)}]" if flags else ""
    # show as escaped string with leading-whitespace count
    leading = 0
    for b in line:
        if b == 0x20:
            leading += 1
        elif b == 0x09:
            leading = -1  # signal a tab
            break
        else:
            break
    if leading == -1:
        ws_label = "TAB-INDENT"
    else:
        ws_label = f"{leading}sp"
    # Print: offset, whitespace label, line content
    print(f"  {offset:6d} [{ws_label:>10s}]{flag_str} {repr(line.decode('utf-8', errors='replace'))}")
    offset += len(line) + 1  # +1 for the newline

print()
print("=== Now checking what the v4 B1 pattern looks like ===")
v4_pattern = (
    "                    ticker = self.kraken.get_ticker(symbol)\n"
    "                    exit_price = ticker.get('last', trade['entry_price'])\n"
    "                except:\n"
    "                    exit_price = trade['entry_price']\n"
)
print("Pattern (repr):")
for line in v4_pattern.split("\n"):
    if line:
        leading = len(line) - len(line.lstrip(' '))
        print(f"  [{leading}sp] {repr(line)}")

# Check if the pattern exists, character by character starting from the file
text = raw.decode("utf-8", errors="replace")
if v4_pattern in text:
    print("\n>>> PATTERN FOUND in text - patch should have worked.")
else:
    print("\n>>> PATTERN NOT FOUND. Searching for partial matches...")
    # Try progressively shorter prefixes of the pattern
    for n_lines in range(4, 0, -1):
        prefix = "\n".join(v4_pattern.split("\n")[:n_lines]) + "\n"
        if prefix in text:
            print(f"  Prefix of {n_lines} line(s) FOUND.")
            # Find where it is and what comes next
            pos = text.find(prefix)
            after = text[pos + len(prefix):pos + len(prefix) + 80]
            print(f"  At position {pos}. Bytes immediately after the prefix:")
            print(f"  {repr(after)}")
            break
        else:
            print(f"  Prefix of {n_lines} line(s) NOT found.")
