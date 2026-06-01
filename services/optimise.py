import json
from services.minimax import chat


def optimise_cv_for_ats(cv: dict, job_description: str, ats_keywords: list, requirements: dict) -> dict:
    """Rewrite CV bullets to maximise ATS keyword match."""
    ats_keywords_str = json.dumps(ats_keywords, indent=2) if ats_keywords else "[]"
    requirements_str = json.dumps(requirements, indent=2) if requirements else "{}"
    cv_str = json.dumps(cv, indent=2)

    prompt = f"""You are an ATS CV optimisation specialist. Rewrite the CV experience bullets to maximise keyword match with the job requirements.

IMPORTANT: Only rewrite the experience bullets. Do NOT change personal info, summary, skills, or education sections.

Priority keywords to weave into bullets (in order of importance):
1. Retail, properties, distribution / wholesale
2. Pipeline forecasting and sales targets / quota / revenue
3. Presales, marketing collaboration, campaigns
4. Implementation, delivery, customer success
5. Stakeholder management, enterprise sales, key accounts, C-suite
6. Microsoft Dynamics, Salesforce CRM, sales reporting

INPUT CV:
{cv_str}

ATS KEYWORDS:
{ats_keywords_str}

JOB DESCRIPTION:
{job_description}

JOB REQUIREMENTS:
{requirements_str}

Rewrite ONLY the experience bullets (the 'highlights' array in each job entry). For each bullet:
- If it already covers an ATS keyword, keep it but make the keyword more prominent
- If a keyword is missing from that job entry, rewrite the bullet to naturally absorb 1-2 keywords
- If a critical ATS keyword has no bullet covering it at all, ADD a new bullet in the most relevant job entry

Rules:
- NEVER fabricate jobs, dates, titles, or achievements
- Keep each bullet truthful to your real experience
- Rewrite bullets in place — change only what needs to change
- Each bullet should be 1-2 lines max
- Add new bullets only for truly missing critical keywords

Return ONLY a JSON object with this exact structure (no markdown, no explanation):
{{
  "personal": {{ keep exactly the same }},
  "summary": {{ keep exactly the same }},
  "experience": [
    {{
      "title": "keep same",
      "company": "keep same",
      "dates": "keep same",
      "highlights": ["rewritten bullet 1", "rewritten bullet 2", "..."]
    }}
  ],
  "skills": {{ keep exactly the same }},
  "education": {{ keep exactly the same }}
}}"""

    response = chat(prompt, prompt)

    if isinstance(response, dict) and "error" in response:
        return cv

    try:
        result = json.loads(str(response))
        if "experience" in result and "personal" in result:
            return result
        else:
            return cv
    except (json.JSONDecodeError, TypeError):
        return cv