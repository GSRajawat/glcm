# Getting This Onto GitHub

Run these from your ECMS folder (PowerShell).

## 1. One-time Git setup (skip if you already use Git)
```
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

## 2. Initialize and make the first commit
```
cd E:\Users\acer\Downloads\ECMS
git init
git add .
git commit -m "Initial commit: multi-tenant exam management system"
```
`.gitignore` already excludes `.streamlit/secrets.toml`, `__pycache__`,
`build/`, `dist/`, `dist_protected/`, and `.spec` files — check with
`git status` before committing that `secrets.toml` is NOT listed. If it
ever does get committed by mistake, rotate your Supabase key immediately
(a git history removal alone isn't enough once it's been pushed).

## 3. Create the GitHub repo
Go to github.com → New repository → name it (e.g. `exam-management-system`)
→ **do not** initialize with a README (you already have one) → Create.

Decide public vs. private:
- **Public** — anyone can see your source code (but not your secrets,
  since those are gitignored). Fine if you're not worried about the code
  itself being copied.
- **Private** — only accounts you invite can see it. Streamlit Community
  Cloud can still deploy from a private repo once you connect your GitHub
  account.

## 4. Connect and push
GitHub will show you the exact commands after creating the repo, but
they'll look like:
```
git remote add origin https://github.com/YOUR_USERNAME/exam-management-system.git
git branch -M main
git push -u origin main
```

## 5. Deploy to Streamlit Community Cloud
Covered in README.md's "Path A" section — share.streamlit.io, point it at
this repo, paste your secrets into its Secrets manager.

## Pushing future updates
```
git add .
git commit -m "describe what changed"
git push
```
Streamlit Community Cloud auto-redeploys on every push to the connected
branch — no manual redeploy step needed.
