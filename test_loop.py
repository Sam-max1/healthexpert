import requests
import json
import time

prompt = "Analyze the following document and provide a summary.\\n\\nDocument:\\nThe insurance policy covers medical expenses up to $10,000 per year. It does not cover pre-existing conditions. Claims must be submitted within 30 days.\\n\\nSummary:"

payload = {
    "prompt": prompt,
    "max_tokens": 512,
    "temperature": 0.0,
    "top_p": 0.9,
    "thinking_mode": True
}

start = time.time()
print("Testing with temperature=0.0...")
resp = requests.post("http://127.0.0.1:8002/v1/completions", json=payload)
end = time.time()
data = resp.json()

print(f"Time: {end-start:.2f}s")
print(f"Tokens: {data.get('usage')}")
print("Response length:", len(data['choices'][0]['text']))
print("Response start:", data['choices'][0]['text'][:100])
print("Response end:", data['choices'][0]['text'][-100:])

