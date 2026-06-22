-- 007_password_reset_tokens.sql
-- Adds password reset token storage for the custom Resend-based reset flow.
-- Replaces the previous Supabase-default reset_password_email() call.
--
-- Idempotent: safe to re-run.
-- Created: 2026-06-22

create table if not exists public.password_reset_tokens (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid not null references auth.users(id) on delete cascade,
    token text unique not null,
    expires_at timestamptz not null,
    used_at timestamptz,
    created_at timestamptz not null default now()
);

-- Lookups by token (the hot path on the reset-password page)
create index if not exists idx_password_reset_tokens_token
    on public.password_reset_tokens(token);

-- Cleanup of expired tokens (cron job later, or just leave them)
create index if not exists idx_password_reset_tokens_user_id
    on public.password_reset_tokens(user_id);

-- RLS: deny everything from the anon/authenticated role at the table level.
-- The Flask app uses the service_role key to insert/consume tokens, never the
-- user's session. The public schema's anon role must NOT be able to read tokens
-- (would let any logged-in user reset someone else's password by guessing token).
alter table public.password_reset_tokens enable row level security;

drop policy if exists "deny all on password_reset_tokens" on public.password_reset_tokens;
create policy "deny all on password_reset_tokens"
    on public.password_reset_tokens
    for all
    to public
    using (false)
    with check (false);

-- Note: the policy "to public" blocks both anon and authenticated. Only the
-- service_role (used by the Flask backend) bypasses RLS, which is what we want.
