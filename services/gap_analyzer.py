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


def extract_requirements(job_description: str) -> dict:
    """Extract requirements from job description using MiniMax."""
    response = chat(REQUIREMENTS_EXTRACTOR_PROMPT, job_description)
    
    if isinstance(response, dict) and "error" in response:
        return response
    
    try:
        # Try to find JSON in the response
        text = response.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": "Failed to parse requirements", "raw": response}


def analyze_gaps(cv_json: dict, requirements: dict) -> dict:
    """Analyze gaps between CV and job requirements."""
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
        
        return result
    except json.JSONDecodeError:
        return {"error": "Failed to parse gaps", "raw": response}


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