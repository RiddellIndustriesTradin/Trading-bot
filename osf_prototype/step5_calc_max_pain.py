"""
Step 5 — Calculate max pain for the upcoming Friday expiry
Goal: Implement the max pain calculation and compare to Deribit's published value.
"""

import requests
from datetime import datetime, timedelta, timezone

# ─── Reuse Step 4's logic to get filtered instruments ──────────────────

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

def get_next_friday(from_date):
    days_until_friday = (4 - from_date.weekday()) % 7
    if days_until_friday == 0:
        days_until_friday = 7
    return from_date + timedelta(days=days_until_friday)

def get_friday_options(target_friday_date):
    instruments = fetch_options_summary()
    matching = []
    for inst in instruments:
        parsed = parse_instrument(inst['instrument_name'])
        if parsed is None:
            continue
        if parsed['expiry_date'].date() == target_friday_date:
            matching.append({
                'strike': parsed['strike'],
                'type': parsed['option_type'],
                'open_interest': inst['open_interest'],
                'underlying_price': inst['underlying_price']
            })
    return matching

# ─── Step 5 — Max pain calculation ─────────────────────────────────────

def calculate_max_pain(options):
    """
    Calculate the strike price at which total option holder payout is minimised.
    
    For each candidate expiry price P (we test each strike):
      - Call payout at strike K = max(0, P - K) * OI_call
      - Put payout at strike K = max(0, K - P) * OI_put
      - Total pain at P = sum of all call + put payouts
    
    Max pain = P that minimises total pain.
    """
    # Get unique strikes
    strikes = sorted(set(opt['strike'] for opt in options))
    
    # For each candidate expiry price (each strike), calculate total pain
    pain_at_each_strike = {}
    
    for candidate_price in strikes:
        total_pain = 0.0
        for opt in options:
            strike = opt['strike']
            oi = opt['open_interest']
            if opt['type'] == 'C':
                # Call holder profits if price > strike
                payout = max(0, candidate_price - strike) * oi
            else:  # 'P'
                # Put holder profits if price < strike
                payout = max(0, strike - candidate_price) * oi
            total_pain += payout
        pain_at_each_strike[candidate_price] = total_pain
    
    # Find strike with minimum pain
    max_pain_strike = min(pain_at_each_strike, key=pain_at_each_strike.get)
    
    return max_pain_strike, pain_at_each_strike

# ─── Run the calculation ───────────────────────────────────────────────

now = datetime.now(timezone.utc)
next_friday = get_next_friday(now).date()

print(f"Calculating max pain for: Friday {next_friday.strftime('%d %b %Y')}")
print("---")

options = get_friday_options(next_friday)
print(f"Options for this expiry: {len(options)}")

if not options:
    print("⚠️  No options found, aborting.")
    exit()

# Get current underlying price (from any option, they all reference the same)
current_price = options[0]['underlying_price']
print(f"Current BTC price: ${current_price:,.2f}")

# Compute max pain
max_pain_strike, pain_curve = calculate_max_pain(options)

print(f"Calculated max pain: ${max_pain_strike:,.2f}")
print(f"Distance from current price: {((max_pain_strike - current_price) / current_price * 100):+.2f}%")
print()

# Show the pain curve at strikes near max pain
print("Pain curve (lower = market makers prefer this price):")
sorted_strikes = sorted(pain_curve.keys())
for strike in sorted_strikes:
    pain_btc = pain_curve[strike]
    marker = " ← MAX PAIN" if strike == max_pain_strike else ""
    print(f"  ${strike:>8,.0f}  Pain: {pain_btc:>10,.1f} BTC{marker}")
