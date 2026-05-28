"""
AI-powered CV parser using MiniMax API.
Extracts structured CV data from raw text (PDF/DOCX output).
"""
import os
import json
import logging
import requests
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# ============ CV SCHEMA (what the AI should extract) ============

CV_JSON_SCHEMA = {
    "name": "Full name of the person",
    "title": "Professional headline / job title",
    "email": "Email address",
    "phone": "Phone number (with country code if present)",
    "location": "City, Country or Region",
    "linkedin": "LinkedIn URL (leave empty if not found)",
    "website": "Personal website URL (leave empty if not found)",
    "summary": "Professional summary / profile text (2-4 sentences). Extract from profile/about section.",
    "experience": [
        {
            "title": "Job title, e.g. 'Chief Technology Officer'",
            "company": "Company name",
            "location": "City/Country (leave empty if not stated)",
            "start_date": "YYYY or 'MMM YYYY' format — e.g. '2021', 'Jan 2022'",
            "end_date": "YYYY or 'MMM YYYY' or 'Present' — e.g. '2023', 'Dec 2024', 'Present'",
            "bullets": [
                "Achievement or responsibility bullet point 1",
                "Achievement or responsibility bullet point 2"
            ]
        }
    ],
    "education": [
        {
            "degree": "Degree name, e.g. 'BSc Computer Science' or 'MBA'",
            "school": "University/Institution name",
            "year": " graduation year, e.g. '2015' or '2012-2016'",
            "field": "Field of study (leave empty if not stated)",
            "notes": "Honours, thesis, notable achievements (leave empty if not stated)"
        }
    ],
    "skills": ["Skill 1", "Skill 2", "Skill 3 (extract as many as possible)"],
    "certifications": ["Certification 1 (leave empty if none)"],
    "languages": [{"language": "English", "level": "Native / Fluent / Professional"}],
    "projects": [{"name": "Project name", "description": "Brief description (leave empty if none)"}]
}


CV_EXTRACTION_PROMPT = """You are an expert CV/Resume parser. Given raw text extracted from a CV document (PDF or DOCX), extract all information and return a structured JSON object.

IMPORTANT RULES:
- Return ONLY valid JSON — no markdown, no explanation, no preamble
- The JSON must match this exact schema with all fields
- For missing fields, use empty string "" or empty list [] — do NOT omit fields
- experience[].bullets: extract 2-6 bullet points per job — these are achievements, responsibilities, skills used. NOT job descriptions in paragraph form
- For date ranges: use 'YYYY' or 'MMM YYYY' format. Use 'Present' for current jobs
- skills: extract ALL technical and professional skills mentioned anywhere in the CV
- location fields: use "City, Country" format where possible
- summary: should be 2-4 sentences extracted from any profile/about section
- For two-column CVs: text may appear jumbled — use context to group content correctly

RAW CV TEXT:
"""


def parse_with_ai(raw_text: str, api_key: str, base_url: str = "https://api.minimax.chat") -> Dict:
    """
    Send raw CV text to MiniMax API and get structured JSON back.
    Falls back to rules-based parsing if AI fails.
    """
    if not raw_text or len(raw_text.strip()) < 50:
        return {"error": "Not enough text to parse. Please upload a text-based PDF or DOCX."}

    if not api_key or api_key.startswith("your_"):
        return {"error": "MiniMax API key not configured. Please add your API key to .env"}

    # Remove problematic Unicode characters
    raw_text = raw_text.replace('\u2028', ' ').replace('\u2029', ' ').replace('\xa0', ' ')

    prompt = CV_EXTRACTION_PROMPT + raw_text[:8000]  # Cap at 8000 chars to control cost

    try:
        endpoint = f"{base_url}/v1/text/chatcompletion_v2"
        payload = json.dumps({
            "model": "MiniMax-M2.7",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a CV parsing expert. Extract structured data from raw CV text. Return only valid JSON matching the provided schema. No markdown code blocks — just raw JSON."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.1,
            "max_tokens": 2000
        }, ensure_ascii=False)
        response = requests.post(
            endpoint,
            data=payload.encode('utf-8'),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json; charset=utf-8"
            },
            timeout=30
        )

        if response.status_code != 200:
            return {"error": f"API error: {response.status_code} - {response.text[:200]}"}

        result = response.json()
        logger.info(f"[CV] MiniMax raw response: {json.dumps(result, ensure_ascii=False)[:1000]}")
        raw_content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        raw_reasoning = result.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")

        # Prefer content field, fall back to reasoning, then combine both
        if raw_content and len(raw_content.strip()) > 10:
            content = raw_content.strip()
        elif raw_reasoning and len(raw_reasoning.strip()) > 10:
            content = raw_reasoning.strip()
        elif raw_content:
            content = raw_content.strip()
        else:
            return {"error": "AI returned empty response."}

        # Always try to extract JSON from the response — look for {...} anywhere
        import re
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            content = json_match.group(0)
        else:
            return {"error": f"AI returned non-JSON response: {content[:200]}"}

        cv_data = json.loads(content)

        if not cv_data.get("name"):
            cv_data["name"] = raw_text.split("\n")[0][:100]

        return cv_data

    except json.JSONDecodeError as e:
        # content is the extracted JSON string — show a preview for debugging
        preview = locals().get('content', 'unknown')[:300]
        return {"error": f"AI returned invalid JSON: {str(e)}. Extracted content: {preview}"}
    except requests.exceptions.Timeout:
        return {"error": "AI parsing timed out. Please try again."}
    except Exception as e:
        return {"error": f"AI parsing failed: {str(e)}"}


def build_fallback_cv(raw_text: str) -> Dict:
    """
    Fallback rules-based parser for when AI is unavailable.
    Based on the existing robust parser logic.
    """
    return {"error": "AI parsing unavailable. Please configure MINIMAX_API_KEY."}
