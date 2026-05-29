import json
from services.minimax import chat

REQUIREMENTS_EXTRACTOR_PROMPT = """You are an expert job requirement analyst. Extract all hard requirements from this job description.

Categorize them into:
- skills: specific technical/hard skills (e.g., Python, SQL, Project Management)
- experience_years: years of experience required for specific domains (e.g., {"project_management": 5, "agile": 3})
- certifications: required degrees, certs, licenses (e.g., PMP, MBA, CPA)
- leadership: management/team size requirements (e.g., {"team_size": 10, "title": "Manager"})
- tools: specific tools/technologies mentioned (e.g., Jira, AWS, Salesforce)
- other: other hard requirements (e.g., "valid drivers license", "clean driving record")

Return ONLY a valid JSON object with these exact keys. No markdown, no explanation.
Example output:
{"skills": ["Python", "SQL", "Project Management"], "experience_years": {"project_management": 5}, "certifications": [], "leadership": {}, "tools": ["Jira", "AWS"], "other": []}"""

GAP_ANALYZER_PROMPT = """You are an expert CV analyst. Compare the CV against the job requirements.

For each requirement, determine if it's:
- MET: The CV clearly satisfies this requirement
- PARTIAL: The CV partially satisfies this (e.g., 4 years vs 5 years required)
- MISSING: The CV doesn't address this requirement at all

For PARTIAL or MISSING requirements, provide a SPECIFIC question to ask the user that would help fill the gap.

Requirements format: {requirements}

CV format: {cv}

Return ONLY a valid JSON object with this exact structure:
{{
  "matches": [{{"requirement": "...", "status": "MET", "cv_evidence": "..."}}],
  "partials": [{{"requirement": "...", "status": "PARTIAL", "cv_evidence": "...", "gap": "...", "question": "..."}}],
  "missing": [{{"requirement": "...", "status": "MISSING", "question": "..."}}]
}}

No markdown, no explanation. Pure JSON only."""


# ATS Keyword Scorer — checks how many job keywords appear in the CV
def score_ats_keywords(cv_json: dict, requirements: dict) -> dict:
    """
    Check how many ATS keywords from the job appear in the CV.
    Returns: {ats_score: 0-100, found: [...], missing: [...]}
    """
    import re
    # Build flat list of ATS keywords from requirements
    keywords = []
    # Check all 6 categories, including experience_years and leadership dicts
    for category in ['skills', 'certifications', 'tools', 'other', 'experience_years', 'leadership']:
        for item in requirements.get(category, []):
            if isinstance(item, str) and item:
                keywords.append(item.lower())
            elif isinstance(item, dict):
                # experience_years and leadership have keys like "enterprise_AI_initiatives"
                # that are the actual requirement names, and string values like "Manager"
                for k, v in item.items():
                    if k.strip():
                        keywords.append(k.lower())
                    if isinstance(v, str) and v.strip():
                        keywords.append(v.lower())
            elif isinstance(item, (int, float)):
                # Numeric values (like years) — skip as ATS keywords
                pass

    # Scan CV text (name, summary, skills, experience bullets, education)
    cv_text_parts = [
        cv_json.get('name', ''),
        cv_json.get('title', ''),
        cv_json.get('summary', ''),
    ]
    for exp in cv_json.get('experience', []):
        cv_text_parts.append(exp.get('title', '') or '')
        cv_text_parts.append(exp.get('company', '') or '')
        cv_text_parts.append(exp.get('description', '') or '')
        for bullet in exp.get('bullets', []):
            cv_text_parts.append(bullet if isinstance(bullet, str) else bullet.get('text', ''))
    for skill in cv_json.get('skills', []):
        cv_text_parts.append(skill if isinstance(skill, str) else skill.get('name', ''))
    for edu in cv_json.get('education', []):
        cv_text_parts.append(edu.get('degree', '') or '')
        cv_text_parts.append(edu.get('field', '') or '')
        cv_text_parts.append(edu.get('school', '') or '')

    cv_text = ' '.join(str(p) for p in cv_text_parts).lower()

    found = []
    missing = []
    for kw in keywords:
        # Match whole word (with word boundary)
        pattern = r'\b' + re.escape(kw) + r'\b'
        if re.search(pattern, cv_text):
            found.append(kw)
        else:
            missing.append(kw)

    score = int((len(found) / max(len(keywords), 1)) * 100) if keywords else 0
    return {'ats_score': score, 'found': found, 'missing': missing}


def extract_requirements(job_description: str) -> dict:
    """Extract requirements from job description using MiniMax."""
    response = chat(REQUIREMENTS_EXTRACTOR_PROMPT, job_description)
    
    # DEBUG: log raw response
    print(f"[DEBUG extract_requirements] raw response type: {type(response)}, value: {repr(response)[:500]}")
    
    if isinstance(response, dict) and "error" in response:
        print(f"[DEBUG extract_requirements] API error returned: {response}")
        return response
    
    try:
        # Try to find JSON in the response
        text = response.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        print(f"[DEBUG extract_requirements] parsed requirements keys: {list(result.keys()) if isinstance(result, dict) else 'not a dict'}")
        return result
    except json.JSONDecodeError as e:
        print(f"[DEBUG extract_requirements] JSON decode error: {e}, raw text: {repr(text)[:300]}")
        return {"error": f"Failed to parse requirements: {e}", "raw": response}
def analyze_gaps(cv_json: dict, requirements: dict) -> dict:
    """Analyze gaps between CV and job requirements. Includes ATS keyword scoring."""
    cv_str = json.dumps(cv_json, indent=2)
    req_str = json.dumps(requirements)

    prompt = GAP_ANALYZER_PROMPT.format(cv=cv_str, requirements=req_str)
    response = chat(prompt, prompt)

    if isinstance(response, dict) and "error" in response:
        return response

    try:
        text = response.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)

        # Calculate interview likelihood score
        missing = result.get('missing', [])
        partials = result.get('partials', [])
        matches = result.get('matches', [])
        interview_likelihood = max(0, min(100, 80 - len(missing) * 15 - len(partials) * 7))
        requirement_count = len(matches) + len(partials) + len(missing)
        covered_count = len(matches)

        result['interview_likelihood'] = interview_likelihood
        result['requirement_count'] = requirement_count
        result['covered_count'] = covered_count

        # ATS keyword scoring
        ats_result = score_ats_keywords(cv_json, requirements)
        result['ats_score'] = ats_result['ats_score']
        result['ats_keywords_found'] = ats_result['found']
        result['ats_keywords_missing'] = ats_result['missing']

        # Boost interview likelihood by ATS score (max +10 points)
        ats_boost = int((ats_result['ats_score'] - 50) / 10)  # -5 to +5 range
        result['interview_likelihood'] = max(0, min(100, interview_likelihood + ats_boost))

        return result
    except json.JSONDecodeError:
        return {"error": "Failed to parse gaps", "raw": response}


# ATS_KEYWORD_SCORER_PROMPT = ..."""  # (defined below as global)


GAP_QNA_PROMPT = """You are an expert CV writer. Convert the user's informal answer into professional CV bullet point language.

Rules:
- Keep facts accurate. Don't exaggerate.
- Preserve the user's voice and phrasing style.
- Write in third person, past tense for CV bullets.
- Make it impactful but honest.

Job requirement being addressed: {requirement}
User's original answer: {answer}

Return ONLY the rewritten CV bullet point. Nothing else. Keep it to 1-2 sentences max."""


def convert_answer_to_cv_language(requirement: str, user_answer: str) -> str:
    """Convert user's informal answer to professional CV language."""
    prompt = GAP_QNA_PROMPT.format(requirement=requirement, answer=user_answer)
    response = chat(prompt, prompt)

    if isinstance(response, dict) and "error" in response:
        return user_answer

    return response.strip()


GAP_INTERPRET_PROMPT = """You are an expert CV analyst and career coach. The user just answered a question about a job requirement gap.

Their answer: "{answer}"
Requirement: "{requirement}"

Step 1 - Rewrite their answer in professional CV bullet language.
- Third person, past tense
- 1-2 sentences max
- Impactful but honest, don't exaggerate

Step 2 - Decide where this information belongs on their CV. Choose one:
- "job_bullet": A work experience bullet point (most common)
- "skill": A hard/soft skill to add to their skills list
- "language": A spoken/programming language
- "certification": A professional certification or degree
- "project": A project they worked on
- "other": Doesn't fit above categories

Step 3 - If "job_bullet", generate up to 3 destination options from their work history.
If another category, describe what would be added.

CV structure:
{cv_str}

Return ONLY a valid JSON object. No markdown, no explanation:
{{
  "interpreted": "Rewritten CV bullet or skill description in professional language",
  "category": "job_bullet" | "skill" | "language" | "certification" | "project" | "other",
  "category_label": "Short human label for the pick button",
  "destinations": [
    {{"type": "job", "job_idx": 0, "title": "Software Engineer", "company": "Amazon", "years": "2020-2022"}},
    ...up to 3 most relevant jobs...
    {{"type": "category", "label": "Add as Skill"}},
    {{"type": "category", "label": "Add as Language"}},
    {{"type": "category", "label": "Add as Certification"}},
    {{"type": "category", "label": "Add as Project"}},
    {{"type": "category", "label": "Add to Summary"}}
  ]
}}"""


def interpret_gap_answer(cv_json: dict, requirement: str, user_answer: str) -> dict:
    """
    Step 1 of the gap Q&A flow.
    Takes user's raw answer, rewrites it professionally, figures out category + destinations.
    """
    cv_str = json.dumps(cv_json, indent=2)
    prompt = GAP_INTERPRET_PROMPT.format(
        answer=user_answer,
        requirement=requirement,
        cv_str=cv_str
    )
    response = chat(prompt, prompt)

    if isinstance(response, dict) and "error" in response:
        return {
            "interpreted": user_answer,
            "category": "other",
            "category_label": "Other",
            "destinations": [{"type": "category", "label": "Add to Summary"}]
        }

    try:
        text = response.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "interpreted": user_answer,
            "category": "other",
            "category_label": "Other",
            "destinations": [{"type": "category", "label": "Add to Summary"}]
        }


GAP_UPDATE_PROFILE_PROMPT = """You are an expert CV writer. Apply a confirmed answer to a CV profile.

Original user answer: "{answer}"
Professional rewrite: "{interpreted}"
Requirement: "{requirement}"
Category: "{category}"

CV current state (partial):
{cv_str}

Apply the interpreted text to the appropriate place in the CV structure.

For job bullets: Add to the specified job's bullets list.
For skills: Add to the skills array.
For certifications: Add to the certs array.
For projects: Add to the projects array.
For summary: Append to the summary field.

Return ONLY a valid JSON object representing the UPDATED CV section (the part that was modified):
{{
  "applied_to": "job_0" | "skills" | "certifications" | "projects" | "summary",
  "applied_text": "The exact text that was added or updated",
  "cv_modification": {{ ...full updated section... }}
}}

All other CV sections must be included in cv_modification unchanged.

Return pure JSON only, no markdown."""


def apply_gap_answer_to_profile(cv_json: dict, requirement: str, user_answer: str,
                                  interpreted: str, category: str,
                                  destination: dict) -> dict:
    """
    Apply a confirmed gap answer to the CV profile and return the updated CV data.
    """
    cv_str = json.dumps(cv_json, indent=2)
    prompt = GAP_UPDATE_PROFILE_PROMPT.format(
        answer=user_answer,
        interpreted=interpreted,
        requirement=requirement,
        category=category,
        cv_str=cv_str
    )
    response = chat(prompt, prompt)

    if isinstance(response, dict) and "error" in response:
        return {"error": "Failed to apply answer", "raw": response}

    try:
        text = response.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": "Failed to parse CV modification", "raw": response}


GAP_QUESTIONS_PROMPT = """You are an expert career coach. For each gap (PARTIAL or MISSING requirement), generate exactly 3 targeted questions:

1. A yes/no question asking if the user has this skill/experience
2. A question asking which job/role/context they gained it in
3. A question asking if we can update their profile with this information

Requirements to generate questions for:
{gap_requirements}

Return ONLY a valid JSON object mapping each requirement to an array of 3 question strings:
{{
  "requirement_name": ["Question 1 about having the skill?", "Question 2 about which job/role?", "Question 3 about updating profile?"],
  ...
}}

No markdown, no explanation. Pure JSON only."""


def generate_gap_questions(gaps: dict) -> dict:
    """Generate targeted 3-question sets for each gap requirement.
    
    Args:
        gaps: dict with partials and missing arrays from analyze_gaps
    
    Returns:
        dict mapping each requirement to a list of 3 questions:
        1) Does user have X skill?
        2) Which job/role/context?
        3) Can we update profile?
    """
    # Collect all gap requirements
    gap_requirements = []
    
    if gaps.get('partials'):
        for p in gaps['partials']:
            gap_requirements.append({
                'requirement': p.get('requirement', ''),
                'type': 'PARTIAL',
                'context': p.get('cv_evidence', ''),
                'question': p.get('question', '')
            })
    
    if gaps.get('missing'):
        for m in gaps['missing']:
            gap_requirements.append({
                'requirement': m.get('requirement', ''),
                'type': 'MISSING',
                'context': '',
                'question': m.get('question', '')
            })
    
    if not gap_requirements:
        return {}
    
    req_str = json.dumps(gap_requirements, indent=2)
    prompt = GAP_QUESTIONS_PROMPT.format(gap_requirements=req_str)
    
    response = chat(prompt, prompt)
    
    if isinstance(response, dict) and "error" in response:
        return _fallback_generate_questions(gap_requirements)
    
    try:
        text = response.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except json.JSONDecodeError:
        return _fallback_generate_questions(gap_requirements)


def _fallback_generate_questions(gap_requirements: list) -> dict:
    """Fallback: generate 3 standard questions per requirement when AI fails."""
    result = {}
    for req in gap_requirements:
        requirement = req.get('requirement', '')
        result[requirement] = [
            f"Do you have experience with {requirement}?",
            f"Which job or project did you gain this experience in?",
            f"Can we add this to your CV profile?"
        ]
    return result