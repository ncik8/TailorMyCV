import os
import json
import tempfile
import stripe as _stripe
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
from services.stripe_client import create_checkout_session, construct_webhook_event, get_tier_from_price_id, STRIPE_PRICE_PRO, STRIPE_PRICE_PRO_PLUS
from services.auth import sign_up, sign_in, sign_out, get_or_create_profile, can_generate_cv, increment_cv_count, get_client as get_supabase_client
from services.user_cv import save_cv, load_cv, delete_cv, upload_raw_file

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'))
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['ENV'] = 'production'
app.config['DEBUG'] = False
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")
CORS(app)

# Session storage — cookie-based (no server-side storage needed for MVP)
# For multi-instance prod, switch to Supabase session or Redis
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


# ============ AUTH ROUTES ============

@app.route('/auth/signup', methods=['GET', 'POST'])
def auth_signup():
    """Sign up new user."""
    if request.method == 'GET':
        return render_template('auth/signup.html', error=None)
    
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '')
    confirm = request.form.get('confirm_password', '')
    
    if not email or not password:
        return render_template('auth/signup.html', error='Email and password are required.')
    if password != confirm:
        return render_template('auth/signup.html', error='Passwords do not match.')
    if len(password) < 6:
        return render_template('auth/signup.html', error='Password must be at least 6 characters.')
    
    result = sign_up(email, password)
    if 'error' in result:
        return render_template('auth/signup.html', error=result['error'])
    
    user = result['user']
    session['user_id'] = user.id
    session['user_email'] = user.email
    profile = get_or_create_profile(user.id)
    session['tier'] = profile.get('tier', 'free')
    session['cv_count'] = profile.get('cv_count', 0)
    session.permanent = True
    return redirect(url_for('dashboard'))


@app.route('/auth/login', methods=['GET', 'POST'])
def auth_login():
    """Login existing user."""
    next_url = request.args.get('next', '')
    if request.method == 'GET':
        return render_template('auth/login.html', error=None, next=next_url)
    
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '')
    
    if not email or not password:
        return render_template('auth/login.html', error='Email and password are required.', next=next_url)
    
    result = sign_in(email, password)
    if 'error' in result:
        return render_template('auth/login.html', error=result['error'], next=next_url)
    
    user = result['user']
    session['user_id'] = user.id
    session['user_email'] = user.email
    profile = get_or_create_profile(user.id)
    session['tier'] = profile.get('tier', 'free')
    session['cv_count'] = profile.get('cv_count', 0)
    session.permanent = True
    return redirect(next_url if next_url else url_for('dashboard'))


@app.route('/auth/logout', methods=['POST'])
def auth_logout():
    """Logout user."""
    sign_out()
    session.clear()
    return redirect(url_for('index'))


@app.route('/upgrade')
def upgrade_page():
    """Pricing / upgrade page."""
    init_session()
    user_tier = session.get('tier', 'free')
    return render_template('upgrade.html', tier=user_tier)


# ============ BLOG ROUTES ============

@app.route('/blog')
def blog_index():
    """Blog listing page."""
    init_session()
    try:
        supabase = get_supabase_client()
        resp = supabase.table('blog_posts').select('id, title, slug, excerpt, author, cover_image, published_at').eq('published', True).order('published_at', desc=True).limit(20).execute()
        posts = resp.data
    except Exception:
        posts = []
    return render_template('blog/index.html', posts=posts)


@app.route('/blog/<slug>')
def blog_post(slug):
    """Single blog post."""
    init_session()
    try:
        supabase = get_supabase_client()
        resp = supabase.table('blog_posts').select('*').eq('slug', slug).eq('published', True).single().execute()
        post = resp.data
    except Exception:
        post = None

    if not post:
        return render_template('blog/404.html'), 404

    return render_template('blog/post.html', post=post)


@app.route('/checkout/<tier>', methods=['POST'])
def checkout_route(tier):
    """Create Stripe Checkout session for the given tier."""
    user_id = session.get('user_id')
    email = session.get('user_email', '')

    if not user_id:
        return redirect(url_for('auth_login'))

    valid_tiers = {'pro': 'Pro', 'pro_plus': 'Pro+'}
    if tier not in valid_tiers:
        return redirect(url_for('upgrade_page'))

    try:
        checkout_url = request.host_url.rstrip('/')
        success_url = f"{checkout_url}/dashboard?upgrade=success"
        cancel_url = f"{checkout_url}/upgrade"

        sc = create_checkout_session(
            user_id=user_id,
            email=email,
            tier=tier,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return redirect(sc.url, code=303)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/stripe/webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events."""
    payload = request.data
    sig = request.headers.get('Stripe-Signature', '')

    if not STRIPE_WEBHOOK_SECRET:
        return jsonify({'error': 'Webhook secret not configured'}), 500

    try:
        event = construct_webhook_event(payload, sig)
    except Exception as e:
        return jsonify({'error': f'Webhook verification failed: {e}'}), 400

    # Handle subscription events
    if event['type'] in ('checkout.session.completed', 'customer.subscription.created', 'customer.subscription.updated'):
        obj = event['data']['object']

        if event['type'] == 'checkout.session.completed':
            sub_id = obj.get('subscription')
            user_id = obj.get('client_reference_id')
        else:
            sub_id = obj.get('id')
            user_id = obj.get('metadata', {}).get('user_id')

        if sub_id and user_id:
            # Get the price ID from the subscription
            sub = _stripe.Subscription.retrieve(sub_id)
            price_id = sub['items']['data'][0]['price']['id']
            tier = get_tier_from_price_id(price_id)

            supabase = get_supabase_client()
            supabase.table('profiles').update({
                'tier': tier,
                'stripe_subscription_id': sub_id,
                'stripe_customer_id': sub.get('customer'),
                'updated_at': 'now()',
            }).eq('user_id', user_id).execute()

    elif event['type'] == 'customer.subscription.deleted':
        obj = event['data']['object']
        user_id = obj.get('metadata', {}).get('user_id')
        if user_id:
            supabase = get_supabase_client()
            supabase.table('profiles').update({
                'tier': 'free',
                'stripe_subscription_id': None,
            }).eq('user_id', user_id).execute()

    return jsonify({'ok': True})


# ============ ROUTES ============

@app.route('/')
def index():
    """Landing page."""
    return render_template('index.html')


@app.route('/dashboard')
def dashboard():
    """User dashboard after login."""
    init_session()
    upgrade_success = request.args.get('upgrade') == 'success'
    return render_template('dashboard.html', upgrade_success=upgrade_success)


@app.route('/cv/upload')
def cv_upload_page():
    """CV upload page."""
    init_session()
    if not session.get('user_id'):
        return redirect(url_for('auth_login', next='/cv/upload'))
    return render_template('cv_upload.html')


@app.route('/cv/parse', methods=['POST'])
def parse_cv_route():
    """API: Parse uploaded CV file, then redirect to edit profile page."""
    init_session()
    if not session.get('user_id'):
        return jsonify({'error': 'Not authenticated'}), 401

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Unsupported file type. Please upload DOCX or PDF.'}), 400

    try:
        # Read file bytes for storage
        file_bytes = file.read()
        file.seek(0)  # Reset for re-reading in parse_cv

        # Get content type
        content_type = file.content_type or ('application/pdf' if file.filename.lower().endswith('.pdf') else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')

        # Parse the CV
        cv_data = parse_cv(file)
        if cv_data.get('error'):
            return jsonify({'error': cv_data['error']}), 400

        # Store in session for this session
        session['cv_data'] = cv_data
        session['cv_filename'] = file.filename
        # Store raw_text so edit-profile can show debug panel if extraction worked but parsing didn't
        if cv_data.get('raw_text'):
            session['raw_text'] = cv_data['raw_text']

        # Persist to Supabase if user is logged in
        user_id = session.get('user_id')
        if user_id:
            app.logger.info(f"[CV] user_id={user_id}, cv_name={cv_data.get('name', 'N/A')}")
            save_result = save_cv(user_id, cv_data)
            app.logger.info(f"[CV] save_cv result: {save_result}")
            ext = file.filename.lower().split('.')[-1]
            stored_filename = f"cv_{user_id[:8]}.{ext}"
            upload_result = upload_raw_file(user_id, file_bytes, stored_filename, content_type)
            app.logger.info(f"[CV] upload_raw_file result: {upload_result}")

        return jsonify({'success': True, 'redirect': url_for('edit_profile_page')})
    except Exception as e:
        return jsonify({'error': f'Failed to parse CV: {str(e)}'}), 500


@app.route('/cv/delete', methods=['POST'])
def delete_cv_route():
    """Delete the user's CV from session and Supabase."""
    init_session()
    if not session.get('user_id'):
        return redirect(url_for('login_page'))

    user_id = session.get('user_id')

    # Clear session
    session.pop('cv_data', None)
    session.pop('profile', None)
    session.pop('tailored_cv', None)
    session.pop('job_data', None)

    # Clear from Supabase
    delete_cv(user_id or '')

    return redirect(url_for('dashboard'))


@app.route('/cv/edit-profile')
def edit_profile_page():
    """Edit profile page — shows parsed CV data for review/editing."""
    init_session()
    cv_data = session.get('cv_data')
    session_cv = bool(cv_data)
    supabase_cv = False

    # If no CV in session but user is logged in, try to load from Supabase
    user_id = session.get('user_id')
    if not cv_data and user_id:
        saved_cv = load_cv(user_id)
        if saved_cv:
            session['cv_data'] = saved_cv
            cv_data = saved_cv
            supabase_cv = True

    if cv_data:
        profile = cv_data.copy()
    else:
        profile = {
            'name': '', 'email': '', 'phone': '', 'location': '',
            'linkedin': '', 'title': '', 'summary': '',
            'experience': [], 'skills': [], 'education': [],
            'projects': [], 'certifications': [], 'languages': [],
        }

    # Show debug panel when profile is nearly empty AND raw_text is available
    raw_text = session.get('raw_text', '')
    raw_text_len = len(raw_text) if raw_text else 0
    show_debug = (
        not profile.get('name')
        and not profile.get('email')
        and not profile.get('experience')
        and raw_text_len > 50
    )
    ai_error = cv_data.get('ai_error') if cv_data else None
    warning_msg = cv_data.get('warning') if cv_data else None

    return render_template(
        'edit_profile.html',
        profile=profile,
        success=request.args.get('success'),
        session_cv=session_cv,
        supabase_cv=supabase_cv,
        raw_text_len=raw_text_len,
        debug_raw=raw_text if show_debug else None,
        ai_error=ai_error,
        warning_msg=warning_msg
    )


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

    # Parse experience entries — submit JS creates indexed fields (exp_title_0, exp_title_1, etc.)
    # so we collect all keys from form that match those patterns
    exp_titles = [v for k, v in request.form.items() if k.startswith('exp_title_')]
    exp_companies = [v for k, v in request.form.items() if k.startswith('exp_company_')]
    exp_starts = [v for k, v in request.form.items() if k.startswith('exp_start_')]
    exp_ends = [v for k, v in request.form.items() if k.startswith('exp_end_')]

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

    # Parse education entries — submit JS creates indexed fields
    edu_degrees = [v for k, v in request.form.items() if k.startswith('edu_degree_')]
    edu_fields = [v for k, v in request.form.items() if k.startswith('edu_field_')]
    edu_schools = [v for k, v in request.form.items() if k.startswith('edu_school_')]
    edu_years = [v for k, v in request.form.items() if k.startswith('edu_year_')]

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

    # Persist updated CV to Supabase
    user_id = session.get('user_id')
    if user_id:
        save_cv(user_id, profile)

    return redirect(url_for('job_paste_page'))


@app.route('/job/paste')
def job_paste_page():
    """Job URL/text paste page."""
    init_session()
    return render_template('job_paste.html')


@app.route('/job/scrape', methods=['POST'])
def scrape_job_route():
    """API: Parse pasted job description text."""
    data = request.get_json()
    
    if not data.get('text'):
        return jsonify({'error': 'No text provided'}), 400
    
    # Parse pasted job description
    job_data = parse_job_text(data['text'])
    session['job_data'] = job_data
    
    # Extract requirements
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
        'source': 'pasted'
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
    
    interview_likelihood = gaps.get('interview_likelihood', 50)
    
    # Generate targeted questions for each gap
    questions = generate_gap_questions(gaps)
    
    return render_template('gap_analyze.html', gaps=gaps, questions=questions, interview_likelihood=interview_likelihood)


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


@app.route('/cv/tailor', methods=['GET'])
def tailor_cv_page():
    """Page: Generate tailored CV and redirect to preview."""
    init_session()
    tailored_cv = session.get('tailored_cv')
    if tailored_cv:
        return redirect(url_for('cv_preview_page'))

    cv_data = session.get('cv_data')
    job_data = session.get('job_data')
    gap_answers = session.get('gap_answers', [])
    user_id = session.get('user_id')

    if not cv_data or not job_data:
        return redirect(url_for('cv_upload_page'))

    if user_id:
        allowed, reason, profile = can_generate_cv(user_id)
        if not allowed:
            return redirect(url_for('upgrade_page'))

    job_description = job_data.get('description', '')
    tailored = tailor_cv(cv_data, gap_answers, job_description)
    session['tailored_cv'] = tailored

    if user_id:
        increment_cv_count(user_id)

    return redirect(url_for('cv_preview_page'))


@app.route('/cv/tailor', methods=['POST'])
def tailor_cv_route():
    """API: Generate tailored CV."""
    cv_data = session.get('cv_data')
    job_data = session.get('job_data')
    gap_answers = session.get('gap_answers', [])
    user_id = session.get('user_id')
    
    if not cv_data:
        return jsonify({'error': 'No CV data'}), 400
    
    if not job_data:
        return jsonify({'error': 'No job data'}), 400
    
    # Check CV count gating if user is logged in
    if user_id:
        allowed, reason, profile = can_generate_cv(user_id)
        if not allowed:
            return jsonify({'error': 'limit_reached', 'redirect': url_for('upgrade_page')}), 403
    
    try:
        job_description = job_data.get('description', '')
        tailored = tailor_cv(cv_data, gap_answers, job_description)
        session['tailored_cv'] = tailored
        
        # Increment CV count if user is logged in
        if user_id:
            increment_cv_count(user_id)
            session['cv_count'] = profile.get('cv_count', 0) + 1
        
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
        from xhtml2pdf import pisa
        import io

        pdf_buffer = io.BytesIO()
        pisa.CreatePDF(
            io.BytesIO(html_content.encode('utf-8')),
            pdf_buffer
        )
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