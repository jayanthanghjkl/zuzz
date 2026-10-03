import requests

# PASTE YOUR NGROK URL HERE
CLOUD_URL = "https://unvitiable-amya-dimensionally.ngrok-free.dev/"

# Test 1: Check if server is alive
print("Test 1: Checking server...")
response = requests.get(f"{CLOUD_URL}/")
print(f"Status: {response.status_code}")
print(f"Response: {response.json()}")
print()

# Test 2: Check Ollama health
print("Test 2: Checking Ollama...")
response = requests.get(f"{CLOUD_URL}/health")
print(f"Response: {response.json()}")
print()

# Test 3: Chat with AI
print("Test 3: Sending chat message...")
response = requests.post(
    f"{CLOUD_URL}/chat",
    json={
        "model": "llama3.1:8b",
        "messages": [
            {"role": "user", "content": "Say hello in 5 words"}
        ]
    },
    timeout=120
)
print(f"AI Response: {response.json()}")