#!/usr/bin/env python3
"""
patch_session4_glue.py

Session 4 patch: Fix systemic glue-mismatch in main.py webhook handler.

Problem: main.py calls signal_parser.parse(payload), receives a validated
dict back, then throws it away and re-reads raw payload for symbol/action/
price/supertrend/rsi. This bypasses parser validation (uppercasing, type
coercion, optional-field handling) and is exactly the systemic glue-mismatch
pattern called out in the handoff doc as the root cause of 13 prior bugs.

Fix: Read from `parsed` dict instead of raw `payload`. Parser already
guarantees symbol/action are non-None and uppercased on success, and
price/supertrend/rsi are floats on LONG/SHORT or None on closes.

Safety:
  - Backs up main.py to main.py.bak_session4_glue before any change.
  - Uses byte-exact match on the OLD block; refuses to patch if not found.
  - Idempotent: if the NEW block is already present, does nothing.
  - Prints a unified diff before writing so you can eyeball the change.

Usage:
  cd ~/Desktop/Projects/Proppa_Kraken_Crypto
  python3 patch_session4_glue.py

Then:
  git diff main.py
  git add main.py
  git commit -m "Fix main.py webhook handler: read from parsed signal dict"
  git push
"""

import sys
import os
import shutil
import difflib

TARGET = "main.py"
BACKUP = "main.py.bak_session4_glue"

OLD_BLOCK = """            symbol = payload.get('symbol')
            action = payload.get('action')
            price = float(payload.get('price', 0))
            supertrend = float(payload.get('supertrend', 0))
            rsi = float(payload.get('rsi', 0))
"""

NEW_BLOCK = """            symbol = parsed['symbol']
            action = parsed['action']
            price = parsed.get('price') or 0
            supertrend = parsed.get('supertrend') or 0
            rsi = parsed.get('rsi') or 0
"""


def main():
    if not os.path.exists(TARGET):
        print(f"ERROR: {TARGET} not found in current directory.")
        print(f"  cwd: {os.getcwd()}")
        print("  Run this script from the repo root.")
        sys.exit(1)

    with open(TARGET, "r", encoding="utf-8") as f:
        original = f.read()

    # Idempotency check: already patched?
    if NEW_BLOCK in original and OLD_BLOCK not in original:
        print("Already patched. NEW_BLOCK is present and OLD_BLOCK is absent.")
        print("No changes made.")
        sys.exit(0)

    # Verify the OLD_BLOCK exists exactly once
    occurrences = original.count(OLD_BLOCK)
    if occurrences == 0:
        print("ERROR: OLD_BLOCK not found in main.py.")
        print("  The file may have been edited since this patch was generated,")
        print("  or indentation/whitespace differs. Aborting without changes.")
        print()
        print("  Expected to find this block (12-space indent inside try:):")
        print("  ---")
        for line in OLD_BLOCK.splitlines():
            print(f"  {line!r}")
        print("  ---")
        sys.exit(2)

    if occurrences > 1:
        print(f"ERROR: OLD_BLOCK found {occurrences} times in main.py.")
        print("  Refusing to patch — would be ambiguous which one to replace.")
        sys.exit(3)

    # Backup
    shutil.copy2(TARGET, BACKUP)
    print(f"Backup created: {BACKUP}")

    # Apply patch
    patched = original.replace(OLD_BLOCK, NEW_BLOCK, 1)

    # Show diff for user verification
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        fromfile=f"{TARGET} (before)",
        tofile=f"{TARGET} (after)",
        n=2,
    )
    print()
    print("--- Diff ---")
    sys.stdout.writelines(diff)
    print("--- End diff ---")
    print()

    # Write
    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(patched)

    print(f"Patched {TARGET} successfully.")
    print()
    print("Next steps:")
    print(f"  1. git diff {TARGET}")
    print(f"  2. python3 -c \"import ast; ast.parse(open('{TARGET}').read()); print('syntax OK')\"")
    print(f"  3. git add {TARGET}")
    print("  4. git commit -m \"Fix main.py webhook handler: read from parsed signal dict, not raw payload\"")
    print("  5. git push")
    print()
    print(f"To rollback: cp {BACKUP} {TARGET}")


if __name__ == "__main__":
    main()
