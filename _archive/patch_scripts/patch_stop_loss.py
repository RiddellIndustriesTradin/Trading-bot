"""One-shot patch: fix stop-loss order type and params"""

with open('kraken_api.py', 'r') as f:
    content = f.read()

OLD = """            order = self.exchange.create_order(
                symbol=ccxt_symbol,
                type='stop',
                side=side,
                amount=quantity,
                price=stop_price,
                params={'stopPrice': stop_price}
            )"""

NEW = """            order = self.exchange.create_order(
                symbol=ccxt_symbol,
                type='stop-loss',
                side=side,
                amount=quantity,
                price=stop_price,
                params={'trading_agreement': 'agree'}
            )"""

if OLD not in content:
    print("ERROR: Old block not found. File may have been edited already.")
    exit(1)

count = content.count(OLD)
if count > 1:
    print(f"ERROR: Old block matches {count} times. Ambiguous. Aborting.")
    exit(1)

with open('kraken_api.py.bak_stoploss', 'w') as f:
    with open('kraken_api.py', 'r') as orig:
        f.write(orig.read())

new_content = content.replace(OLD, NEW)

with open('kraken_api.py', 'w') as f:
    f.write(new_content)

print("✅ Patched kraken_api.py")
print("   - type='stop' → type='stop-loss'")
print("   - params={'stopPrice'} → params={'trading_agreement': 'agree'}")
print("   Backup at: kraken_api.py.bak_stoploss")
