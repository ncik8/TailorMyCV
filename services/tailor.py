"""
Tailor service: Generates ATS-optimized, tailored CV content.
Uses an expert prompt to produce professional CV output with:
- Optimized summary in user's voice
- Experience bullets matched to job description language
- ATS-optimised structure
- Skills filtered and prioritized for the job
"""
import json
import re
from io import BytesIO

from services.minimax import chat

ATS_EXPERT_PROMPT = """You are an expert CV designer with 20 years experience creating award-winning CVs that consistently land interviews at top companies. Your CVs are renowned for:
- ATS (Applicant Tracking System) optimization — getting past automated screening filters
- Perfect keyword density and placement matching job descriptions
- Clean, scannable structure that recruiters and hiring managers love
- Impactful achievement statements that quantify results
- Preserving the candidate's authentic voice and personality

## YOUR TASK
Tailor the base CV + gap answers for the specific job description. Produce a complete, polished tailored CV.

## ATS RULES (critical — many CVs are rejected by automated filters)
1. Place job-relevant keywords naturally throughout — mirror exact phrasing from job description
2. Use standard section headers: "Professional Summary", "Experience", "Education", "Skills"
3. Avoid tables, text boxes, headers in sidebars, images, or special characters in key fields
4. Use plain text for contact info, no icons or symbols that ATS can't read
5. Format dates consistently: Month Year – Month Year (e.g., "Jan 2020 – Present")
6. Put most relevant/recent experience FIRST — ATS reads top-to-bottom
7. Include both raw skills AND skills mentioned in job description

## TAILORING RULES
1. ONLY use real experience from base CV + gap answers provided — NEVER fabricate
2. NEVER exaggerate: don't add years, team sizes, budget numbers that aren't supported
3. Reorder experience: most relevant job for this role goes FIRST
4. Rewrite bullets: mirror language patterns from job description naturally (e.g., if job says "spearheaded", use that)
5. Preserve user's voice — if they naturally say "built from scratch" keep that phrasing
6. Achievement-first bullets: lead with impact/result, add context second
7. For each experience item, select 3-5 bullets most relevant to THIS job, not all bullets

## OUTPUT FORMAT
Return ONLY a valid JSON object — no markdown, no explanation. Structure must match this exact schema:

{
  "personal": {
    "name": "Full Name",
    "email": "email@example.com",
    "phone": "+1 555 000 0000",
    "location": "City, State/Region",
    "linkedin": "linkedin.com/in/username",
    "website": "portfolio.com",
    "title": "Target Job Title"
  },
  "summary": "2-3 sentence professional summary in user's voice — first person, not third. Include years of experience, key expertise areas matching job, and one standout achievement or differentiator.",
  "experience": [
    {
      "title": "Job Title",
      "company": "Company Name",
      "location": "City, State",
      "start_date": "Jan 2020",
      "end_date": "Present",
      "bullets": [
        "Achievement statement using action verb, quantified result, context. 1-2 lines max. Mirror job description language.",
        "Second relevant achievement...",
        "Third relevant achievement..."
      ]
    }
  ],
  "skills": [
    {"name": "Skill Category or Specific Skill", "level": null},
    ...
  ],
  "education": [
    {"degree": "Degree Name", "school": "University Name", "year": "2020", "field": "Field of Study"}
  ],
  "certifications": [
    {"name": "Cert Name", "year": "2023"}
  ],
  "languages": [
    {"name": "Language", "level": "Native / Fluent / etc."}
  ]
}

## CRITICAL NOTES
- skills: List all relevant skills. Use job description keywords exactly as written.
- experience.bullets: Use varied action verbs (Built, Led, Delivered, Transformed, Scaled). Quantify with numbers when available. Keep to 1-2 lines each.
- summary: Write in first person, natural voice. Not generic.
- If base CV has no education/certifications/languages for a section, omit that section from output (don't add fake data).
- Keep language simple — no buzzwords, no padding.

## ATS KEYWORD OPTIMIZATION
The target job requires these specific keywords — use them naturally in your output where relevant:
{ats_keywords_list}

## CRITICAL: SUMMARY MUST INCLUDE ALL ATS KEYWORDS
Your "summary" field (2-3 sentences in first person) MUST organically weave in every single ATS keyword from the list above. The summary is the FIRST thing recruiters and ATS systems see — it must prove the candidate matches the job requirements at a glance. Do not just list keywords; use them naturally in sentences that describe the candidate's profile and achievements. Every skill/tool/technology from ats_keywords_list should appear in the summary."""


def tailor_cv(base_cv: dict, gap_answers: list, job_description: str, ats_keywords: list = None, requirements: dict = None) -> dict:
    """
    Tailor CV using base CV + gap answers + job description.
    
    Args:
        base_cv: Parsed CV data from cv_parser
        gap_answers: List of gap Q&A answers with 'requirement', 'user_answer', 'ai_phrased'
        job_description: Full job description text
        ats_keywords: List of ATS keywords from job requirements (optional, for summary generation)
        requirements: Dict with 'skills', 'certifications', 'tools', etc. from extract_requirements()
    
    Returns:
        Tailored CV dictionary with ATS-optimised structure
    """
    base_cv_str = json.dumps(base_cv, indent=2)
    
    # Format gap answers for the prompt
    gap_formatted = []
    for answer in gap_answers:
        gap_formatted.append({
            "requirement": answer.get("requirement", ""),
            "user_answer": answer.get("user_answer", ""),
            "ai_bullet": answer.get("ai_phrased", "")
        })
    gap_answers_str = json.dumps(gap_formatted, indent=2)
    
    # Format ats_keywords for the prompt
    ats_keywords_list = ', '.join(ats_keywords) if ats_keywords else 'None provided'
    
    # Build requirements summary for the summary generation section
    if requirements and isinstance(requirements, dict):
        parts = []
        for key in ['skills', 'certifications', 'tools']:
            items = requirements.get(key, [])
            if isinstance(items, list) and items:
                parts.append(f"{key}: " + ", ".join(str(i) for i in items))
        exp_years = requirements.get('experience_years', {})
        if isinstance(exp_years, dict) and exp_years:
            for k, v in exp_years.items():
                parts.append(f"Experience: {v}+ years in {k}")
        leadership = requirements.get('leadership', {})
        if isinstance(leadership, dict) and leadership:
            for k, v in leadership.items():
                parts.append(f"{k}: {v}")
        requirements_summary = '\n'.join(parts)
    else:
        requirements_summary = ats_keywords_list
    
    # Extract job title from job description (first line or sentence)
    job_title = ""
    if job_description:
        first_line = job_description.strip().split('\n')[0]
        job_title = first_line.strip()[:100]
    
    prompt = f"""{ATS_EXPERT_PROMPT}

## INPUT DATA

### Base CV (parsed from user's uploaded file):
{base_cv_str}

### Gap Answers (user's responses to job requirement gaps — use these to enrich experience bullets):
{gap_answers_str}

### Target Job Description:
{job_description}

### Job Requirements (ATS keywords the AI must weave into the summary):
{requirements_summary}

## NOW TAILOR THE CV
Output the complete tailored CV JSON following the schema above."""


    response = chat(prompt, prompt)
    
    if isinstance(response, dict) and "error" in response:
        return response
    
    try:
        text = response.strip()
        # Strip markdown code blocks if present
        if text.startswith("```"):
            parts = text.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("{") or part.startswith("["):
                    text = part
                    break
        
        # Try to find JSON object start
        json_start = text.find("{")
        if json_start != -1:
            text = text[json_start:]
        
        tailored = json.loads(text)
        
        # Post-process: ensure structure is complete
        tailored = _normalize_tailored_cv(tailored, base_cv)
        
        return tailored
        
    except json.JSONDecodeError as e:
        return {"error": f"Failed to parse tailored CV response: {str(e)}", "raw": response}


def _normalize_tailored_cv(tailored: dict, base_cv: dict) -> dict:
    """
    Normalize and validate the tailored CV structure.
    Fill in missing fields from base CV where needed.
    """
    # Ensure personal section exists
    if "personal" not in tailored:
        tailored["personal"] = {}
    
    personal = tailored["personal"]
    
    # Copy contact info from base if missing
    if not personal.get("name") and base_cv.get("name"):
        personal["name"] = base_cv["name"]
    if not personal.get("email") and base_cv.get("email"):
        personal["email"] = base_cv["email"]
    if not personal.get("phone") and base_cv.get("phone"):
        personal["phone"] = base_cv["phone"]
    if not personal.get("location") and base_cv.get("location"):
        personal["location"] = base_cv["location"]
    if not personal.get("linkedin") and base_cv.get("linkedin"):
        personal["linkedin"] = base_cv.get("linkedin")
    if not personal.get("website") and base_cv.get("website"):
        personal["website"] = base_cv.get("website")
    
    # Ensure title is set
    if not personal.get("title"):
        personal["title"] = tailored.get("title", "")
    
    # Ensure experience items have required fields
    if "experience" in tailored:
        for exp in tailored["experience"]:
            if "bullets" not in exp:
                exp["bullets"] = []
            # Normalize date fields
            if "dates" in exp:
                # Split dates into start_date and end_date
                dates = exp["dates"]
                if isinstance(dates, str) and " – " in dates:
                    parts = dates.split(" – ", 1)
                    if not exp.get("start_date"):
                        exp["start_date"] = parts[0]
                    if not exp.get("end_date"):
                        exp["end_date"] = parts[1] if len(parts) > 1 else "Present"
    
    # Ensure skills is a list of dicts with 'name' key
    if "skills" in tailored:
        normalized_skills = []
        for skill in tailored["skills"]:
            if isinstance(skill, str):
                normalized_skills.append({"name": skill, "level": None})
            elif isinstance(skill, dict):
                if "name" not in skill:
                    skill["name"] = str(skill.get("skill", skill.get("skill_name", "")))
                if "level" not in skill:
                    skill["level"] = None
                normalized_skills.append(skill)
        tailored["skills"] = normalized_skills
    
    # Ensure education items have required fields
    if "education" in tailored:
        for edu in tailored["education"]:
            if "degree" not in edu:
                # Try to extract from various formats
                if isinstance(edu, dict):
                    for key in ["degree", "title", "qualification"]:
                        if key in edu:
                            edu["degree"] = edu[key]
                            break
    
    # Sort experience: current/recent jobs first
    import re
    def _exp_sort_key(job):
        end = (job.get("end_date") or "").lower()
        start = job.get("start_date") or ""
        is_current = "present" in end or "current" in end
        year_match = re.search(r'\b(19|20)\d{2}\b', start)
        year = int(year_match.group()) if year_match else 1900
        return (0 if is_current else 1, -year)
    
    if "experience" in tailored and len(tailored["experience"]) > 1:
        tailored["experience"] = sorted(tailored["experience"], key=_exp_sort_key)
    
    return tailored


def generate_cv_pdf(tailored_cv: dict, template: str = "classic") -> bytes:
    """
    Generate PDF from tailored CV using specified template.
    
    Args:
        tailored_cv: The tailored CV dictionary
        template: Template name (modern, classic, minimal, creative, academic, bold)
    
    Returns:
        PDF bytes ready for download
    """
    from flask import render_template
    import tempfile
    import os
    
    template_map = {
        'modern': 'cv/style_1_modern/modern.html',
        'classic': 'cv/style_2_classic/classic.html',
        'minimal': 'cv/style_3_minimal/minimal.html',
        'creative': 'cv/style_4_creative/creative.html',
        'academic': 'cv/style_5_academic/academic.html',
        'bold': 'cv/style_6_bold/bold.html',
    }
    
    template_file = template_map.get(template, template_map['classic'])
    
    # Prepare data for template (map tailored_cv to template context)
    template_context = _prepare_template_context(tailored_cv)
    
    # Render HTML
    html_content = render_template(template_file, **template_context)
    
    # Generate PDF using WeasyPrint
    from weasyprint import HTML
    
    pdf_buffer = BytesIO()
    HTML(string=html_content).write_pdf(pdf_buffer)
    pdf_buffer.seek(0)
    
    return pdf_buffer.getvalue()


def _prepare_template_context(tailored_cv: dict) -> dict:
    """
    Prepare the tailored CV for template rendering.
    Maps the flat-ish structure from AI to what templates expect.
    """
    ctx = dict(tailored_cv)
    
    # The classic template expects 'personal' sub-object for contact info
    if "personal" not in ctx:
        ctx["personal"] = {
            "name": ctx.get("name", ""),
            "email": ctx.get("email", ""),
            "phone": ctx.get("phone", ""),
            "location": ctx.get("location", ""),
            "title": ctx.get("title", ""),
            "summary": ctx.get("summary", ""),
            "website": ctx.get("website", ""),
            "linkedin": ctx.get("linkedin", ""),
        }
    
    # Map experience format: some templates use 'highlights' not 'bullets'
    if "experience" in ctx:
        for exp in ctx["experience"]:
            if "bullets" in exp and "highlights" not in exp:
                exp["highlights"] = exp["bullets"]
    
    # For skills, some templates expect objects with name/level
    if "skills" in ctx:
        normalized = []
        for skill in ctx["skills"]:
            if isinstance(skill, str):
                normalized.append({"name": skill, "level": None})
            else:
                normalized.append(skill)
        ctx["skills"] = normalized
    
    return ctx