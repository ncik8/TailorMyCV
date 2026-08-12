# Job Requirements Extractor
Extract HARD requirements from this job description. Be conservative — only include what is genuinely required (not nice-to-haves, not culture statements, not responsibilities).

Categorise them into:
- skills: specific technical/hard skills (e.g. Python, SQL, Project Management)
- experience_years: years of experience required for specific domains (e.g. {"project_management": 5}). ONLY include if the JD explicitly states a numeric years requirement.
- certifications: required degrees, certs, licenses (e.g. PMP, MBA, CPA). ONLY include if explicitly required.
- leadership: management/team size requirements (e.g. {"team_size": 10}). ONLY include if team size or reporting level is explicit.
- tools: specific tools/technologies mentioned (e.g. Jira, AWS, Salesforce, HubSpot)
- other: STRICTLY use this category ONLY for hard external constraints — work location (e.g. "must be based in Hong Kong"), work authorisation, security clearance, language requirement. DO NOT include soft asks, nice-to-haves, culture statements, mission/values statements, or generic role descriptions. These should be FILTERED OUT.

Be especially wary of:
- Lifting duty descriptions from "What You'll Do" or "Responsibilities" sections — these describe the job, not requirements.
- Including aspirational or culture statements ("passionate about X", "drive innovation") — filter these out.
- Inferring requirements not stated (e.g. "must know Excel" when JD says "advanced Excel user").

Return ONLY a valid JSON object with these exact keys. No markdown, no explanation.
Example output:
{"skills": ["Python", "SQL", "Project Management"], "experience_years": {"project_management": 5}, "certifications": [], "leadership": {}, "tools": ["Jira", "AWS"], "other": []}
