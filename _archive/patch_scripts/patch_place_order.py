"""One-shot patch: fix place_order method name and args in main.py"""

with open('main.py', 'r') as f:
    content = f.read()

OLD = """            success, order, error = self.kraken.place_order(
                symbol=symbol,
                side='buy' if action == 'LONG' else 'sell',
                order_type='market',
                amount=qty
            )"""

NEW = """            success, order, error = self.kraken.place_market_order(
                symbol=symbol,
                side='buy' if action == 'LONG' else 'sell',
                quantity=qty
            )"""

if OLD not in content:
    print("ERROR: Old block not found. File may have been edited already.")
    print("Aborting without changes.")
    exit(1)

count = content.count(OLD)
if count > 1:
    print(f"ERROR: Old block matches {count} times. Ambiguous. Aborting.")
    exit(1)

new_content = content.replace(OLD, NEW)

with open('main.py.bak_placeorder', 'w') as f:
    with open('main.py', 'r') as orig:
        f.write(orig.read())

with open('main.py', 'w') as f:
    f.write(new_content)

print("✅ Patched main.py")
print("   - place_order → place_market_order")
print("   - removed order_type='market'")
print("   - amount=qty → quantity=qty")
print("   Backup at: main.py.bak_placeorder")
