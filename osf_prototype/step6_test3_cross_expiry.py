"""
Step 6 — Test 3: Cross-expiry consistency check
Goal: Verify max pain calculation matches across weekly, monthly, quarterly expiries.
"""

import requests
from datetime import datetime, timezone

# ─── Reused functions from earlier steps ──────────────────────────────

def fetch_options_summary():
    url = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
    params = {"currency": "BTC", "kind": "option"}
    response = requests.get(url, params=params)
    return response.json()['result']

def parse_instrument(name):
    parts = name.split('-')
    if len(parts) != 4:
        return None
    _, expiry_str, strike_str, option_type = parts
    try:
        expiry_date = datetime.strptime(expiry_str, "%d%b%y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return {
        'expiry_date': expiry_date,
        'strike': float(strike_str),
        'option_type': option_type
    }

def get_options_for_date(target_date, instruments):
    """Filter instruments to a specific expiry date."""
    matching = []
    for inst in instruments:
        parsed = parse_instrument(inst['instrument_name'])
        if parsed is None:
            continue
        if parsed['expiry_date'].date() == target_date:
            matching.append({
                'strike': parsed['strike'],
                'type': parsed['option_type'],
                'open_interest': inst['open_interest'],
                'underlying_price': inst['underlying_price']
            })
    return matching

def calculate_max_pain(options):
    """Calculate max pain strike from a list of options."""
    strikes = sorted(set(opt['strike'] for opt in options))
    pain_at_each_strike = {}
    for candidate_price in strikes:
        total_pain = 0.0
        for opt in options:
            strike = opt['strike']
            oi = opt['open_interest']
            if opt['type'] == 'C':
                payout = max(0, candidate_price - strike) * oi
            else:
                payout = max(0, strike - candidate_price) * oi
            total_pain += payout
        pain_at_each_strike[candidate_price] = total_pain
    max_pain_strike = min(pain_at_each_strike, key=pain_at_each_strike.get)
    return max_pain_strike, pain_at_each_strike

# ─── Test 3 — Run for 3 different expiries ─────────────────────────────

# Fetch once, reuse for all three tests
print("Fetching all BTC options from Deribit (single API call)...")
all_instruments = fetch_options_summary()
print(f"Total instruments fetched: {len(all_instruments)}")
print()

# Define our three test targets
test_targets = [
    {
        'label': 'WEEKLY (15 May 2026)',
        'date': datetime(2026, 5, 15).date(),
        'expected_coinglass': 79000,  # We confirmed this earlier
    },
    {
        'label': 'MONTHLY (29 May 2026)',
        'date': datetime(2026, 5, 29).date(),
        'expected_coinglass': None,  # User to confirm from CoinGlass
    },
    {
        'label': 'QUARTERLY (26 June 2026)',
        'date': datetime(2026, 6, 26).date(),
        'expected_coinglass': None,  # User to confirm from CoinGlass
    },
]

print("=" * 70)
print("TEST 3 — CROSS-EXPIRY CONSISTENCY CHECK")
print("=" * 70)
print()

for target in test_targets:
    print(f"--- {target['label']} ---")
    options = get_options_for_date(target['date'], all_instruments)
    
    if not options:
        print(f"  ⚠️  No options found for {target['date']}")
        continue
    
    underlying_price = options[0]['underlying_price']
    max_pain_strike, _ = calculate_max_pain(options)
    
    # Calculate total OI in BTC and USD notional
    to
