import os
import sys
sys.path.insert(0, '/Users/nick/tailormycv')

from dotenv import load_dotenv
load_dotenv('/Users/nick/tailormycv/.env')

from services.gap_analyzer import extract_requirements

job_description = """Chief AI Officer for a leading Hong Kong financial services firm. Must have 10+ years AI experience, proficiency in Python, SQL, LLM fine-tuning, and RAG. MBA or equivalent required. Experience with enterprise AI at scale. Salesforce experience preferred."""

print("=== TESTING extract_requirements() directly ===")
requirements = extract_requirements(job_description)
print(f"\nrequirements returned: {requirements}")
print(f"Type: {type(requirements)}")

print("\n[Simulating app.py lines 621-629]")
ats_keywords = []
for category in ['skills', 'certifications', 'tools', 'other']:
    for item in requirements.get(category, []):
        if isinstance(item, dict):
            ats_keywords.append(item.get('keyword', '') or item.get('name', ''))
        elif isinstance(item, str):
            ats_keywords.append(item)
ats_keywords = list(dict.fromkeys(k for k in ats_keywords if k))
print(f"ats_keywords = {ats_keywords}")
print(f"length = {len(ats_keywords)}")