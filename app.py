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
from services.gap_analyzer import extract_requirements, analyze_gaps, convert_answer_to_cv_language, generate_gap_questions, interpret_gap_answer, apply_gap_answer_to_profile, score_ats_keywords
from services.tailor import tailor_cv, generate_cv_pdf
from services.optimise import optimise_cv_for_ats
from services.cover_letter import generate_cover_letter
from services.stripe_client import create_checkout_session, construct_webhook_event, get_tier_from_price_id, upgrade_subscription, STRIPE_PRICE_PRO, STRIPE_PRICE_PRO_PLUS, STRIPE_WEBHOOK_SECRET
from services.auth import sign_up, sign_in, sign_out, get_or_create_profile, can_generate_cv, increment_cv_count, get_client as get_supabase_client
from services.user_cv import save_cv, load_cv, delete_cv, upload_raw_file
from services.events import log_event

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
            "website": ctx.get("website", ""),
            "linkedin": ctx.get("linkedin", ""),
        }
        # summary lives at top level from AI output — copy it into personal for templates
        if ctx.get("summary"):
            ctx["personal"]["summary"] = ctx["summary"]
    else:
        # personal already exists from AI output — ensure summary is also present in it
        if ctx.get("summary") and not ctx["personal"].get("summary"):
            ctx["personal"]["summary"] = ctx["summary"]
    
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
    # Always refresh tier from DB to keep it current (payments, upgrades, cancellations)
    if session.get('user_id'):
        supabase = get_supabase_client()
        profile = supabase.table('profiles').select('tier').eq('user_id', session['user_id']).execute()
        if profile.data:
            session['tier'] = profile.data[0].get('tier', 'free')


# ============ AUTH ROUTES ============

@app.route('/auth/signup', methods=['GET', 'POST'])
def auth_signup():
    """Sign up new user."""
    if request.method == 'GET':
        log_event(session.get('user_id'), 'view_signup')
        return render_template('auth/signup.html', error=None)
    
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '')
    confirm = request.form.get('confirm_password', '')
    agree_terms = request.form.get('agree_terms')

    if not agree_terms:
        return render_template('auth/signup.html', error='You must agree to the Terms & Conditions and Privacy Policy.')
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
    log_event(user.id, 'signup', source='email')
    return redirect(url_for('dashboard'))


@app.route('/auth/login', methods=['GET', 'POST'])
def auth_login():
    """Login existing user."""
    next_url = request.args.get('next', '')
    if request.method == 'GET':
        log_event(session.get('user_id'), 'view_login')
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
    log_event(user.id, 'login', source='email')
    return redirect(next_url if next_url else url_for('dashboard'))


@app.route('/auth/logout', methods=['POST'])
def auth_logout():
    """Logout user."""
    sign_out()
    session.clear()
    return redirect(url_for('index'))


@app.route('/auth/forgot-password', methods=['GET', 'POST'])
def auth_forgot_password():
    """Send password reset email via Resend (from hello@rezmycv.com).

    Replaces the previous Supabase-default reset flow so the email looks
    branded and uses our own sender domain. The "no enumeration" guarantee
    stays: same success message whether or not the email exists.
    """
    if request.method == 'GET':
        return render_template('auth/forgot_password.html')

    email = (request.form.get('email') or '').strip().lower()
    if not email:
        return render_template('auth/forgot_password.html', error='Please enter your email address.')

    # Always render the same success template, regardless of whether the
    # user exists or email send succeeded. This avoids email enumeration
    # and avoids leaking our Resend/Supabase state to the public.
    success_template = 'auth/forgot_password.html'

    try:
        from services.auth import create_password_reset_token
        from services.email import send_password_reset_email
        result = create_password_reset_token(email)
        if result:
            reset_url = url_for('auth_reset_password', token=result['token'], _external=True)
            # Fire-and-don't-block: log but don't fail the request if email errors.
            try:
                send_password_reset_email(email, reset_url)
            except Exception as e:
                import sys
                print(f"[AUTH] forgot-password: email send raised: {e}", file=sys.stderr)
        # Whether or not the user exists, show the same message.
        return render_template(success_template, success=True)
    except Exception as e:
        # Token creation failed (DB error, missing key, etc.). Still show
        # the success message so we don't leak server state.
        import sys
        print(f"[AUTH] forgot-password error: {type(e).__name__}: {e}", file=sys.stderr)
        return render_template(success_template, success=True)


@app.route('/auth/reset-password/<token>', methods=['GET', 'POST'])
def auth_reset_password(token):
    """Set a new password using a valid reset token.

    GET: validate token, show the new-password form. Token invalid/expired/used
         -> show a neutral error page with a "request a new link" CTA.
    POST: validate + update password via Supabase admin. On success, redirect
          to /auth/login with a flash.
    """
    from services.auth import verify_password_reset_token, consume_password_reset_token

    # Always validate first, for both GET and POST.
    row = verify_password_reset_token(token)
    if not row:
        return render_template(
            'auth/reset_password.html',
            invalid_token=True,
            error="This reset link is invalid, expired, or already used. Request a new one.",
        ), 400

    if request.method == 'GET':
        return render_template('auth/reset_password.html', token=token)

    new_password = request.form.get('password') or ''
    confirm = request.form.get('confirm_password') or ''
    if not new_password or not confirm:
        return render_template('auth/reset_password.html', token=token,
                               error='Please enter and confirm your new password.')
    if new_password != confirm:
        return render_template('auth/reset_password.html', token=token,
                               error='Passwords do not match.')
    if len(new_password) < 6:
        return render_template('auth/reset_password.html', token=token,
                               error='Password must be at least 6 characters.')

    success = consume_password_reset_token(token, new_password)
    if not success:
        return render_template('auth/reset_password.html', token=token,
                               error='Could not update your password. Try requesting a new link.'), 500

    log_event(row['user_id'], 'password_reset')
    return redirect('/auth/login?reset=1')


@app.route('/upgrade')
def upgrade_page():
    """Pricing / upgrade page."""
    init_session()
    user_tier = session.get('tier', 'free')
    return render_template('upgrade.html', tier=user_tier)


@app.route('/terms')
def terms_page():
    """Terms & Conditions page."""
    return render_template('terms.html')


@app.route('/privacy')
def privacy_page():
    """Privacy Policy page."""
    return render_template('privacy.html')


@app.route('/profile')
def profile_page():
    """Profile / account page — always reads fresh from DB."""
    init_session()
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login_page'))

    profile = get_or_create_profile(user_id)
    upgrade_success = session.pop('upgrade_success', False)
    return render_template('profile.html', profile=profile, upgrade_success=upgrade_success)


@app.route('/billing/portal')
def billing_portal():
    """Redirect to Stripe customer portal for subscription management."""
    init_session()
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login_page'))

    from services.stripe_client import stripe
    profile = get_or_create_profile(user_id)
    customer_id = profile.get('stripe_customer_id')

    if not customer_id:
        # Fall back to email-based portal
        customer_id = session.get('user_email')

    try:
        session_url = stripe.billing_portal.sessions.create({
            "customer": customer_id,
            "return_url": url_for('profile_page', _external=True),
        })
        return redirect(session_url.url)
    except Exception as e:
        return redirect(url_for('upgrade_page'))


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


from datetime import datetime, timedelta, timezone
from functools import wraps


def _check_admin_auth():
    """HTTP Basic Auth for /admin/* endpoints. Hardcoded credentials
    (admin / Mylo@5327) per Nick's request. Browser shows native login popup."""
    auth = request.authorization
    if not auth or auth.username != 'admin' or auth.password != 'Mylo@5327':
        return False
    return True


def _make_unauth_response():
    """401 with WWW-Authenticate header so browser pops a native login prompt."""
    resp = jsonify({'error': 'unauthorized', 'message': 'Login required: admin / Mylo@5327'})
    resp.status_code = 401
    resp.headers['WWW-Authenticate'] = 'Basic realm="RezMyCV Admin"'
    return resp


@app.route('/admin/funnel')
def admin_funnel():
    """
    Investor-facing funnel metrics. Counts events for each step of the
    signup → upload → tailor → download pipeline.

    Last 30 days, grouped by event_name. Returns JSON for easy embed
    in dashboards or investor updates.

    Protected by HTTP Basic Auth (admin / Mylo@5327).
    """
    if not _check_admin_auth():
        return _make_unauth_response()

    try:
        supabase = get_supabase_client()
        # Last 30 days of events
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        resp = supabase.table('events').select('event_name, user_id, created_at') \
            .gte('created_at', cutoff).execute()
        events = resp.data or [] 

        # Count by event_name
        from collections import Counter
        counts = Counter(e['event_name'] for e in events)

        # Unique users per event
        users_per_event = {}
        for e in events:
            en = e['event_name']
            uid = e.get('user_id')
            if uid:
                users_per_event.setdefault(en, set()).add(uid)

        unique_user_counts = {en: len(uids) for en, uids in users_per_event.items()}

        # Funnel (in order)
        funnel_steps = [
            ('view_landing', 'Landing'),
            ('view_signup', 'Signup page'),
            ('signup', 'Signed up'),
            ('login', 'Logged in'),
            ('view_upload', 'Upload page'),
            ('upload_cv', 'Uploaded CV'),
            ('view_paste_job', 'Job paste page'),
            ('paste_job', 'Job pasted'),
            ('view_gap_answer', 'Gap answer page'),
            ('submit_gap_answer', 'Gap answered'),
            ('view_preview', 'Preview viewed'),
            ('download_pdf', 'PDF downloaded'),
            ('click_upgrade', 'Clicked upgrade'),
        ]
        funnel = []
        for en, label in funnel_steps:
            count = counts.get(en, 0)
            unique = unique_user_counts.get(en, 0)
            funnel.append({'event': en, 'label': label, 'count': count, 'unique_users': unique})

        # Also return top-level totals
        total_events = len(events)
        unique_users_overall = len({e['user_id'] for e in events if e.get('user_id')})

        return jsonify({
            'window_days': 30,
            'total_events': total_events,
            'unique_users_overall': unique_users_overall,
            'funnel': funnel,
            'all_event_counts': dict(counts),
            'generated_at': datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        import traceback
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc(),
        }), 500


@app.route('/admin/funnel/view')
def admin_funnel_view():
    """Human-readable funnel dashboard. Same data as /admin/funnel but
    rendered as HTML so non-technical viewers (investors, Nick) can see it.
    Protected by HTTP Basic Auth (admin / Mylo@5327)."""
    if not _check_admin_auth():
        return _make_unauth_response()
    return render_template('admin/funnel.html')


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
        log_event(user_id, 'click_upgrade', tier=tier, from_route='checkout')
        return redirect(sc.url, code=303)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/upgrade-subscription/<tier>', methods=['POST'])
def upgrade_subscription_route(tier):
    """
    Upgrade/downgrade an existing subscription to a new tier.
    Uses the existing Stripe subscription, modifies the price item.
    """
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth_login'))

    valid_tiers = {'pro': 'Pro', 'pro_plus': 'Pro+'}
    if tier not in valid_tiers:
        return redirect(url_for('upgrade_page'))

    # Get user's current subscription from their profile
    supabase = get_supabase_client()
    profile = supabase.table('profiles').select('stripe_subscription_id, tier').eq('user_id', user_id).execute()
    if not profile.data:
        return redirect(url_for('upgrade_page'))

    current_sub_id = profile.data[0].get('stripe_subscription_id')
    if not current_sub_id:
        # No existing subscription — go to checkout instead
        return redirect(url_for('checkout_route', tier=tier))

    try:
        # Check subscription status first
        sub = _stripe.Subscription.retrieve(current_sub_id)
        if sub.status in ('incomplete_expired', 'canceled', 'unpaid'):
            # Subscription is dead — clear it and go to checkout for a new one
            supabase.table('profiles').update({
                'stripe_subscription_id': None,
                'stripe_customer_id': None,
            }).eq('user_id', user_id).execute()
            app.logger.info(f"[UPGRADE] Subscription {current_sub_id} is {sub.status}, redirecting to checkout")
            return redirect(url_for('checkout_route', tier=tier))

        result = upgrade_subscription(current_sub_id, tier)
        app.logger.info(f"[UPGRADE] user_id={user_id}, result={result}")

        # Update local profile tier immediately (webhook will also fire)
        new_tier = result.get('tier', tier)
        supabase.table('profiles').update({
            'tier': new_tier,
        }).eq('user_id', user_id).execute()

        log_event(user_id, 'click_upgrade', tier=tier, from_route='upgrade_subscription')
        return redirect(url_for('dashboard', upgrade='success'))
    except Exception as e:
        app.logger.error(f"[UPGRADE] Error upgrading subscription: {e}")
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

        app.logger.info(f"[WEBHOOK] event_type={event['type']}, obj_keys={list(obj.keys()) if hasattr(obj, 'keys') else 'no keys'}")
        if event['type'] == 'checkout.session.completed':
            # checkout.session uses different field access
            sub_id = getattr(obj, 'subscription', None) or (obj['subscription'] if 'subscription' in obj else None)
            user_id = getattr(obj, 'client_reference_id', None) or (obj['client_reference_id'] if 'client_reference_id' in obj else None)
            app.logger.info(f"[WEBHOOK] checkout.session: sub_id={sub_id}, user_id={user_id}")
        else:
            # For subscription events, safely access attributes
            sub_id = getattr(obj, 'id', None)
            metadata = getattr(obj, 'metadata', {}) or {}
            user_id = metadata.get('user_id') if isinstance(metadata, dict) else None
            app.logger.info(f"[WEBHOOK] subscription event: sub_id={sub_id}, user_id={user_id}, metadata={metadata}")

        if sub_id and user_id:
            app.logger.info(f"[WEBHOOK] Updating profile for user_id={user_id} to tier from subscription")
            # Get the price ID from the subscription
            sub = _stripe.Subscription.retrieve(sub_id)
            price_id = sub['items']['data'][0]['price']['id']
            tier = get_tier_from_price_id(price_id)
            app.logger.info(f"[WEBHOOK] price_id={price_id}, tier={tier}")

            supabase = get_supabase_client()
            # Use upsert so users whose profiles row was never created at signup
            # (the try/except in sign_up swallowed the error) still get tier set.
            # ON CONFLICT (user_id) DO UPDATE — preserves existing cv_count etc.
            try:
                result = supabase.table('profiles').upsert({
                    'user_id': user_id,
                    'tier': tier,
                    'stripe_subscription_id': sub_id,
                    'stripe_customer_id': getattr(sub, 'customer', None),
                    'updated_at': 'now()',
                }, on_conflict='user_id').execute()
                app.logger.info(f"[WEBHOOK] profiles upsert OK for user_id={user_id}, tier={tier}")
            except Exception as e:
                app.logger.error(f"[WEBHOOK] profiles upsert FAILED for user_id={user_id}: {e}")
        else:
            app.logger.warning(f"[WEBHOOK] Skipping update - sub_id={sub_id}, user_id={user_id}")

    elif event['type'] == 'customer.subscription.deleted':
        obj = event['data']['object']
        metadata = getattr(obj, 'metadata', {}) or {}
        user_id = metadata.get('user_id') if isinstance(metadata, dict) else None
        if user_id:
            supabase = get_supabase_client()
            supabase.table('profiles').update({
                'tier': 'free',
                'stripe_subscription_id': None,
                'stripe_customer_id': None,
            }).eq('user_id', user_id).execute()

    return jsonify({'ok': True})


# ============ ROUTES ============

@app.route('/')
def index():
    """Landing page."""
    log_event(session.get('user_id'), 'view_landing')
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

    # Load profile from DB for fresh cv_count
    profile = get_or_create_profile(user_id) if user_id else {}
    app.logger.info(f"[DASHBOARD] profile from DB: user_id={user_id}, cv_count={profile.get('cv_count')}, tier={profile.get('tier')}")

    upgrade_success = request.args.get('upgrade') == 'success'
    return render_template('dashboard.html', upgrade_success=upgrade_success, cv_data=cv_data, job_data=job_data, profile=profile)


@app.route('/cv/upload')
def cv_upload_page():
    """CV upload page."""
    init_session()
    if not session.get('user_id'):
        return redirect(url_for('auth_login', next='/cv/upload'))
    log_event(session.get('user_id'), 'view_upload')
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
        # Persist to Supabase if user is logged in
        user_id = session.get('user_id')
        if user_id:
            app.logger.info(f"[CV] user_id={user_id}, cv_name={cv_data.get('name', 'N/A')}")
            save_result = save_cv(user_id, cv_data)
            app.logger.info(f"[CV] save_cv result: {save_result}")
            # Hard-fail: if save_cv returned None, the upsert errored silently
            # (most commonly: missing columns in user_cvs — see migrations/003).
            # Don't redirect the user to "success" if nothing was saved — surface
            # the error so they retry instead of bouncing.
            if save_result is None:
                log_event(user_id, 'cv_save_failed')
                return jsonify({
                    'error': 'Could not save your CV. Please try again — if it keeps failing, contact support.'
                }), 500
            ext = file.filename.lower().split('.')[-1]
            stored_filename = f"cv_{user_id[:8]}.{ext}"
            upload_result = upload_raw_file(user_id, file_bytes, stored_filename, content_type)
            app.logger.info(f"[CV] upload_raw_file result: {upload_result}")
            log_event(user_id, 'upload_cv', filename=file.filename, ext=ext)

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
            # Don't store in session — it's already in Supabase. Keeps cookie small.
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

    # Persist updated CV to Supabase only — don't bloat the session cookie
    user_id = session.get('user_id')
    if user_id:
        save_result = save_cv(user_id, profile)
        if save_result is None:
            # Hard-fail: silent success on a failed save is the bug we just
            # shipped a fix for. Mirror the /cv/parse hard-fail pattern so the
            # user sees an error instead of a fake success.
            app.logger.error(f"[SAVE] save_cv returned None for user_id={user_id}")
            log_event(user_id, 'cv_save_failed', source='save_profile_route')
            return jsonify({
                'error': 'Could not save your changes. Please try again — if it keeps failing, contact support.'
            }), 500

    app.logger.info(f"[SAVE] final profile experience: {profile['experience']}")
    app.logger.info(f"[SAVE] final profile education: {profile['education']}")
    return redirect(url_for('edit_profile_page', success=1))


@app.route('/job/paste')
def job_paste_page():
    """Job URL/text paste page."""
    init_session()
    log_event(session.get('user_id'), 'view_paste_job')
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

    # Build ats_keywords from ALL requirement categories so tools like Salesforce
    # (which live in requirements['tools'], not requirements['skills']) get checked
    ats_keywords = []
    if isinstance(requirements, dict) and 'error' not in requirements:
        all_keywords = []
        for category in ['skills', 'certifications', 'tools', 'other',
                         'experience_years', 'leadership']:
            items = requirements.get(category, [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, str) and item.strip():
                        all_keywords.append(item.strip())
                    elif isinstance(item, dict):
                        # experience_years / leadership dicts: keys are requirement names
                        for k in item.keys():
                            if k.strip():
                                all_keywords.append(k.strip())
        ats_keywords = [k.lower() for k in all_keywords]
        app.logger.info(f"[JOB] ats_keywords from all categories: count={len(ats_keywords)}, keywords={ats_keywords}")

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
    log_event(user_id, 'paste_job', job_id=job_id, keywords=len(ats_keywords))

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
            'ats_keywords': job_record.get('ats_keywords') if isinstance(job_record.get('ats_keywords'), str) else json.dumps(job_record.get('ats_keywords', [])),
            'gaps': json.dumps(job_record.get('gaps')) if job_record.get('gaps') else None,
            'requirements': json.dumps(job_record.get('requirements')) if job_record.get('requirements') else None,
            'gap_answers': json.dumps(job_record.get('gap_answers', [])) if job_record.get('gap_answers') is not None else None,
            'cover_letter': job_record.get('cover_letter', ''),
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
                'cover_letter': jd.get('cover_letter', ''),
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

    log_event(user_id, 'view_gap_answer')
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
        log_event(user_id, 'submit_gap_answer', requirement_len=len(requirement), answer_len=len(answer))
        return jsonify({'success': True, **result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/gap/confirm-destination', methods=['POST'])
def gap_confirm_destination_route():
    """Step 2: User picked destination. Return the AI-phrased text for confirmation.
    
    No CV modification here — gap answers are saved as knowledge to user_cvs.gap_answers.
    The tailoring step (tailor_cv) reads gap_answers directly and uses them to write
    better bullets. No need to pre-merge into CV sections."""
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

    try:
        # Build human-readable confirmation label
        if destination.get('type') == 'job':
            cv_data = load_cv(user_id)
            if cv_data:
                idx = destination.get('job_idx', 0)
                jobs = cv_data.get('experience', [])
                job = jobs[idx] if idx < len(jobs) else jobs[-1] if jobs else {}
                label = f"{job.get('title', 'Job')} @ {job.get('company', '')}"
            else:
                label = 'Work Experience'
        else:
            label = destination.get('label', category.title())

        app.logger.info(f"[GAP] confirm-destination: requirement={requirement}, label={label}")

        return jsonify({
            'success': True,
            'applied_to': destination.get('type', 'other'),
            'applied_text': interpreted,
            'confirmation_label': label,
            'cv_modification': {}  # No longer used — gap answers stay as knowledge
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/gap/confirm-answer', methods=['POST'])
def gap_confirm_answer_route():
    """Step 3: User confirmed. Save the gap answer to user_cvs.gap_answers (permanent knowledge base).
    
    NO CV modification here. tailor_cv reads gap_answers directly and uses them
    to write better bullets during the tailoring step. This avoids the double-merge
    bugs from the old flow (confirm-destination merged to CV, then confirm-answer merged again)."""
    data = request.get_json()
    requirement = data.get('requirement', '').strip()
    answer = data.get('answer', '').strip()
    interpreted = data.get('interpreted', '').strip()
    category = data.get('category', 'other')
    destination = data.get('destination', {})
    user_id = session.get('user_id')

    if not all([requirement, interpreted]) or not user_id:
        return jsonify({'error': 'Missing data'}), 400

    try:
        gap_answer = {
            'requirement': requirement,
            'user_answer': answer,
            'ai_phrased': interpreted,
            'category': category,
            'destination_label': destination.get('label', category.title()),
            'applied_to': destination.get('type', 'other'),
            'answered_at': _iso_now()
        }

        # Save to user_cvs.gap_answers (permanent, cross-job knowledge base)
        supabase = get_supabase_client()
        user_cv = supabase.table('user_cvs').select('gap_answers').eq('user_id', user_id).maybe_single().execute()
        
        existing_answers = []
        if user_cv and user_cv.data:
            raw = user_cv.data.get('gap_answers')
            if raw:
                existing_answers = json.loads(raw) if isinstance(raw, str) else raw
            elif isinstance(raw, list):
                existing_answers = raw
        
        # Deduplicate by requirement — replace old answer for same requirement
        all_answers = [a for a in existing_answers if a.get('requirement') != requirement]
        all_answers.append(gap_answer)
        
        result = supabase.table('user_cvs').update({
            'gap_answers': json.dumps(all_answers, ensure_ascii=False)
        }).eq('user_id', user_id).execute()
        
        app.logger.info(f"[GAP] confirm-answer saved: requirement={requirement}, total_answers={len(all_answers)}")
        return jsonify({'success': True, 'total_answers': len(all_answers)})
        
    except Exception as e:
        import traceback
        app.logger.info(f"[GAP] confirm-answer CRASH: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


def _iso_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def merge_cv_sections(cv_data: dict, modification: dict) -> dict:
    """
    Merge cv_modification into cv_data.
    modification keys: 'job_0', 'job_1', ..., 'skills', 'certifications', 'projects', 'summary'
    """
    updated = dict(cv_data)

    # Preserve gap_answers — don't let them be wiped
    gap_answers = cv_data.get('gap_answers', [])

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
        # Build separate lists for string skills and dict skill names for dedup
        existing_str_skills = [s if isinstance(s, str) else '' for s in updated['skills']]
        existing_dict_names = [x.get('name', '') if isinstance(x, dict) else '' for x in updated['skills']]
        for s in new_skills:
            if isinstance(s, str):
                if s not in existing_str_skills and s not in existing_dict_names:
                    updated['skills'].append(s)
                    existing_str_skills.append(s)
            elif isinstance(s, dict):
                name = s.get('name', '')
                if name and name not in existing_str_skills and name not in existing_dict_names:
                    updated['skills'].append(s)
                    existing_dict_names.append(name)
    elif applied_to == 'certifications':
        updated['certifications'] = updated.get('certifications', [])
        new_certs = modification.get('cv_modification', [])
        existing_certs = updated['certifications']
        for c in new_certs:
            if isinstance(c, str) and c not in existing_certs:
                updated['certifications'].append(c)
            elif isinstance(c, dict) and c.get('name') not in [x.get('name') if isinstance(x, dict) else x for x in existing_certs]:
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
    elif applied_to == 'additional_info':
        # Catch-all for multi-item skill lists (Salesforce, HubSpot, Notion, etc.)
        # Stored as 'additional_info' list in CV data
        updated['additional_info'] = updated.get('additional_info', [])
        new_items = modification.get('cv_modification', [])
        existing_items = updated['additional_info']
        for item in new_items:
            if item not in existing_items:
                existing_items.append(item)
        updated['additional_info'] = existing_items
    else:
        # Catch-all: if applied_to is something unexpected (education, languages, etc.)
        # store the modification so it's never silently lost
        extra = updated.get('_extra_modifications', [])
        mod_content = modification.get('cv_modification', modification)
        if mod_content not in extra:
            extra.append(mod_content)
        updated['_extra_modifications'] = extra

    # Restore preserved gap_answers
    updated['gap_answers'] = gap_answers

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

    # Load requirements first (needed for both fresh gaps and ATS scoring)
    requirements = job_data.get('requirements', {})

    # Load or generate gaps — prefer Supabase, fall back to on-the-fly
    app.logger.info(f"[ANALYZE] job_data.keys={list(job_data.keys()) if job_data else 'empty'}, gaps={'yes' if job_data.get('gaps') else 'NONE'}")
    gaps = job_data.get('gaps')
    if not gaps:
        if not requirements:
            requirements = extract_requirements(job_data.get('description', ''))
        try:
            gaps = analyze_gaps(cv_data, requirements)
        except Exception as e:
            return render_template('gap_analyze.html', gaps={}, questions=[], interview_likelihood=50, error=str(e))

    # Ensure ats_score is always calculated fresh (not from stale cache)
    ats_result = score_ats_keywords(cv_data, requirements)
    app.logger.info(f"[ANALYZE] ats_result: score={ats_result['ats_score']}, found={ats_result['found'][:10]}, total_keywords={len(ats_result['found'])+len(ats_result['missing'])}")
    app.logger.info(f"[ANALYZE] requirements keys: {list(requirements.keys()) if requirements else 'NONE'}")
    app.logger.info(f"[ANALYZE] cv_data keys: {list(cv_data.keys()) if cv_data else 'NONE'}, exp_count={len(cv_data.get('experience',[])) if cv_data else 0}")
    gaps['ats_score'] = ats_result['ats_score']
    gaps['ats_keywords_found'] = ats_result['found']
    gaps['ats_keywords_missing'] = ats_result['missing']

    # Persist gaps to Supabase for session-free access
    try:
        job_record = {
            'user_id': user_id,
            'description': job_data.get('description', ''),
            'gaps': gaps,
            'requirements': requirements,
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
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login_page'))

    # Always regenerate — clear any stale tailored_cv from session
    session.pop('tailored_cv', None)

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
    requirements = job_data.get('requirements', {})

    # gap_answers: always pull from user_cvs (permanent record of all gap
    # answers across all jobs). Merge with per-job answers so current job
    # context takes priority.
    user_cv = load_cv(user_id) if user_id else {}
    user_gap_answers = user_cv.get('gap_answers', []) if user_cv else []
    job_gap_answers = job_data.get('gap_answers', []) if job_data else []
    # Build dict keyed by requirement so per-job answers override user-level ones
    gap_answers_map = {a.get('requirement', ''): a for a in user_gap_answers}
    for a in job_gap_answers:
        gap_answers_map[a.get('requirement', '')] = a
    gap_answers = list(gap_answers_map.values())

    if not job_description:
        return redirect(url_for('job_paste_page'))

    tailored = tailor_cv(cv_data, gap_answers, job_description, ats_keywords, requirements)
    session['tailored_cv'] = tailored

    if user_id:
        app.logger.info(f"[TAILOR_CV_PAGE] calling increment_cv_count for user_id={user_id}")
        increment_cv_count(user_id)

    return redirect(url_for('cv_preview_page'))


@app.route('/cv/optimise', methods=['POST'])
def optimise_cv_route():
    """Optimise the current tailored CV for ATS keyword match."""
    init_session()
    user_id = session.get('user_id')

    tailored_cv = session.get('tailored_cv')
    if not tailored_cv:
        return jsonify({'error': 'No tailored CV found. Please run Tailor CV first.'}), 400

    job_data = load_job_description(user_id) if user_id else {}
    job_description = job_data.get('description', '')
    ats_keywords = job_data.get('ats_keywords', [])
    requirements = job_data.get('requirements', {})

    # Load gap answers from Supabase (permanent profile knowledge)
    gap_answers = []
    if user_id:
        cv_data = load_cv(user_id)
        if cv_data:
            gap_answers = cv_data.get('gap_answers') or []

    try:
        optimised = optimise_cv_for_ats(
            tailored_cv,
            job_description,
            ats_keywords,
            requirements,
            gap_answers
        )
        app.logger.info(f"[OPTIMISE] returned keys: {list(optimised.keys())}, summary: {optimised.get('summary', 'MISSING')[:100] if optimised.get('summary') else 'MISSING'}")
        session['tailored_cv'] = optimised
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': f'Optimisation failed: {str(e)}'}), 500


@app.route('/cv/tailor/delete', methods=['POST'])
def delete_tailored_cv():
    """Delete the tailored CV from session."""
    init_session()
    session.pop('tailored_cv', None)
    return redirect(url_for('dashboard'))


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
    requirements = job_data.get('requirements', {})

    if not job_description:
        return jsonify({'error': 'No job data'}), 400

    # Check CV count gating if user is logged in
    if user_id:
        allowed, reason, profile = can_generate_cv(user_id)
        if not allowed:
            return jsonify({'error': 'limit_reached', 'redirect': url_for('upgrade_page')}), 403

    try:
        tailored = tailor_cv(cv_data, gap_answers, job_description, ats_keywords, requirements)
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
    """Light-editable CV preview — uses cv_editor.html for inline editing."""
    init_session()
    tailored_cv = session.get('tailored_cv')
    log_event(session.get('user_id'), 'view_preview')

    # Dev mode: if no tailored_cv but request has ?mock=1, use sample data
    if not tailored_cv and request.args.get('mock') == '1':
        tailored_cv = {
            "personal": {
                "name": "Alex Chen", "title": "Senior Product Manager",
                "email": "alex@techcorp.com", "phone": "+852 9123 4567",
                "location": "Hong Kong",
                "summary": "Experienced product leader with 10+ years building AI-powered products across Asia-Pacific. Proven track record of scaling products from zero to millions of users."
            },
            "experience": [
                {"title": "Senior Product Manager", "company": "TechCorp Ltd", "dates": "2020 – Present",
                 "highlights": ["Led a team of 12 product managers across 3 countries", "Increased revenue by 40% through AI-powered features", "Launched products used by 2M+ users"]},
                {"title": "Product Manager", "company": "StartupXYZ", "dates": "2017 – 2020",
                 "highlights": ["Scaled user base from 10K to 500K", "Managed $2M annual product budget"]}
            ],
            "skills": [{"name": "AI Strategy"}, {"name": "Product Roadmap"}, {"name": "Agile"}, {"name": "Stakeholder Management"}, {"name": "Data Analytics"}],
            "education": [{"degree": "MBA", "school": "HKUST", "year": "2017"}, {"degree": "BEng Computer Science", "school": "CUHK", "year": "2014"}]
        }
    elif not tailored_cv:
        return redirect(url_for('cv_upload_page'))

    template_ctx = _prepare_cv_context(tailored_cv)
    template_ctx['profile'] = {'name': tailored_cv.get('personal', {}).get('name', 'My CV')}
    return render_template('cv_editor.html', **template_ctx)


@app.route('/cv/editor')
def cv_editor_page():
    """Light-editable CV preview on the grid-background page."""
    init_session()
    tailored_cv = session.get('tailored_cv')

    # Dev mode: if no tailored_cv but request has ?mock=1, use sample data
    if not tailored_cv and request.args.get('mock') == '1':
        tailored_cv = {
            "personal": {
                "name": "Alex Chen", "title": "Senior Product Manager",
                "email": "alex@techcorp.com", "phone": "+852 9123 4567",
                "location": "Hong Kong",
                "summary": "Experienced product leader with 10+ years building AI-powered products across Asia-Pacific. Proven track record of scaling products from zero to millions of users."
            },
            "experience": [
                {"title": "Senior Product Manager", "company": "TechCorp Ltd", "dates": "2020 – Present",
                 "highlights": ["Led a team of 12 product managers across 3 countries", "Increased revenue by 40% through AI-powered features", "Launched products used by 2M+ users"]},
                {"title": "Product Manager", "company": "StartupXYZ", "dates": "2017 – 2020",
                 "highlights": ["Scaled user base from 10K to 500K", "Managed $2M annual product budget"]}
            ],
            "skills": [{"name": "AI Strategy"}, {"name": "Product Roadmap"}, {"name": "Agile"}, {"name": "Stakeholder Management"}, {"name": "Data Analytics"}],
            "education": [{"degree": "MBA", "school": "HKUST", "year": "2017"}, {"degree": "BEng Computer Science", "school": "CUHK", "year": "2014"}]
        }
    elif not tailored_cv:
        return redirect(url_for('cv_upload_page'))

    template_ctx = _prepare_cv_context(tailored_cv)
    template_ctx['profile'] = {'name': tailored_cv.get('personal', {}).get('name', 'My CV')}
    return render_template('cv_editor.html', **template_ctx)


@app.route('/cv/save-editor', methods=['POST'])
def save_cv_editor():
    """Save edited CV fields from the editor page."""
    data = request.get_json()
    fields = data.get('fields', {})
    tailored_cv = session.get('tailored_cv', {})
    # Apply edits to tailored_cv using dot-notation keys
    for key, value in fields.items():
        parts = key.split('.')
        d = tailored_cv
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        d[parts[-1]] = value
    # Also update personal sub-object if name/title/summary were edited
    if 'personal' in tailored_cv:
        for k in ['name', 'title', 'summary', 'email', 'phone', 'location']:
            if fields.get(f'personal.{k}'):
                tailored_cv['personal'][k] = fields[f'personal.{k}']
    session['tailored_cv'] = tailored_cv
    return jsonify({'success': True})


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

        log_event(user_id, 'download_pdf', template=selected_template, filename=filename)
        return send_file(pdf_buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')
    except Exception as e:
        return jsonify({'error': f'Failed to generate PDF: {str(e)}'}), 500


@app.route('/cover-letter/generate', methods=['POST'])
def cover_letter_generate_route():
    """API: Generate cover letter from cv_editor page (no JSON body needed)."""
    user_id = session.get('user_id')
    cv_data = session.get('cv_data')
    if not cv_data and user_id:
        cv_data = load_cv(user_id)
    job_data = load_job_description(user_id) if user_id else {}
    gap_answers = job_data.get('gap_answers', []) if job_data else []

    if not cv_data:
        return jsonify({'error': 'No CV data'}), 400

    try:
        cover_letter = generate_cover_letter(
            cv_data,
            gap_answers,
            job_data.get('description', ''),
            job_data.get('company', ''),
            job_data.get('title', ''),
            'professional'
        )
        session['cover_letter'] = cover_letter
        if user_id:
            save_job_description({'user_id': user_id, 'cover_letter': cover_letter})
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': f'Failed to generate cover letter: {str(e)}'}), 500


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
        # Save to Supabase job_descriptions so it's available on refresh
        if user_id:
            save_job_description({'user_id': user_id, 'cover_letter': cover_letter})
        return jsonify({'success': True, 'cover_letter': cover_letter})
    except Exception as e:
        return jsonify({'error': f'Failed to generate cover letter: {str(e)}'}), 500


@app.route('/cover-letter/preview')
def cover_letter_preview_page():
    """Preview cover letter — load from Supabase via job description."""
    user_id = session.get('user_id')
    job_data = load_job_description(user_id) if user_id else {}
    cover_letter = job_data.get('cover_letter', '') or session.get('cover_letter', '')
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


@app.route('/debug')
def debug_test():
    """Test endpoint to verify Railway is running latest code."""
    return {'status': 'ok', 'message': 'debug route working', 'time': str(__import__('datetime').datetime.now())}

@app.route('/debug/analyze')
def debug_analyze():
    """Debug route: show raw gap analysis computation."""
    if not session.get('user_id'):
        return {'error': 'not logged in'}
    user_id = session.get('user_id')

    cv_data = load_cv(user_id)
    job_data = load_job_description(user_id)

    requirements = job_data.get('requirements', {})
    gaps = job_data.get('gaps')

    # Debug: log what we received
    import sys
    print(f"[DEBUG] user_id={user_id}", file=sys.stderr)
    print(f"[DEBUG] cv_data type={type(cv_data).__name__}, keys={list(cv_data.keys()) if cv_data else None}", file=sys.stderr)
    print(f"[DEBUG] job_data type={type(job_data).__name__}, keys={list(job_data.keys()) if job_data else None}", file=sys.stderr)
    print(f"[DEBUG] requirements type={type(requirements).__name__}, keys={list(requirements.keys()) if isinstance(requirements, dict) else str(requirements)[:100]}", file=sys.stderr)
    print(f"[DEBUG] requirements={requirements}", file=sys.stderr)

    # Run fresh ATS calculation
    ats_result = score_ats_keywords(cv_data, requirements) if cv_data else {'ats_score': 0, 'found': [], 'missing': []}

    print(f"[DEBUG] ats_result={ats_result}", file=sys.stderr)

    return {
        'cv_data_keys': list(cv_data.keys()) if cv_data else None,
        'cv_experience_count': len(cv_data.get('experience', [])) if cv_data else 0,
        'cv_skills': cv_data.get('skills', []) if cv_data else [],
        'job_data_keys': list(job_data.keys()) if job_data else None,
        'requirements_type': type(requirements).__name__,
        'requirements_keys': list(requirements.keys()) if isinstance(requirements, dict) else str(requirements)[:100],
        'requirements': requirements,
        'gaps_type': type(gaps).__name__,
        'gaps_keys': list(gaps.keys()) if isinstance(gaps, dict) else str(gaps)[:200] if gaps else None,
        'ats_score': ats_result['ats_score'],
        'ats_found': ats_result['found'],
        'ats_missing': ats_result['missing'],
    }