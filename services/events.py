"""
events.py — non-breaking analytics logger for RezMyCV.

Logs user activity to public.events table. Wraps every Supabase call in
try/except so a logging failure NEVER breaks the user-facing flow.

Usage from app.py:
    from services.events import log_event
    log_event(user_id, 'signup', source='email')
    log_event(session.get('user_id'), 'view_upload')

If user_id is None (anonymous viewer), the event is still logged — it just
has a NULL user_id. This lets us see landing-page traffic too.
"""
import json
import threading
from services.auth import get_client as get_supabase_client


# Fire-and-forget thread so the user never waits for the write.
# Logging a single row to Supabase takes 100-400ms — not negligible.
def _write_async(payload: dict) -> None:
    def _do():
        try:
            client = get_supabase_client()
            client.table("events").insert(payload).execute()
        except Exception:
            # Swallow all errors. Analytics must never affect the app.
            pass
    t = threading.Thread(target=_do, daemon=True)
    t.start()


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
        payload = {
            "event_name": event_name,
        }
        if user_id:
            payload["user_id"] = user_id
        if properties:
            # Only JSON-serializable values
            payload["properties"] = json.loads(json.dumps(properties, default=str))
        _write_async(payload)
    except Exception:
        # Belt and suspenders — we should never get here.
        pass
