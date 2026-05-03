"""One-shot patch: fix entry_price None handling in main.py"""

with open('main.py', 'r') as f:
    content = f.read()

OLD = "            entry_price = order.get('average', price)"
NEW = "            entry_price = order.get('average') or price"

if OLD not in content:
    print("ERROR: Old line not found. File may have been edited already.")
    print("Aborting without changes.")
    exit(1)

count = content.count(OLD)
if count > 1:
    print(f"ERROR: Old line matches {count} times. Ambiguous. Aborting.")
    exit(1)

with open('main.py.bak_entryprice', 'w') as f:
    with open('main.py', 'r') as orig:
        f.write(orig.read())

new_content = content.replace(OLD, NEW)

with open('main.py', 'w') as f:
    f.write(new_content)

print("✅ Patched main.py")
print("   - entry_price now falls back to 'price' if 'average' is None or missing")
print("   Backup at: main.py.bak_entryprice")
