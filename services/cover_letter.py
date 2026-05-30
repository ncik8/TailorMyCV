import json
from services.minimax import chat

COVER_LETTER_PROMPT = """You are an expert cover letter writer. Write a professional cover letter for this job application.

Structure:
- Opening: Start with "To Whom It May Concern" on its own line, then "In reference to the position of {title} at {company}" on the next line, then a blank line before the body
- Body: 1-2 paragraphs highlighting your most relevant experience
- Closing: Call to action, thank them

Rules:
- Use real content from CV + gap answers only
- Tone: {tone}
- Length: ~300-400 words
- Never fabricate dates, titles, or achievements
- Personalize for the specific company/role
- ALWAYS start with exactly:
  To Whom It May Concern
  In reference to the position of [JOB TITLE] at [COMPANY NAME]

Input:
- CV: {cv}
- Gap answers: {gap_answers}
- Job description: {job_description}
- Company: {company}
- Job title: {title}

Return ONLY the cover letter text. No headers like "Cover Letter:" - just start with "To Whom It May Concern"."""

TONE_INSTRUCTIONS = {
    "professional": "Formal but warm. Confident without being arrogant.",
    "confident": "Assertive and direct. Strong leadership presence.",
    "friendly": "Warm and approachable. Shows personality while remaining professional."
}


def generate_cover_letter(cv: dict, gap_answers: list, job_description: str, company: str, job_title: str, tone: str = "professional") -> str:
    """Generate cover letter from CV + gap answers + job description."""
    tone_instruction = TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS["professional"])
    
    cv_str = json.dumps(cv, indent=2)
    gap_answers_str = json.dumps(gap_answers, indent=2)
    
    prompt = COVER_LETTER_PROMPT.format(
        cv=cv_str,
        gap_answers=gap_answers_str,
        job_description=job_description,
        company=company or "the company",
        title=job_title or "the role",
        tone=tone_instruction
    )
    
    response = chat(prompt, prompt)
    
    if isinstance(response, dict) and "error" in response:
        return response.get("error", "Failed to generate cover letter")
    
    return response.strip()