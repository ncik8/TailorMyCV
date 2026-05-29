#!/usr/bin/env python3
"""
Test script to determine what extract_requirements returns and what
the extraction loop produces.

NOTE: The actual MiniMax API key in .env is invalid (masked as ***),
so we simulate the expected AI response based on the prompt format.
"""

import json
import sys
sys.path.insert(0, '/Users/nick/tailormycv')

job_description = "We are looking for a Chief Communications Officer with 10+ years of experience in corporate communications, media relations, and executive messaging. Must have experience leading high-performing teams, managing crisis communications, and working with C-suite executives. PMP or MBA preferred. Proficiency in Salesforce, Tableau, and PowerPoint required."

print("=" * 60)
print("JOB DESCRIPTION:")
print("=" * 60)
print(job_description)
print()

# Based on the REQUIREMENTS_EXTRACTOR_PROMPT example output format,
# the AI is expected to return flat string arrays for skills/certifications/tools/other
# and dicts for experience_years and leadership

# Simulate what the AI would likely return for this job description
simulated_ai_response = {
    "skills": [
        "Corporate Communications",
        "Media Relations",
        "Executive Messaging",
        "Crisis Communications",
        "Team Leadership"
    ],
    "experience_years": {
        "corporate_communications": 10,
        "media_relations": 10,
        "executive_messaging": 10,
        "crisis_communications": 10
    },
    "certifications": ["PMP", "MBA"],
    "leadership": {
        "team_leadership": True,
        "c_suite_collaboration": True
    },
    "tools": ["Salesforce", "Tableau", "PowerPoint"],
    "other": ["C-suite executive collaboration", "High-performing team management"]
}

print("=" * 60)
print("SIMULATED AI RESPONSE (based on prompt's example format)")
print("=" * 60)
print(json.dumps(simulated_ai_response, indent=2))
print()

print("=" * 60)
print("TYPE ANALYSIS OF AI RESPONSE")
print("=" * 60)
for category in ['skills', 'certifications', 'tools', 'other', 'experience_years', 'leadership']:
    if category in simulated_ai_response:
        val = simulated_ai_response[category]
        if isinstance(val, list):
            print(f"  {category}: list of {type(val[0]).__name__ if val else 'empty'}")
        else:
            print(f"  {category}: {type(val).__name__}")
print()

print("=" * 60)
print("WHAT THE EXTRACTION LOOP PRODUCES")
print("=" * 60)
print("Loop code:")
print("""
for category in ['skills', 'certifications', 'tools', 'other']:
    for item in requirements.get(category, []):
        if isinstance(item, dict):
            ats_keywords.append(item.get('keyword', '') or item.get('name', ''))
        elif isinstance(item, str):
            ats_keywords.append(item)
""")

ats_keywords = []
for category in ['skills', 'certifications', 'tools', 'other']:
    for item in simulated_ai_response.get(category, []):
        if isinstance(item, dict):
            ats_keywords.append(item.get('keyword', '') or item.get('name', ''))
        elif isinstance(item, str):
            ats_keywords.append(item)

print(f"Result: {ats_keywords}")
print(f"Total keywords: {len(ats_keywords)}")
print()

print("=" * 60)
print("ISSUE IDENTIFIED")
print("=" * 60)
print("""
The extraction loop only iterates over: skills, certifications, tools, other
It does NOT include: experience_years, leadership

So even if the AI returns experience_years or leadership data with keywords
embedded in dict values, they would be SKIPPED by the extraction loop.

Additionally, the extraction loop expects either:
- dicts with 'keyword' or 'name' keys, OR
- flat strings

If the AI returns list of dicts like [{"skill": "Python"}] instead of ["Python"],
the loop would produce empty strings because it looks for 'keyword' not 'skill'.
""")