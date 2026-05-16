"""
Step 4 — Parse instruments and filter to upcoming Friday expiry
Goal: From 936 instruments, narrow to just the ones expiring this Friday.
"""

import requests
from datetime import datetime, timedelta, timezone

# Same API call as before
url = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
params = {"currency": "BTC", "kind": "option"}
response = requests.get(url, params=params)
data = response.json()
instruments = data['result']

print(f"Total instruments fetched: {len(instruments)}")

# Step 4a — Parse instrument names
# Format: "BTC-DDMMMYY-STRIKE-C/P"
# e.g. "BTC-25DEC26-40000-C"

def parse_instrument(name):
    """Extract expiry date, strike, and option type from instrument name."""
    parts = name.split('-')
    if len(parts) != 4:
        return None
    
    _, expiry_str, strike_str, option_type = parts
    
    # Parse expiry: "25DEC26" → datetime
    try:
        expiry_date = datetime.strptime(expiry_str, "%d%b%y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    
    return {
        'expiry_date': expiry_date,
        'strike': float(strike_str),
        'option_type': option_type,  # 'C' or 'P'
        'instrument_name': name
    }

# Step 4b — Find the upcoming Friday
def get_next_friday(from_date):
    """Find the next Friday from the given date."""
    days_until_friday = (4 - from_date.weekday()) % 7
    if days_until_friday == 0:
        # If today IS Friday, take next Friday (7 days)
        days_until_friday = 7
    return from_date + timedelta(days=days_until_friday)

now = datetime.now(timezone.utc)
next_friday = get_next_friday(now)
# Deribit options expire at 08:00 UTC, so set the time
next_friday = next_friday.replace(hour=8, minute=0, second=0, microsecond=0)

print(f"Today (UTC): {now.strftime('%a %d %b %Y %H:%M')}")
print(f"Next Friday expiry target: {next_friday.strftime('%a %d %b %Y %H:%M')}")
print()

# Step 4c — Filter instruments to that Friday
matching_instruments = []
for inst in instruments:
    parsed = parse_instrument(inst['instrument_name'])
    if parsed is None:
        continue
    
    # Match on date (year, month, day) — ignore exact time
    if parsed['expiry_date'].date() == next_friday.date():
        matching_instruments.append({
            'name': inst['instrument_name'],
            'strike': parsed['strike'],
            'type': parsed['option_type'],
            'open_interest': inst['open_interest'],
            'underlying_price': inst['underlying_price']
        })

print(f"Instruments matching upcoming Friday ({next_friday.strftime('%d %b %Y')}): {len(matching_instruments)}")
print()

# Show what we found
if matching_instruments:
    # Sort by strike
    matching_instruments.sort(key=lambda x: (x['strike'], x['type']))
    
    # Show summary
    calls = [i for i in matching_instruments if i['type'] == 'C']
    puts = [i for i in matching_instruments if i['type'] == 'P']
    
    print(f"Calls: {len(calls)}, Puts: {len(puts)}")
    print(f"Strike range: ${min(i['strike'] for i in matching_instruments):,.0f} - ${max(i['strike'] for i in matching_instruments):,.0f}")
    print(f"Underlying price: ${matching_instruments[0]['underlying_price']:,.2f}")
    print()
    
    # Show first 5 strikes with OI
    print("First 5 strikes (lowest):")
    for inst in matching_instruments[:5]:
        print(f"  {inst['name']:<30} OI: {inst['open_interest']:>8.2f} BTC")
else:
    print("⚠️  No instruments found for upcoming Friday. May need to check date logic.")
