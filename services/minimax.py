import requests
import os
from dotenv import load_dotenv
load_dotenv()

MINIMAX_BASE_URL = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io")
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")

def chat(system_prompt, user_message, model="MiniMax-M2.7"):
    """Call MiniMax chat API and return the response text."""
    if not MINIMAX_API_KEY:
        return {"error": "MINIMAX_API_KEY not set"}
    
    response = requests.post(
        f"{MINIMAX_BASE_URL}/v1/text/chatcompletion_v2",
        headers={
            "Authorization": f"Bearer {MINIMAX_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]
        }
    )
    
    if response.status_code != 200:
        return {"error": f"API error: {response.status_code} - {response.text}"}
    
    try:
        return response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        return {"error": f"Failed to parse response: {str(e)} - {response.text}"}