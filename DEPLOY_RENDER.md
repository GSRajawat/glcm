# Deploying ShopOps Feedback to Render

This is a Next.js 15 app that stores feedback in Supabase. Deploy it to Render in ~5 minutes.

---

## Step 1 — Create the Supabase table

Open your Supabase project → **SQL Editor** → **New query** → paste and Run:

```sql
create table if not exists public.shop_feedback (
  id uuid primary key default gen_random_uuid(),
  name text,
  email text,
  category text not null default 'problem'
    check (category in ('problem','bug','solution','feature')),
  title text not null,
  description text not null,
  status text not null default 'open'
    check (status in ('open','in_progress','resolved')),
  created_at timestamptz not null default now()
);

alter table public.shop_feedback enable row level security;

create policy "anon can insert feedback"
  on public.shop_feedback for insert to anon with check (true);

create policy "anon can read feedback"
  on public.shop_feedback for select to anon using (true);

create index if not exists shop_feedback_created_idx
  on public.shop_feedback (created_at desc);
```

## Step 2 — Grab your Supabase credentials

Supabase Dashboard → **Project Settings** → **API**
- Copy **Project URL** → this is `SUPABASE_URL`
- Copy **anon / public** key → this is `SUPABASE_ANON_KEY`

## Step 3 — Push code to GitHub

```bash
cd /app
git init
git add .
git commit -m "ShopOps feedback board"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

> `.env` is git-ignored, so your local secrets stay safe.

## Step 4 — Deploy on Render

### Option A — Blueprint (recommended, uses `render.yaml`)
1. Render Dashboard → **New +** → **Blueprint**
2. Connect your GitHub repo
3. Render detects `render.yaml` automatically
4. Click **Apply**
5. When prompted, paste your two secret values:
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
6. Wait ~3–5 minutes for the first build

### Option B — Manual Web Service
1. Render Dashboard → **New +** → **Web Service** → pick your repo
2. Configure:
   - **Runtime**: Node
   - **Build Command**: `yarn install --frozen-lockfile && yarn build`
   - **Start Command**: `yarn start`
   - **Health Check Path**: `/api/health`
3. Under **Environment** → add:
   | Key | Value |
   |-----|-------|
   | `NODE_VERSION` | `20.18.0` |
   | `SUPABASE_URL` | (your Supabase project URL) |
   | `SUPABASE_ANON_KEY` | (your Supabase anon key) |
   | `NODE_ENV` | `production` |
4. Click **Create Web Service**

## Step 5 — Verify

Once deploy is green:
- Visit `https://<your-service>.onrender.com` → the site loads
- Visit `https://<your-service>.onrender.com/api/health` → should return `{"ok":true,"supabase_configured":true}`
- Submit a test feedback → confirm the row appears in Supabase **Table Editor** → `shop_feedback`

---

## Troubleshooting

**❌ `supabase_configured: false`**
→ env vars not set. Check Render → Environment tab → values present and no trailing spaces → **Manual Deploy** to reload.

**❌ 500 error on POST `/api/feedback`**
→ Table doesn't exist or RLS is blocking anon. Re-run the SQL in Step 1.

**💤 First request after idle is slow (free tier)**
→ Render free instances sleep after 15 min idle. Upgrade to `starter` plan for always-on.

**🔐 Want writes only for logged-in users?**
→ Replace the anon INSERT policy with an authenticated one and add Supabase Auth.
