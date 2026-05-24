# TailorMyCV

**Your CV, tailored to any job — in 30 seconds.**

TailorMyCV is a SaaS tool where job seekers upload their CV (DOCX/PDF), paste a job posting URL, get a gap analysis, answer targeted questions about missing skills, and receive an AI-tailored CV ready to download as PDF.

## Features

- **CV Upload & Parse** — Upload DOCX or PDF, AI extracts structured data
- **LinkedIn Summary Import** — Paste your LinkedIn summary/About section directly (no auth, no scraping)
- **Manual Entry** — Step-by-step form if you don't have a CV file
- **Job URL Scraping** — Paste any job posting URL (Indeed, LinkedIn, etc.) or raw text
- **Gap Analysis** — AI compares your CV against job requirements
- **Gap Q&A** — Answer targeted questions about missing experience, AI converts to professional language
- **CV Tailoring** — AI tailors your CV using real content + gap answers, preserving your voice
- **PDF Download** — ATS-safe, clean modern template
- **Cover Letter Generation** — AI-generated from your CV and gap answers

## Tech Stack

- **Backend:** Python/Flask
- **Database:** Supabase (Postgres + Auth)
- **AI:** MiniMax API
- **PDF Generation:** WeasyPrint
- **Frontend:** HTML/Jinja2 templates

## Project Structure

```
tailormycv/
├── app.py                  # Flask application
├── requirements.txt        # Python dependencies
├── .env.example             # Environment variables template
├── static/
│   ├── style.css           # Global CSS
│   └── js/
│       └── app.js          # Frontend JavaScript
├── templates/
│   ├── base.html           # Base template
│   ├── index.html          # Landing page
│   ├── dashboard.html      # User dashboard
│   ├── cv_upload.html      # CV upload page
│   ├── job_paste.html      # Job URL/text paste
│   ├── gap_analysis.html  # Gap analysis display
│   ├── gap_qna.html        # Gap Q&A with modal
│   ├── tailored_cv.html    # Tailored CV preview
│   └── cover_letter.html   # Cover letter preview
├── services/
│   ├── minimax.py          # MiniMax API helper
│   ├── cv_parser.py        # DOCX/PDF → JSON
│   ├── job_scraper.py      # URL → job description
│   ├── gap_analyzer.py     # CV vs job gap analysis
│   ├── tailor.py           # AI CV tailoring
│   └── cover_letter.py     # Cover letter generation
├── prompts/                # AI prompt templates
│   ├── cv_parser.md
│   ├── gap_analyzer.md
│   ├── gap_qna.md
│   ├── cv_tailor.md
│   └── cover_letter.md
└── supabase/
    └── schema.sql          # Database schema
```

## Local Setup

### Prerequisites

- Python 3.9+
- Supabase account (free tier works)
- MiniMax API account

### Step 1: Clone and Install Dependencies

```bash
cd ~/tailormycv
pip install -r requirements.txt
```

### Step 2: Set Up Supabase

1. Create a new Supabase project at [supabase.com](https://supabase.com)
2. Go to **SQL Editor** in your Supabase dashboard
3. Run the contents of `supabase/schema.sql`
4. Go to **Settings → API** and copy:
   - `SUPABASE_URL` (Project URL)
   - `SUPABASE_KEY` (anon public key)

### Step 3: Get MiniMax API Key

1. Sign up at [minimax.chat](https://minimax.chat)
2. Go to API keys section
3. Create a new API key

### Step 4: Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your values:

```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key
MINIMAX_API_KEY=your-minimax-key
MINIMAX_BASE_URL=https://api.minimax.chat
FLASK_SECRET_KEY=your-random-secret-key
```

### Step 5: Run the App

```bash
cd ~/tailormycv
python app.py
```

The app will start at `http://localhost:5000`

## User Flow

1. **Upload CV** → `/cv/upload` — Upload DOCX/PDF
2. **Parse CV** → AI extracts name, email, experience, skills, education
3. **Paste Job** → `/job/paste` — URL or raw text
4. **Scrape Job** → AI extracts job requirements
5. **Gap Analysis** → `/gap/analysis` — Shows matches/partials/missing
6. **Answer Gaps** → Modal Q&A for each gap
7. **Tailor CV** → `/cv/tailor` — AI generates tailored CV
8. **Preview/Download** → `/cv/preview` — HTML preview + PDF download
9. **Cover Letter** → `/cover-letter/preview` — Generated cover letter

## API Routes

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/` | Landing page |
| GET | `/dashboard` | User dashboard |
| GET | `/cv/upload` | CV upload page |
| POST | `/cv/parse` | Parse uploaded CV |
| GET | `/job/paste` | Job paste page |
| POST | `/job/scrape` | Scrape job URL or parse text |
| GET | `/gap/analysis` | Gap analysis display |
| POST | `/gap/answer` | Submit gap Q&A answer |
| GET | `/gap/qna` | Gap Q&A page |
| POST | `/cv/tailor` | Generate tailored CV |
| GET | `/cv/preview` | Preview tailored CV |
| GET | `/cv/download` | Download CV as PDF |
| POST | `/cover-letter` | Generate cover letter |
| GET | `/cover-letter/preview` | Preview cover letter |

## Testing the Flow

1. Start the app: `python app.py`
2. Open `http://localhost:5000`
3. Upload a sample CV (DOCX or PDF)
4. Paste a job URL (or job description text)
5. Review gap analysis
6. Answer a gap question
7. Generate tailored CV
8. Preview and download PDF

## Notes

- Session data is stored in memory (filesystem-based for demo)
- For production, use Supabase or Redis for session storage
- WeasyPrint requires specific system dependencies; if PDF download fails, check WeasyPrint installation
- Job scraping may fail for some sites; always provide paste text fallback

## License

MIT