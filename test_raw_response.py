import os
import sys
sys.path.insert(0, '/Users/nick/tailormycv')

from dotenv import load_dotenv
load_dotenv('/Users/nick/tailormycv/.env')

from services.minimax import chat
from services.gap_analyzer import REQUIREMENTS_EXTRACTOR_PROMPT
import json

job_description = """Chief AI Officer for a leading Hong Kong financial services firm. Must have 10+ years AI experience, proficiency in Python, SQL, LLM fine-tuning, and RAG. MBA or equivalent required. Experience with enterprise AI at scale. Salesforce experience preferred."""

print("=== TESTING extract_requirements flow ===")
print(f"\n[JOB DESCRIPTION]\n{job_description}\n")

print("\n[RAW API RESPONSE]")
raw_response = chat(REQUIREMENTS_EXTRACTOR_PROMPT, job_description)
print(f"Type: {type(raw_response)}")
print(f"Content:\n{raw_response}")

print("\n[POST-PROCESSING ATTEMPT]")
text = raw_response.strip() if isinstance(raw_response, str) else str(raw_response)
if text.startswith("```"):
    text = text.split("```")[1]
    if text.startswith("json"):
        text = text[4:]
print(f"After stripping markdown: {text[:500]}...")

try:
    result = json.loads(text)
    print(f"\n[PARSED JSON]\n{json.dumps(result, indent=2)}")
    print(f"\n[ATS KEYWORDS EXTRACTION]")
    ats_keywords = []
    for category in ['skills', 'certifications', 'tools', 'other']:
        for item in result.get(category, []):
            if isinstance(item, dict):
                ats_keywords.append(item.get('keyword', '') or item.get('name', ''))
            elif isinstance(item, str):
                ats_keywords.append(item)
    ats_keywords = list(dict.fromkeys(k for k in ats_keywords if k))
    print(f"ats_keywords = {ats_keywords}")
except json.JSONDecodeError as e:
    print(f"JSON PARSE ERROR: {e}")