-- TailorMyCV Supabase Schema

-- Profiles table (extends Supabase Auth users)
create table public.profiles (
    id uuid references auth.users on delete cascade primary key,
    email text,
    photo_url text,
    created_at timestamptz default now()
);

-- Enable RLS
alter table public.profiles enable row level security;

-- Profiles policy: users can only see/edit their own profile
create policy "Users can manage own profile" on public.profiles
    for all using auth.uid() = id;

-- Base CVs table
create table public.base_cvs (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references public.profiles(id) on delete cascade,
    parsed_cv_json jsonb not null,
    original_filename text,
    created_at timestamptz default now()
);

-- Enable RLS on base_cvs
alter table public.base_cvs enable row level security;

-- Base CVs policy: users can only see/edit their own CVs
create policy "Users can manage own CVs" on public.base_cvs
    for all using auth.uid() = user_id;

-- Tailored CVs table
create table public.tailored_cvs (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references public.profiles(id) on delete cascade,
    base_cv_id uuid references public.base_cvs(id),
    job_url text,
    job_title text,
    job_company text,
    tailored_cv_json jsonb,
    gap_answers jsonb default '[]',
    cover_letter_text text,
    template text default 'modern',
    created_at timestamptz default now()
);

-- Enable RLS on tailored_cvs
alter table public.tailored_cvs enable row level security;

-- Tailored CVs policy
create policy "Users can manage own tailored CVs" on public.tailored_cvs
    for all using auth.uid() = user_id;

-- Gap Answers table
create table public.gap_answers (
    id uuid primary key default gen_random_uuid(),
    tailored_cv_id uuid references public.tailored_cvs(id) on delete cascade,
    requirement text,
    user_answer text,
    ai_phrased text,
    created_at timestamptz default now()
);

-- Enable RLS on gap_answers
alter table public.gap_answers enable row level security;

-- Gap answers policy
create policy "Users can manage own gap answers" on public.gap_answers
    for all using auth.uid() = (select user_id from public.tailored_cvs where id = tailored_cv_id);

-- Trigger to create profile on user signup
create or replace function public.handle_new_user()
returns trigger as $$
begin
    insert into public.profiles (id, email)
    values (new.id, new.email);
    return new;
end;
$$ language plpgsql security definer;

create trigger on_auth_user_created
    after insert on auth.users
    for each row execute procedure public.handle_new_user();