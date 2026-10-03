# Deploying SalaryBridge

Backend (FastAPI) → **Render** free web service. Frontend (Next.js) → **Vercel**.
Both need your own accounts; nothing here is deployed automatically.

## What was verified locally (production-style, F24)

- A clean Python 3.11 environment installed from `backend/requirements.txt` alone loads all five models.
- The API cold-started with no `seed.db` in **13 s** (it builds the 9 MB seed database at startup).
- Memory: peak **335 MB**, steady **~295 MB** after a full demo flow (employee, payday, ops, validation).
- `next build` with `NEXT_PUBLIC_API_BASE_URL` pointing at that API; every page served, the full flow worked, and CORS
  allowed only the configured frontend origin.

## 1. Push the latest code to GitHub

The project already lives at `https://github.com/aqshanto/SalaryBridge` (branch `main`). Commit and push the
deployment files (`render.yaml`, `backend/.python-version`, `backend/requirements*.txt`, `docs/deploy.md`):

```bash
git add render.yaml backend/.python-version backend/requirements.txt backend/requirements-dev.txt docs/deploy.md README.md
```

```bash
git commit -m "Add Render and Vercel deployment config"
```

```bash
git push
```

`seed.db`, `.venv/`, `node_modules/`, `.next/` and `.env` files are git-ignored. The trained models in
`backend/artifacts/` (11 MB) **are** committed; the API needs them.

## 2. Backend on Render

1. Render dashboard → **New** → **Blueprint** → pick the repository. Render reads `render.yaml`.
2. When asked:
   - `CORS_ORIGINS`: leave a placeholder for now (e.g. `http://localhost:3000`); you will set the Vercel URL in step 4.
   - `ANTHROPIC_API_KEY`: optional. Leave empty to use the built-in explanation templates.
3. Deploy. The first build takes several minutes (scientific Python wheels). Open
   `https://<your-service>.onrender.com/health`; it should return `{"status":"ok",...}`.

`render.yaml` pins `PYTHON_VERSION=3.11.9` (Render's default is newer, and the models were pickled on 3.11) and sets
`healthCheckPath: /health`, region `singapore`, plan `free`.

## 3. Frontend on Vercel

1. Vercel → **Add New** → **Project** → import the same repository.
2. **Root Directory**: `frontend`. Framework: Next.js (detected).
3. Environment variable: `NEXT_PUBLIC_API_BASE_URL` = `https://<your-service>.onrender.com` (no trailing slash).
4. Deploy. Note the URL, e.g. `https://salarybridge.vercel.app`.

`NEXT_PUBLIC_*` values are baked in at build time: if you change it, redeploy the frontend.

## 4. Connect them

On Render → your service → **Environment** → set `CORS_ORIGINS` to the Vercel URL (comma-separate several, e.g. the
production URL and a preview URL). Save; Render redeploys.

## 5. Demo day checklist

- **10 minutes before**: open `https://<your-service>.onrender.com/health`. A free service sleeps after 15 minutes
  without traffic and takes about a minute to wake.
- **Keep it awake during the demo**: any request counts. If there is a long pause, open `/health` again.
- **Sessions do not survive a sleep or redeploy**: the free disk is ephemeral. The app rebuilds the seed and starts
  each session fresh, so just press **Reset** or a scenario button.
- Start each run with **Reset** (or a scenario button) so every demo starts on 20 October 2026.
- Check the ledger badge shows **✓ Reconciled**.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Page shows "Cannot reach the API" | API asleep (wait a minute and retry) or wrong `NEXT_PUBLIC_API_BASE_URL` (fix and redeploy the frontend) |
| Browser console shows a CORS error | `CORS_ORIGINS` on Render does not exactly match the Vercel URL (scheme, no trailing slash) |
| Render build fails on scikit-learn / LightGBM | Python version not pinned: check `PYTHON_VERSION=3.11.9` |
| Model loading error after changing `requirements.txt` | Library versions must match the ones the models were trained with; retrain (README) or restore the pins |
| Out-of-memory restarts | Measured peak is 335 MB; check the plan's memory limit on Render |
| AI explanation button missing | Expected without `ANTHROPIC_API_KEY`; the template explanation is always shown |
