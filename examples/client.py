"""Tiny Python client example:  python examples/client.py invoice.pdf"""
import json
import sys

import httpx

API = "http://localhost:8000"

path = sys.argv[1] if len(sys.argv) > 1 else "examples/samples/invoice.pdf"
with open(path, "rb") as f:
    r = httpx.post(f"{API}/v1/extract", files={"file": f}, data={"include_text": "false"}, timeout=120)
r.raise_for_status()
doc = r.json()
print(doc["doc_type"], json.dumps(doc["fields"], indent=2))
for item in doc["line_items"]:
    print("  -", item)
