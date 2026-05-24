import os
import json
import tempfile
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from flask_cors import CORS
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
import base64

# Services
from services.cv_parser import parse_cv
from services.job_scraper import scrape_job_url, parse_job_text
from services.gap_analyzer import extract_requirements, analyze_gaps, convert_answer_to_cv_language, generate_gap_questions
from services.tailor import tailor_cv, generate_cv_pdf
from services.cover_letter import generate_cover_letter

load_dotenv()

app = Flask(__name__, template_folder='templates', root_path=os.path.dirname(os.path.abspath(__file__)))
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")
CORS(app)

# Session storage for multi-step flow
# In production, use Supabase or Redis
app.config['SESSION_TYPE'] = 'filesystem'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

ALLOWED_EXTENSIONS = {'pdf', 'docx', 'doc'}


def _prepare_cv_context(tailored_cv: dict) -> dict:
    """
    Prepare tailored CV for template rendering.
    Maps the flat-ish structure from AI to what templates expect.
    """
    ctx = dict(tailored_cv)
    
    # The templates expect 'personal' sub-object for contact info
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
    
    # Map experience format: templates use 'highlights' not 'bullets'
    if "experience" in ctx:
        for exp in ctx["experience"]:
            if "bullets" in exp and "highlights" not in exp:
                exp["highlights"] = exp["bullets"]
    
    # For skills, templates expect objects with name/level
    if "skills" in ctx:
        normalized = []
        for skill in ctx["skills"]:
            if isinstance(skill, str):
                normalized.append({"name": skill, "level": None})
            else:
                normalized.append(skill)
        ctx["skills"] = normalized
    
    return ctx


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def init_session():
    """Initialize session data if not present."""
    if 'cv_data' not in session:
        session['cv_data'] = None
    if 'job_data' not in session:
        session['job_data'] = None
    if 'requirements' not in session:
        session['requirements'] = None
    if 'gaps' not in session:
        session['gaps'] = None
    if 'gap_answers' not in session:
        session['gap_answers'] = []
    if 'tailored_cv' not in session:
        session['tailored_cv'] = None
    if 'cv_template' not in session:
        session['cv_template'] = 'classic'  # default template
    if 'profile' not in session:
        session['profile'] = None


# ============ ROUTES ============

@app.route('/')
def index():
    """Landing page."""
    return render_template('index.html')


@app.route('/dashboard')
def dashboard():
    """User dashboard after login."""
    init_session()
    return render_template('dashboard.html')


@app.route('/cv/upload')
def cv_upload_page():
    """CV upload page."""
    init_session()
    return render_template('cv_upload.html')


@app.route('/cv/parse', methods=['POST'])
def parse_cv_route():
    """API: Parse uploaded CV file, then redirect to edit profile page."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Unsupported file type. Please upload DOCX or PDF.'}), 400

    try:
        cv_data = parse_cv(file)
        session['cv_data'] = cv_data
        return jsonify({'success': True, 'redirect': url_for('edit_profile_page')})
    except Exception as e:
        return jsonify({'error': f'Failed to parse CV: {str(e)}'}), 500


@app.route('/cv/edit-profile')
def edit_profile_page():
    """Edit profile page — shows parsed CV data for review/editing."""
    init_session()
    cv_data = session.get('cv_data')
    if cv_data:
        profile = cv_data.copy()
    else:
        profile = {
            'name': '', 'email': '', 'phone': '', 'location': '',
            'linkedin': '', 'title': '', 'summary': '',
            'experience': [], 'skills': [], 'education': [],
            'projects': [], 'certifications': [], 'languages': [],
        }
    return render_template('edit_profile.html', profile=profile)


@app.route('/cv/save-profile', methods=['POST'])
def save_profile_route():
    """Save edited profile data to session, then redirect to job paste."""
    profile = {
        'name': request.form.get('name', '').strip(),
        'email': request.form.get('email', '').strip(),
        'phone': request.form.get('phone', '').strip(),
        'location': request.form.get('location', '').strip(),
        'linkedin': request.form.get('linkedin', '').strip(),
        'title': request.form.get('title', '').strip(),
        'summary': request.form.get('summary', '').strip(),
        'experience': [],
        'skills': [],
        'education': [],
        'projects': [],
        'certifications': [],
        'languages': [],
    }

    # Parse skills (comma-separated in hidden field)
    skills_raw = request.form.get('skills', '').strip()
    if skills_raw:
        profile['skills'] = [s.strip() for s in skills_raw.split(',') if s.strip()]

    # Parse experience entries
    exp_titles = request.form.getlist('exp_title')
    exp_companies = request.form.getlist('exp_company')
    exp_starts = request.form.getlist('exp_start')
    exp_ends = request.form.getlist('exp_end')

    for i, title in enumerate(exp_titles):
        if title.strip():
            exp = {
                'title': title.strip(),
                'company': exp_companies[i].strip() if i < len(exp_companies) else '',
                'start_date': exp_starts[i].strip() if i < len(exp_starts) else '',
                'end_date': exp_ends[i].strip() if i < len(exp_ends) else '',
                'bullets': [],
            }
            for key, val in request.form.items():
                if key.startswith(f'exp_bullet_{i}_'):
                    bullet_val = val.strip()
                    if bullet_val:
                        exp['bullets'].append(bullet_val)
            profile['experience'].append(exp)

    # Parse education entries
    edu_degrees = request.form.getlist('edu_degree')
    edu_fields = request.form.getlist('edu_field')
    edu_schools = request.form.getlist('edu_school')
    edu_years = request.form.getlist('edu_year')

    for i, degree in enumerate(edu_degrees):
        if degree.strip():
            edu = {
                'degree': degree.strip(),
                'field': edu_fields[i].strip() if i < len(edu_fields) else '',
                'school': edu_schools[i].strip() if i < len(edu_schools) else '',
                'year': edu_years[i].strip() if i < len(edu_years) else '',
            }
            profile['education'].append(edu)

    session['profile'] = profile
    session['cv_data'] = profile

    return redirect(url_for('job_paste_page'))


@app.route('/job/paste')
def job_paste_page():
    """Job URL/text paste page."""
    init_session()
    return render_template('job_paste.html')


@app.route('/job/scrape', methods=['POST'])
def scrape_job_route():
    """API: Scrape job URL or parse pasted text."""
    data = request.get_json()
    
    if data.get('text'):
        # User pasted raw job description
        job_data = parse_job_text(data['text'])
    elif data.get('url'):
        # User provided a URL
        job_data = scrape_job_url(data['url'])
    else:
        return jsonify({'error': 'No URL or text provided'}), 400
    
    session['job_data'] = job_data
    
    # Determine source label
    if data.get('text'):
        source = 'pasted'
    else:
        source = 'url'
    
    # Also extract requirements
    if job_data.get('description'):
        requirements = extract_requirements(job_data['description'])
        session['requirements'] = requirements
    else:
        session['requirements'] = {'skills': [], 'experience_years': {}, 'certifications': [], 'leadership': {}, 'tools': [], 'other': []}
    
    return jsonify({
        'success': True,
        'job': job_data,
        'requirements': session['requirements'],
        'text': job_data.get('description', ''),
        'source': source
    })


@app.route('/job/confirm', methods=['POST'])
def confirm_job_route():
    """API: User confirmed the job description text."""
    data = request.get_json()
    confirmed_text = data.get('text', '').strip()
    
    if not confirmed_text:
        return jsonify({'error': 'No job description provided'}), 400
    
    if len(confirmed_text) < 50:
        return jsonify({'error': 'Job description seems too short. Please provide more detail.'}), 400
    
    # Update job_data with confirmed text
    job_data = session.get('job_data', {})
    job_data['description'] = confirmed_text
    session['job_data'] = job_data
    
    # Re-extract requirements from confirmed text
    requirements = extract_requirements(confirmed_text)
    session['requirements'] = requirements
    
    return jsonify({'success': True, 'requirements': requirements})


@app.route('/gap/analyze', methods=['POST'])
def analyze_gaps_route():
    """API: Analyze gaps between CV and job requirements."""
    cv_data = session.get('cv_data')
    requirements = session.get('requirements')
    
    if not cv_data:
        return jsonify({'error': 'No CV data. Please upload your CV first.'}), 400
    
    if not requirements:
        return jsonify({'error': 'No job requirements. Please paste a job URL or description first.'}), 400
    
    try:
        gaps = analyze_gaps(cv_data, requirements)
        session['gaps'] = gaps
        session['gap_answers'] = []
        return jsonify({'success': True, 'gaps': gaps})
    except Exception as e:
        return jsonify({'error': f'Failed to analyze gaps: {str(e)}'}), 500


@app.route('/gap/analyze')
def gap_analyze_page():
    """Page: Show gaps + targeted questions."""
    init_session()
    cv_data = session.get('cv_data')
    job_data = session.get('job_data')
    
    if not cv_data:
        return redirect(url_for('cv_upload_page'))
    if not job_data:
        return redirect(url_for('job_paste_page'))
    
    gaps = session.get('gaps')
    if not gaps:
        return redirect(url_for('analyze_gaps_route'))
    
    # Generate targeted questions for each gap
    questions = generate_gap_questions(gaps)
    
    return render_template('gap_analyze.html', gaps=gaps, questions=questions)


@app.route('/gap/answer', methods=['POST'])
def gap_answer_route():
    """API: Record a gap answer and update profile."""
    data = request.get_json()
    requirement = data.get('requirement')
    answer = data.get('answer')
    update_profile = data.get('update_profile', False)
    
    if not requirement or not answer:
        return jsonify({'error': 'Missing requirement or answer'}), 400
    
    try:
        # Convert answer to professional CV language
        ai_phrased = convert_answer_to_cv_language(requirement, answer)
        
        # Store the answer
        gap_answer = {
            'requirement': requirement,
            'user_answer': answer,
            'ai_phrased': ai_phrased,
            'update_profile': update_profile
        }
        
        # Append to session answers
        answers = session.get('gap_answers', [])
        updated = False
        for i, a in enumerate(answers):
            if a['requirement'] == requirement:
                answers[i] = gap_answer
                updated = True
                break
        if not updated:
            answers.append(gap_answer)
        
        session['gap_answers'] = answers
        
        return jsonify({'success': True, 'ai_phrased': ai_phrased})
    except Exception as e:
        return jsonify({'error': f'Failed to process answer: {str(e)}'}), 500


@app.route('/gap/answer')
def gap_answer_page():
    """Page: Record answers to gap questions and update profile."""
    init_session()
    cv_data = session.get('cv_data')
    job_data = session.get('job_data')
    
    if not cv_data:
        return redirect(url_for('cv_upload_page'))
    if not job_data:
        return redirect(url_for('job_paste_page'))
    
    gaps = session.get('gaps')
    if not gaps:
        return redirect(url_for('analyze_gaps_route'))
    
    questions = generate_gap_questions(gaps)
    answers = session.get('gap_answers', [])
    
    return render_template('gap_answer.html', gaps=gaps, questions=questions, answers=answers)


@app.route('/gap/analysis')
def gap_analysis_page():
    """Gap analysis display page."""
    init_session()
    cv_data = session.get('cv_data')
    job_data = session.get('job_data')
    
    # Redirect if CV or job data is missing (user skipped steps)
    if not cv_data:
        return redirect(url_for('cv_upload_page'))
    if not job_data:
        return redirect(url_for('job_paste_page'))
    
    gaps = session.get('gaps')
    return render_template('gap_analysis.html', gaps=gaps)


@app.route('/gap/qna')
def gap_qna_page():
    """Gap Q&A page with modal interaction."""
    init_session()
    cv_data = session.get('cv_data')
    job_data = session.get('job_data')
    
    if not cv_data:
        return redirect(url_for('cv_upload_page'))
    if not job_data:
        return redirect(url_for('job_paste_page'))
    
    gaps = session.get('gaps')
    return render_template('gap_qna.html', gaps=gaps)


@app.route('/cv/tailor', methods=['POST'])
def tailor_cv_route():
    """API: Generate tailored CV."""
    cv_data = session.get('cv_data')
    job_data = session.get('job_data')
    gap_answers = session.get('gap_answers', [])
    
    if not cv_data:
        return jsonify({'error': 'No CV data'}), 400
    
    if not job_data:
        return jsonify({'error': 'No job data'}), 400
    
    try:
        job_description = job_data.get('description', '')
        tailored = tailor_cv(cv_data, gap_answers, job_description)
        session['tailored_cv'] = tailored
        return jsonify({'success': True, 'tailored_cv': tailored})
    except Exception as e:
        return jsonify({'error': f'Failed to tailor CV: {str(e)}'}), 500


@app.route('/cv/preview')
def cv_preview_page():
    """Preview tailored CV."""
    init_session()
    tailored_cv = session.get('tailored_cv')
    if not tailored_cv:
        return redirect(url_for('cv_upload_page'))
    selected_template = session.get('cv_template', 'classic')
    # Map template name to folder
    template_map = {
        'modern': 'cv/style_1_modern/modern.html',
        'classic': 'cv/style_2_classic/classic.html',
        'minimal': 'cv/style_3_minimal/minimal.html',
        'creative': 'cv/style_4_creative/creative.html',
        'academic': 'cv/style_5_academic/academic.html',
        'bold': 'cv/style_6_bold/bold.html',
    }
    template_file = template_map.get(selected_template, 'cv/style_2_classic/classic.html')
    
    # Prepare context for template — map tailored_cv to template fields
    template_context = _prepare_cv_context(tailored_cv)
    
    return render_template(template_file, **template_context)


@app.route('/cv/select-template', methods=['POST'])
def select_template_route():
    """API: User selects a CV template style."""
    data = request.get_json()
    template = data.get('template', 'classic')
    valid_templates = ['modern', 'classic', 'minimal', 'creative', 'academic', 'bold']
    if template not in valid_templates:
        return jsonify({'error': 'Invalid template'}), 400
    session['cv_template'] = template
    return jsonify({'success': True, 'template': template})


@app.route('/cv/download')
def download_cv_pdf():
    """Download CV as PDF using selected template style."""
    tailored_cv = session.get('tailored_cv')
    job_data = session.get('job_data', {})
    selected_template = session.get('cv_template', 'classic')
    
    if not tailored_cv:
        return jsonify({'error': 'No tailored CV found'}), 400
    
    template_map = {
        'modern': 'cv/style_1_modern/modern.html',
        'classic': 'cv/style_2_classic/classic.html',
        'minimal': 'cv/style_3_minimal/minimal.html',
        'creative': 'cv/style_4_creative/creative.html',
        'academic': 'cv/style_5_academic/academic.html',
        'bold': 'cv/style_6_bold/bold.html',
    }
    template_file = template_map.get(selected_template, 'cv/style_2_classic/classic.html')
    
    # Prepare context for template
    template_context = _prepare_cv_context(tailored_cv)
    
    # Render HTML
    html_content = render_template(template_file, **template_context)
    
    try:
        from weasyprint import HTML
        import io
        
        pdf_buffer = io.BytesIO()
        HTML(string=html_content).write_pdf(pdf_buffer)
        pdf_buffer.seek(0)
        
        filename = f"{tailored_cv.get('name', 'CV').replace(' ', '_')}_tailored_{job_data.get('title', 'job').replace(' ', '_')}.pdf"
        
        return send_file(pdf_buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')
    except Exception as e:
        return jsonify({'error': f'Failed to generate PDF: {str(e)}'}), 500


@app.route('/cover-letter', methods=['POST'])
def cover_letter_route():
    """API: Generate cover letter."""
    cv_data = session.get('cv_data')
    job_data = session.get('job_data', {})
    gap_answers = session.get('gap_answers', [])
    
    if not cv_data:
        return jsonify({'error': 'No CV data'}), 400
    
    tone = request.json.get('tone', 'professional') if request.is_json else 'professional'
    
    try:
        cover_letter = generate_cover_letter(
            cv_data,
            gap_answers,
            job_data.get('description', ''),
            job_data.get('company', ''),
            job_data.get('title', ''),
            tone
        )
        
        session['cover_letter'] = cover_letter
        return jsonify({'success': True, 'cover_letter': cover_letter})
    except Exception as e:
        return jsonify({'error': f'Failed to generate cover letter: {str(e)}'}), 500


@app.route('/cover-letter/preview')
def cover_letter_preview_page():
    """Preview cover letter."""
    cover_letter = session.get('cover_letter', '')
    return render_template('cover_letter.html', cover_letter=cover_letter)


@app.route('/clear')
def clear_session():
    """Clear session data."""
    session.clear()
    return redirect(url_for('index'))


# ============ STATIC FILES ============

@app.route('/static/style.css')
def serve_css():
    return app.send_static_file('style.css')


@app.route('/static/js/app.js')
def serve_js():
    return app.send_static_file('js/app.js')


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)