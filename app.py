# RezMyCV — https://rezmycv.com
# Version: 2026-05-29-fix-final
# Fix: |safe instead of |tojson to prevent double-encoding BOM
import os
import json
import tempfile
import stripe as _stripe
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from flask_cors import CORS
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
import base64

# Services
from services.cv_parser import parse_cv
from services.job_scraper import scrape_job_url, parse_job_text
from services.gap_analyzer import extract_requirements, analyze_gaps, convert_answer_to_cv_language, generate_gap_questions, interpret_gap_answer, apply_gap_answer_to_profile
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

app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['SESSION_COOKIE_SIZE'] = None
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # Fix cross-site session loss

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
    
    # Add ATS score if not present (used in preview page banner)
    if "ats_score" not in ctx:
        ctx["ats_score"] = session.get("ats_score", 78)
    
    return ctx


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def init_session():
    """Initialize session data if not present. Keep session COOKIE SMALL — no CV/job/gap data."""
    if 'user_id' not in session:
        session['user_id'] = None
    # Never store large CV/job/gap data in session — always read from Supabase
    if 'cv_data' not in session:
        session['cv_data'] = None  # read from Supabase on demand instead
    if 'tailored_cv' not in session:
        session['tailored_cv'] = None  # regenerated per job on /cv/tailor
    if 'cv_template' not in session:
        session['cv_template'] = 'classic'
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
    user_id = session.get('user_id')

    # Load CV from Supabase directly (don't rely on session cookie)
    cv_data = None
    if user_id:
        cv_data = load_cv(user_id)
    # Also check session for CV just uploaded this session
    if not cv_data:
        cv_data = session.get('cv_data')

    # Load job data from Supabase (not session — session gets lost on cookie reset)
    job_data = {}
    if user_id:
        job_data = load_job_description(user_id)

    upgrade_success = request.args.get('upgrade') == 'success'
    return render_template('dashboard.html', upgrade_success=upgrade_success, cv_data=cv_data, job_data=job_data)


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
        app.logger.info(f"[CV] parse_cv returned: name={cv_data.get('name')}, ai_error={cv_data.get('ai_error')}, error={cv_data.get('error')}")
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
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth_login', next='/cv/delete'))

    # Clear session
    session.pop('cv_data', None)
    session.pop('profile', None)
    session.pop('tailored_cv', None)
    session.pop('job_data', None)

    # Clear from Supabase — fail hard if user_id is empty (logs it)
    deleted = delete_cv(user_id)
    print(f"[DELETE] route: user_id={user_id}, deleted={deleted}")

    return redirect(url_for('dashboard'))


@app.route('/cv/edit-profile')
def edit_profile_page():
    """Edit profile page — shows parsed CV data for review/editing."""
    init_session()
    user_id = session.get('user_id')
    cv_data = session.get('cv_data')
    if not cv_data and user_id:
        cv_data = load_cv(user_id)
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
    
    # DEBUG: log all exp/edu fields received
    exp_fields = {k: v for k, v in request.form.items() if k.startswith('exp_')}
    edu_fields = {k: v for k, v in request.form.items() if k.startswith('edu_')}
    app.logger.info(f"[SAVE] exp fields received: {exp_fields}")
    app.logger.info(f"[SAVE] edu fields received: {edu_fields}")
    
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

    # Parse languages (JSON from hidden field)
    langs_raw = request.form.get('languages', '').strip()
    if langs_raw:
        try:
            profile['languages'] = json.loads(langs_raw)
        except Exception:
            profile['languages'] = [s.strip() for s in langs_raw.split(',') if s.strip()]

    # Parse certifications (||| separated in hidden field)
    certs_raw = request.form.get('certifications', '').strip()
    if certs_raw:
        profile['certifications'] = [c.strip() for c in certs_raw.split('|||') if c.strip()]

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

    # Parse additional_info entries — submit JS creates indexed fields
    # (addinfo_label_0, addinfo_content_0, etc.)
    addinfo_labels = [v for k, v in request.form.items() if k.startswith('addinfo_label_')]
    addinfo_contents = [v for k, v in request.form.items() if k.startswith('addinfo_content_')]

    profile['additional_info'] = []
    for i, label in enumerate(addinfo_labels):
        if label.strip():
            entry = {
                'label': label.strip(),
                'content': addinfo_contents[i].strip() if i < len(addinfo_contents) else '',
            }
            profile['additional_info'].append(entry)

    session['profile'] = profile
    session['cv_data'] = profile

    # Persist updated CV to Supabase
    user_id = session.get('user_id')
    if user_id:
        save_cv(user_id, profile)

    app.logger.info(f"[SAVE] final profile experience: {profile['experience']}")
    app.logger.info(f"[SAVE] final profile education: {profile['education']}")
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
    
    # Parse pasted job description — job_data stored in Supabase only, not session cookie
    job_data = parse_job_text(data['text'])
    # Extract requirements
    if job_data.get('description'):
        requirements = extract_requirements(job_data['description'])

    return jsonify({
        'success': True,
        'job': job_data,
        'requirements': requirements if job_data.get('description') else {'skills': [], 'experience_years': {}, 'certifications': [], 'leadership': {}, 'tools': [], 'other': []},
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

    # Store job description + ATS analysis in Supabase (not session cookie)
    user_id = session.get('user_id')
    requirements = extract_requirements(confirmed_text)
    app.logger.info(f"[JOB] extract_requirements returned keys: {list(requirements.keys()) if isinstance(requirements, dict) else 'ERROR: ' + str(requirements)[:200]}")

    # Extract ATS keywords: flatten all requirement categories into a keyword list
    ats_keywords = []
    if isinstance(requirements, dict) and 'error' not in requirements:
        for category in ['skills', 'certifications', 'tools', 'other']:
            items = requirements.get(category, [])
            app.logger.info(f"[JOB] category={category}, items={items}")
            for item in items:
                if isinstance(item, dict):
                    ats_keywords.append(item.get('keyword', '') or item.get('name', ''))
                elif isinstance(item, str):
                    ats_keywords.append(item)
    else:
        app.logger.warning(f"[JOB] requirements has error or wrong type: {requirements}")
    # Deduplicate
    ats_keywords = list(dict.fromkeys(k for k in ats_keywords if k))
    app.logger.info(f"[JOB] final ats_keywords count={len(ats_keywords)}, keywords={ats_keywords}")

    # Load CV data to run full gap analysis (one AI call to get gaps + ATS score)
    cv_data = None
    if user_id:
        cv_data = load_cv(user_id)

    gaps = {}
    if cv_data:
        # Run full gap analysis with ATS scoring in one call
        gaps = analyze_gaps(cv_data, requirements)
        app.logger.info(f"[JOB] Gap analysis done: partials={len(gaps.get('partials', []))}, missing={len(gaps.get('missing', []))}, ats_score={gaps.get('ats_score', 0)}")

    # Delete existing job descriptions for this user to get a clean slate.
    # NOTE: on-the-fly fallback in gap_answer_page overwrites gaps/requirements
    # in Supabase without a targeted update — it uses save_job_description()
    # which preserves ats_keywords but can cause race conditions if both
    # confirm_job_route and gap_answer_page write simultaneously.
    try:
        supabase = get_supabase_client()
        supabase.table('job_descriptions').delete().eq('user_id', user_id).execute()
    except Exception as e:
        app.logger.info(f"[JOB] confirm_job_route cleanup error: {e}")

    job_record = {
        'user_id': user_id,
        'description': confirmed_text,
        'title': '',
        'company': '',
        'ats_keywords': json.dumps(ats_keywords),
        'requirements': requirements,
        'gaps': gaps,
    }
    job_id = save_job_description(job_record)

    # Session only stores the ID reference — no cookie bloat
    session['job_desc_id'] = job_id

    return jsonify({'success': True, 'requirements': requirements, 'ats_keywords': ats_keywords, 'gaps': gaps})


def save_job_description(job_record: dict) -> str:
    """Save job description + ATS keywords to Supabase, return job_id."""
    try:
        supabase = get_supabase_client()
        user_id = job_record.get('user_id')
        if not user_id:
            return None

        # Check if user already has a job description (replace it)
        existing = supabase.table('job_descriptions').select('id').eq('user_id', user_id).execute()
        job_data = {
            'user_id': user_id,
            'description': job_record.get('description', ''),
            'title': job_record.get('title', ''),
            'company': job_record.get('company', ''),
            # ats_keywords: already a Python list or JSON string — store as JSON string
            'ats_keywords': job_record.get('ats_keywords') if isinstance(job_record.get('ats_keywords'), str) else json.dumps(job_record.get('ats_keywords', [])),
            'gaps': json.dumps(job_record.get('gaps')) if job_record.get('gaps') else None,
            'requirements': json.dumps(job_record.get('requirements')) if job_record.get('requirements') else None,
            'gap_answers': json.dumps(job_record.get('gap_answers', [])) if job_record.get('gap_answers') is not None else None,
            'updated_at': datetime.now().isoformat()
        }

        if existing.data:
            # Update existing
            supabase.table('job_descriptions').update(job_data).eq('user_id', user_id).execute()
            return existing.data[0]['id']
        else:
            # Insert new
            job_data['created_at'] = datetime.now().isoformat()
            result = supabase.table('job_descriptions').insert(job_data).execute()
            return result.data[0]['id'] if result.data else None
    except Exception as e:
        app.logger.info(f"[JOB] save_job_description error: {e}")
        return None


def load_job_description(user_id: str) -> dict:
    """Load job description + ATS keywords + gap session data from Supabase."""
    try:
        supabase = get_supabase_client()
        result = supabase.table('job_descriptions').select('*').eq('user_id', user_id).order('created_at', desc=True).limit(1).execute()
        if result.data:
            jd = result.data[0]
            ats_keywords = jd.get('ats_keywords', '[]')
            if isinstance(ats_keywords, str):
                ats_keywords = json.loads(ats_keywords) if ats_keywords else []

            # Load gaps if present
            gaps_raw = jd.get('gaps')
            gaps = gaps_raw if isinstance(gaps_raw, dict) else (json.loads(gaps_raw) if gaps_raw else None)

            # Load requirements if present
            req_raw = jd.get('requirements')
            requirements = req_raw if isinstance(req_raw, dict) else (json.loads(req_raw) if req_raw else None)

            # Load gap answers if present
            gap_answers_raw = jd.get('gap_answers')
            gap_answers = gap_answers_raw if isinstance(gap_answers_raw, list) else (json.loads(gap_answers_raw) if gap_answers_raw else [])

            return {
                'description': jd.get('description', ''),
                'title': jd.get('title', ''),
                'company': jd.get('company', ''),
                'ats_keywords': ats_keywords,
                'gaps': gaps,
                'requirements': requirements,
                'gap_answers': gap_answers,
            }
    except Exception as e:
        app.logger.info(f"[JOB] load_job_description error: {e}")
    return {}

def delete_job_description(user_id: str) -> bool:
    """Delete user's job description from Supabase."""
    try:
        supabase = get_supabase_client()
        supabase.table('job_descriptions').delete().eq('user_id', user_id).execute()
        return True
    except Exception as e:
        app.logger.info(f"[JOB] delete_job_description error: {e}")
        return False


@app.route('/job/clear', methods=['POST'])
def clear_job_route():
    """API: Delete job description from Supabase and clear session."""
    user_id = session.get('user_id')
    if user_id:
        delete_job_description(user_id)
    session.pop('job_desc_id', None)
    session.pop('job_data', None)
    session.pop('requirements', None)
    session.pop('gaps', None)
    session.pop('gap_answers', None)
    return redirect(url_for('dashboard'))


def delete_job_description(user_id: str) -> bool:
    """Delete user's job description from Supabase."""
    try:
        supabase = get_supabase_client()
        supabase.table('job_descriptions').delete().eq('user_id', user_id).execute()
        return True
    except Exception as e:
        app.logger.info(f"[JOB] delete_job_description error: {e}")
        return False


@app.route('/gap/answer')
def gap_answer_page():
    """Page: Record answers to gap questions and update profile."""
    init_session()
    user_id = session.get('user_id')

    cv_data = None
    if user_id:
        cv_data = load_cv(user_id)
    if not cv_data:
        return redirect(url_for('cv_upload_page'))

    job_data = {}
    if user_id:
        job_data = load_job_description(user_id)
    if not job_data or not job_data.get('description'):
        return redirect(url_for('job_paste_page'))

    app.logger.info(f"[GAP] session.gaps={'yes' if session.get('gaps') else 'NONE'}, job_data.gaps={'yes' if (job_data and job_data.get('gaps')) else 'NONE'}, job_data.keys={list(job_data.keys()) if job_data else 'empty'}")
    gaps = session.get('gaps')
    if not gaps:
        # Load from Supabase only — never cache gaps in session
        if job_data and job_data.get('gaps'):
            gaps = job_data['gaps']
        else:
            # On-the-fly fallback — save back to Supabase for next load
            requirements = job_data.get('requirements')
            if not requirements:
                requirements = extract_requirements(job_data.get('description', ''))
            gaps = analyze_gaps(cv_data, requirements)
            # Persist to Supabase so future loads get it from there.
            # Use targeted UPDATE to avoid wiping ats_keywords/description.
            try:
                supabase = get_supabase_client()
                supabase.table('job_descriptions').update({
                    'gaps': json.dumps(gaps),
                    'requirements': json.dumps(requirements),
                }).eq('user_id', user_id).execute()
            except Exception:
                pass

    app.logger.info(f"[GAP] final gaps object: partials={len(gaps.get('partials',[]))}, missing={len(gaps.get('missing',[]))}, matches={len(gaps.get('matches',[]))}")

    questions = generate_gap_questions(gaps)
    # Gap answers — prefer Supabase (survives cookie limits), fall back to session
    answers = job_data.get('gap_answers') if job_data else None
    if answers is None:
        answers = session.get('gap_answers', [])

    # Build all_gaps list for the chat UI — NO MiniMax calls here, use gap data directly
    import json as _json
    all_gaps = []
    for p in (gaps.get('partials') or []):
        req = p.get('requirement', '')
        q = questions.get(req, [None] * 3)[0] if isinstance(questions, dict) and req in questions else None
        answer = next((a for a in answers if a.get('requirement') == req), None)
        all_gaps.append({
            'requirement': req,
            'type': 'partial',
            'gap': p,
            'question': q or p.get('question', '') or f"Tell me about your experience with {req}. What did you actually do?",
            'answer': answer
        })
    for m in (gaps.get('missing') or []):
        req = m.get('requirement', '')
        q = questions.get(req, [None] * 3)[0] if isinstance(questions, dict) and req in questions else None
        answer = next((a for a in answers if a.get('requirement') == req), None)
        all_gaps.append({
            'requirement': req,
            'type': 'missing',
            'gap': m,
            'question': q or m.get('question', '') or f"Tell me about your experience with {req}. Even a brief example counts!",
            'answer': answer
        })

    # Build start messages from existing answers
    start_messages = []
    for item in all_gaps:
        if item.get('answer'):
            start_messages.append({
                'role': 'coach',
                'text': f"Let's talk about: {item['requirement']}",
                'requirement': item['requirement'],
                'timestamp': ''
            })
            start_messages.append({
                'role': 'user',
                'text': item['answer'].get('user_answer', ''),
                'timestamp': ''
            })
            start_messages.append({
                'role': 'ai',
                'text': f"Great story! Here's how I'll add it to your CV:\n\n\"{item['answer'].get('ai_phrased', '')}\"",
                'confirmed': True,
                'timestamp': ''
            })

    return render_template('gap_answer.html',
        gaps=gaps, questions=questions, answers=answers,
        all_gaps_json=_json.dumps(all_gaps),
        start_messages_json=_json.dumps(start_messages),
        cv_data_json=_json.dumps(cv_data))


@app.route('/gap/interpret', methods=['POST'])
def gap_interpret_route():
    """Step 1: User gave initial answer. AI rewrites it + figures out category + destinations."""
    data = request.get_json()
    requirement = data.get('requirement', '').strip()
    answer = data.get('answer', '').strip()
    user_id = session.get('user_id')

    if not requirement or not answer:
        return jsonify({'error': 'Missing requirement or answer'}), 400

    if not user_id:
        return jsonify({'error': 'Not logged in'}), 401

    cv_data = load_cv(user_id)
    if not cv_data:
        return jsonify({'error': 'No CV found'}), 400

    try:
        result = interpret_gap_answer(cv_data, requirement, answer)
        return jsonify({'success': True, **result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/gap/confirm-destination', methods=['POST'])
def gap_confirm_destination_route():
    """Step 2: User picked destination. Apply it to CV, return preview for confirmation."""
    data = request.get_json()
    requirement = data.get('requirement', '').strip()
    answer = data.get('answer', '').strip()
    interpreted = data.get('interpreted', '').strip()
    category = data.get('category', 'other')
    destination = data.get('destination', {})  # {type, job_idx, label}
    user_id = session.get('user_id')

    if not all([requirement, answer, interpreted]):
        return jsonify({'error': 'Missing fields'}), 400

    if not user_id:
        return jsonify({'error': 'Not logged in'}), 401

    cv_data = load_cv(user_id)
    if not cv_data:
        return jsonify({'error': 'No CV found'}), 400

    try:
        modification = apply_gap_answer_to_profile(
            cv_data, requirement, answer, interpreted, category, destination
        )
        if 'error' in modification:
            return jsonify({'error': modification['error']}), 500

        # Build human-readable confirmation label
        if destination.get('type') == 'job':
            idx = destination.get('job_idx', 0)
            jobs = cv_data.get('experience', [])
            job = jobs[idx] if idx < len(jobs) else jobs[-1]
            label = f"{job.get('title', 'Job')} @ {job.get('company', '')}"
        else:
            label = destination.get('label', category.title())

        return jsonify({
            'success': True,
            'applied_to': modification.get('applied_to', ''),
            'applied_text': modification.get('applied_text', interpreted),
            'confirmation_label': label,
            'cv_modification': modification.get('cv_modification', {})
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/gap/confirm-answer', methods=['POST'])
def gap_confirm_answer_route():
    """Step 3: User confirmed. Save the updated/modified CV back to Supabase."""
    data = request.get_json()
    requirement = data.get('requirement', '').strip()
    answer = data.get('answer', '').strip()
    interpreted = data.get('interpreted', '').strip()
    category = data.get('category', 'other')
    destination = data.get('destination', {})
    cv_modification = data.get('cv_modification', {})
    user_id = session.get('user_id')

    if not all([requirement, interpreted]) or not user_id:
        return jsonify({'error': 'Missing data'}), 400

    try:
        cv_data = load_cv(user_id)
        if not cv_data:
            return jsonify({'error': 'No CV found'}), 400

        applied_to = cv_modification.get('applied_to', '')

        # Deep merge cv_modification into cv_data
        updated_cv = merge_cv_sections(cv_data, cv_modification)

        # Save updated CV to Supabase
        save_result = save_cv(user_id, updated_cv)
        if not save_result:
            return jsonify({'error': 'Failed to save updated CV'}), 500

        # Save gap answer to session
        gap_answer = {
            'requirement': requirement,
            'user_answer': answer,
            'ai_phrased': interpreted,
            'category': category,
            'destination': destination,
            'applied_to': applied_to
        }
        # Update session (small — just IDs/flags, no CV data)
        answers = session.get('gap_answers', [])
        for i, a in enumerate(answers):
            if a.get('requirement') == requirement:
                answers[i] = gap_answer
                break
        else:
            answers.append(gap_answer)
        session['gap_answers'] = answers  # small — cleared if session resets, Supabase is source of truth

        # Persist gap answers to Supabase — ONLY update gap_answers field, don't wipe other fields
        try:
            supabase = get_supabase_client()
            existing = load_job_description(user_id)
            if existing:
                # Targeted update: only touch gap_answers, preserve all other fields
                supabase.table('job_descriptions').update({
                    'gap_answers': json.dumps(answers)
                }).eq('user_id', user_id).execute()
        except Exception as e:
            app.logger.info(f"[GAP] Failed to persist gap answers: {e}")

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def merge_cv_sections(cv_data: dict, modification: dict) -> dict:
    """
    Merge cv_modification into cv_data.
    modification keys: 'job_0', 'job_1', ..., 'skills', 'certifications', 'projects', 'summary'
    """
    updated = dict(cv_data)

    applied_to = modification.get('applied_to', '')

    if applied_to.startswith('job_'):
        idx = int(applied_to.split('_')[1])
        if 'experience' not in updated:
            updated['experience'] = []
        while len(updated['experience']) <= idx:
            updated['experience'].append({'title': '', 'company': '', 'bullets': []})
        new_bullets = modification.get('cv_modification', {}).get('bullets', [])
        existing = updated['experience'][idx].get('bullets', [])
        for b in new_bullets:
            if b not in existing:
                existing.append(b)
        updated['experience'][idx]['bullets'] = existing
    elif applied_to == 'skills':
        updated['skills'] = updated.get('skills', [])
        new_skills = modification.get('cv_modification', [])
        for s in new_skills:
            if isinstance(s, str) and s not in updated['skills']:
                updated['skills'].append(s)
            elif isinstance(s, dict) and s.get('name') not in [x.get('name') for x in updated['skills']]:
                updated['skills'].append(s)
    elif applied_to == 'certifications':
        updated['certifications'] = updated.get('certifications', [])
        new_certs = modification.get('cv_modification', [])
        for c in new_certs:
            if isinstance(c, str) and c not in updated['certifications']:
                updated['certifications'].append(c)
    elif applied_to == 'projects':
        updated['projects'] = updated.get('projects', [])
        new_projects = modification.get('cv_modification', [])
        for p in new_projects:
            if p not in updated['projects']:
                updated['projects'].append(p)
    elif applied_to == 'summary':
        current = updated.get('summary', '')
        new_text = modification.get('cv_modification', '')
        updated['summary'] = f"{current} {new_text}".strip()

    return updated


@app.route('/gap/analyze')
def gap_analysis_page():
    """Gap analysis display page."""
    init_session()
    user_id = session.get('user_id')

    cv_data = None
    if user_id:
        cv_data = load_cv(user_id)
    if not cv_data:
        return redirect(url_for('cv_upload_page'))

    job_data = {}
    if user_id:
        job_data = load_job_description(user_id)

    if not job_data or not job_data.get('description'):
        return redirect(url_for('job_paste_page'))

    # Load or generate gaps — prefer Supabase, fall back to on-the-fly
    app.logger.info(f"[ANALYZE] job_data.keys={list(job_data.keys()) if job_data else 'empty'}, gaps={'yes' if job_data.get('gaps') else 'NONE'}")
    gaps = job_data.get('gaps')
    if not gaps:
        requirements = job_data.get('requirements')
        if not requirements:
            requirements = extract_requirements(job_data.get('description', ''))
        try:
            gaps = analyze_gaps(cv_data, requirements)
        except Exception as e:
            return render_template('gap_analyze.html', gaps={}, questions=[], interview_likelihood=50, error=str(e))

    # Persist gaps to Supabase for session-free access
    try:
        job_record = {
            'user_id': user_id,
            'description': job_data.get('description', ''),
            'gaps': gaps,
            'requirements': job_data.get('requirements'),
        }
        save_job_description(job_record)
    except Exception as e:
        app.logger.info(f"[GAP] Failed to save gaps to Supabase: {e}")

    interview_likelihood = gaps.get('interview_likelihood', 50)
    return render_template('gap_analyze.html', gaps=gaps, interview_likelihood=interview_likelihood)


@app.route('/gap/qna')
def gap_qna_page():
    """Gap Q&A page with modal interaction."""
    init_session()
    user_id = session.get('user_id')
    cv_data = session.get('cv_data')
    if not cv_data and user_id:
        cv_data = load_cv(user_id)
    job_data = load_job_description(user_id) if user_id else {}

    if not cv_data:
        return redirect(url_for('cv_upload_page'))
    if not job_data:
        return redirect(url_for('job_paste_page'))

    gaps = job_data.get('gaps', []) if job_data else []
    return render_template('gap_qna.html', gaps=gaps)


@app.route('/cv/tailor', methods=['GET'])
def tailor_cv_page():
    """Page: Generate tailored CV and redirect to preview."""
    init_session()
    tailored_cv = session.get('tailored_cv')
    if tailored_cv:
        return redirect(url_for('cv_preview_page'))

    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login_page'))

    # Load CV from Supabase only — never trust session for CV data
    cv_data = load_cv(user_id)
    if not cv_data:
        return redirect(url_for('cv_upload_page'))

    allowed, reason, profile = can_generate_cv(user_id)
    if not allowed:
        return redirect(url_for('upgrade_page'))

    # Load job description + ATS keywords from Supabase only
    job_data = load_job_description(user_id) if user_id else {}
    job_description = job_data.get('description', '')
    ats_keywords = job_data.get('ats_keywords', [])
    gap_answers = job_data.get('gap_answers', []) if job_data else []

    if not job_description:
        return redirect(url_for('job_paste_page'))

    tailored = tailor_cv(cv_data, gap_answers, job_description, ats_keywords)
    session['tailored_cv'] = tailored

    if user_id:
        increment_cv_count(user_id)

    return redirect(url_for('cv_preview_page'))


@app.route('/cv/tailor', methods=['POST'])
def tailor_cv_route():
    """API: Generate tailored CV."""
    user_id = session.get('user_id')
    cv_data = session.get('cv_data')
    if not cv_data and user_id:
        cv_data = load_cv(user_id)
    # Always load gap_answers from Supabase (not session — keeps cookie small)
    gap_answers = []
    if user_id:
        job_data = load_job_description(user_id)
        gap_answers = job_data.get('gap_answers', []) if job_data else []

    if not cv_data:
        return jsonify({'error': 'No CV data'}), 400

    # Load job description + ATS keywords from Supabase
    job_data = load_job_description(user_id) if user_id else {}
    job_description = job_data.get('description', '')
    ats_keywords = job_data.get('ats_keywords', [])

    if not job_description:
        return jsonify({'error': 'No job data'}), 400

    # Check CV count gating if user is logged in
    if user_id:
        allowed, reason, profile = can_generate_cv(user_id)
        if not allowed:
            return jsonify({'error': 'limit_reached', 'redirect': url_for('upgrade_page')}), 403

    try:
        tailored = tailor_cv(cv_data, gap_answers, job_description, ats_keywords)
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
    user_id = session.get('user_id')
    job_data = load_job_description(user_id) if user_id else {}
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
        import re
        import os

        # Inline CSS for PDF generation (xhtml2pdf can't fetch external files)
        css_path_map = {
            'cv/style_1_modern/modern.html': '/static/cv/style_1_modern/style.css',
            'cv/style_2_classic/classic.html': '/static/cv/style_2_classic/style.css',
            'cv/style_3_minimal/minimal.html': '/static/cv/style_3_minimal/style.css',
            'cv/style_4_creative/creative.html': '/static/cv/style_4_creative/style.css',
            'cv/style_5_academic/academic.html': '/static/cv/style_5_academic/style.css',
            'cv/style_6_bold/bold.html': '/static/cv/style_6_bold/style.css',
        }
        css_file_path = css_path_map.get(template_file)
        if css_file_path:
            full_css_path = os.path.join(BASE_DIR, css_file_path.lstrip('/'))
            if os.path.exists(full_css_path):
                with open(full_css_path, 'r') as f:
                    css_content = f.read()
                # Replace link tag with style tag
                html_content = re.sub(
                    r'<link[^>]*rel=["\']stylesheet["\'][^>]*>',
                    f'<style>{css_content}</style>',
                    html_content,
                    count=1
                )
                # Also handle the case where href comes first
                html_content = re.sub(
                    r'<link[^>]*href=["\'][^"\']*style\.css["\'][^>]*>',
                    f'<style>{css_content}</style>',
                    html_content,
                    count=1
                )

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
    user_id = session.get('user_id')
    cv_data = session.get('cv_data')
    if not cv_data and user_id:
        cv_data = load_cv(user_id)
    # Always load job + gap data from Supabase (not session — keeps cookie small)
    job_data = load_job_description(user_id) if user_id else {}
    gap_answers = job_data.get('gap_answers', []) if job_data else []
    
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


@app.route('/debug/supabase')
def debug_supabase():
    """Debug route: show what's in the user_cvs table."""
    if not session.get('user_id'):
        return {'error': 'not logged in'}
    user_id = session.get('user_id')
    saved = load_cv(user_id)
    return {'user_id': user_id, 'saved_cv': saved}