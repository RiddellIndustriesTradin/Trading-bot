"""
Step 3 — First API call to Deribit
Goal: Just see what the API returns. No processing yet.
"""

import requests
import json

# The endpoint we identified
url = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
params = {
    "currency": "BTC",
    "kind": "option"
}

print(f"Calling: {url}")
print(f"Params: {params}")
print("---")

response = requests.get(url, params=params)

# Status check
print(f"HTTP Status: {response.status_code}")

# Parse JSON
data = response.json()

# Show structure overview
print(f"Top-level keys: {list(data.keys())}")
print(f"Number of instruments returned: {len(data.get('result', []))}")
print()

# Show ONE example instrument (just the first one) so we can see the structure
if data.get('result'):
    print("Example instrument (first in the list):")
    print(json.dumps(data['result'][0], indent=2))
