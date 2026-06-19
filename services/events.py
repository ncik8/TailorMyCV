"""
events.py — non-blocking analytics logger for RezMyCV.

Logs user activity to public.events table. Wraps every Supabase call in
try/except so a logging failure NEVER breaks the user-facing flow.

Usage from app.py:
    from services.events import log_event
    log_event(user.id, 'signup', source='email')
    log_event(session.get('user_id'), 'view_upload')

If user_id is None (anonymous viewer), the event is still logged — it just
has a NULL user_id. This lets us see landing-page traffic too.

Why ThreadPoolExecutor instead of threading.Thread(daemon=True)?
  - A daemon thread is killed when the request handler returns on
    gunicorn/Flask worker shutdown — Supabase writes were silently lost.
  - ThreadPoolExecutor is a module-level singleton that lives for the
    lifetime of the process. Submitted tasks complete even after the
    request handler returns (as long as the worker is alive).
  - We still don't block the request — submit() returns immediately.

Tradeoff: in the worst case (worker crash mid-write), the event is lost.
For analytics, that's acceptable. We log loudly if the executor itself
errors so silent loss is detectable.
"""
import atexit
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from services.auth import get_client as get_supabase_client

_log = logging.getLogger(__name__)

# Module-level executor — survives across requests within one worker process.
# 2 workers is enough because Supabase writes are quick (~200ms) and bursts
# are rare. Bump to 4 if log latency becomes a problem.
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="events")


def _write_event(payload: dict) -> None:
    """Actually do the Supabase insert. Runs on the executor."""
    try:
        client = get_supabase_client()
        client.table("events").insert(payload).execute()
    except Exception as e:
        # Never raise from a background thread — would be lost.
        # Log so silent loss is detectable in production logs.
        _log.warning("[events] background write failed: %s: %s | payload=%s",
                     type(e).__name__, e, payload)


@atexit.register
def _shutdown_executor(wait=True):
    """On process shutdown, give in-flight events a moment to flush."""
    _executor.shutdown(wait=wait)


def log_event(user_id, event_name: str, **properties) -> None:
    """
    Log an analytics event. Safe to call anywhere — never raises.

    Args:
        user_id:     The auth.users UUID, or None for anonymous.
        event_name:  Short snake_case name, e.g. 'signup', 'view_upload'.
        **properties: Arbitrary key=value pairs stored as JSONB.

    Returns immediately. Actual write happens on a background thread.
    """
    try:
        payload = {"event_name": event_name}
        if user_id:
            payload["user_id"] = user_id
        if properties:
            # Only JSON-serializable values
            payload["properties"] = json.loads(json.dumps(properties, default=str))
        # Submit and forget — non-blocking, runs on module-level executor.
        _executor.submit(_write_event, payload)
    except Exception as e:
        # Belt and suspenders — we should never get here, but if executor
        # is shut down or queue is full, log and swallow.
        _log.warning("[events] submit failed: %s: %s", type(e).__name__, e)
