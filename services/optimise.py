import json
from services.minimax import chat


def optimise_cv_for_ats(cv: dict, job_description: str, ats_keywords: list, requirements: dict, gap_answers=None) -> dict:
    """Rewrite CV to maximise ATS keyword match, using profile, tailored CV, and gap answers as context."""

    ats_keywords_str = json.dumps(ats_keywords, indent=2) if ats_keywords else "[]"
    requirements_str = json.dumps(requirements, indent=2) if requirements else "{}"
    cv_str = json.dumps(cv, indent=2)
    gap_str = json.dumps(gap_answers, indent=2) if gap_answers else "[]"

    prompt = f"""You are an ATS CV optimisation specialist. Rewrite the CV to maximise keyword match with the job requirements. You may change any field — personal, summary, experience, skills, education — as long as it stays truthful to the source data.

SOURCES YOU CAN DRAW FROM:
1. The current tailored CV
2. Gap answers (context from previous Q&A with the jobseeker)
3. Profile data already in the CV

RULES:
- NEVER fabricate jobs, dates, titles, or achievements that don't exist in the source data
- NEVER remove or omit the summary section — it must always be present in the output
- You MAY rewrite, expand, shorten, or restructure the summary to improve ATS keyword match, but it must always exist
- You MAY rewrite, expand, shorten, or restructure any field to improve ATS keyword match
- Use gap answers to fill in missing experience context — weave those details into relevant job entries
- Keep the CV authentic — don't exaggerate or claim skills not supported by the sources

ATS PRIORITY KEYWORDS (in order of importance):
1. Retail, properties, distribution / wholesale
2. Pipeline forecasting and sales targets / quota / revenue
3. Presales, marketing collaboration, campaigns
4. Implementation, delivery, customer success
5. Stakeholder management, enterprise sales, key accounts, C-suite
6. Microsoft Dynamics, Salesforce CRM, sales reporting
7. AI technologies

MANDATORY OUTPUT RULE — DO NOT BREAK THIS RULE:
The "summary" field is MANDATORY and MUST appear in your JSON output. It must be a 2-3 sentence professional summary in first person. You MAY rewrite it to optimise for ATS keywords, but you MUST output it — never return null, never return empty string, never omit it.

INPUT CV:
{cv_str}

GAP ANSWERS (use these to enrich experience bullets):
{gap_str}

ATS KEYWORDS:
{ats_keywords_str}

JOB DESCRIPTION:
{job_description}

JOB REQUIREMENTS:
{requirements_str}

Return a complete rewritten CV as JSON. You may change all fields — every field is eligible for optimisation. Only the factual core (real jobs, real dates, real titles) must be preserved.
Return ONLY a JSON object with this exact structure (no markdown, no explanation):
{{
  "personal": {{ ... }},
  "summary": {{ rewrite to optimise for ATS but must ALWAYS be present — never return empty or null }},
  "experience": [
    {{
      "title": "...",
      "company": "...",
      "dates": "...",
      "highlights": ["...", "..."]
    }}
  ],
  "skills": {{ ... }},
  "education": {{ ... }}
}}"""

    response = chat(prompt, prompt)

    if isinstance(response, dict) and "error" in response:
        return cv

    try:
        result = json.loads(str(response))
        if "experience" in result and "personal" in result:
            # Safeguard: ensure summary is always present
            if not result.get("summary") and cv.get("summary"):
                result["summary"] = cv["summary"]
            return result
        else:
            return cv
    except (json.JSONDecodeError, TypeError):
        return cv