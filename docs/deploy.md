# Deploying SalaryBridge

Backend (FastAPI) → **Render** free web service. Frontend (Next.js) → **Vercel**.
Both need your own accounts; nothing here is deployed automatically.

## What was verified locally (production-style, F24)

- A clean Python 3.11 environment installed from `backend/requirements.txt` alone loads all five models.
- The API cold-started with no `seed.db` in **13 s** (it builds the 9 MB seed database at startup).
- Memory: peak **335 MB**, steady **~295 MB** after a full demo flow (employee, payday, ops, validation).
- `next build` with `NEXT_PUBLIC_API_BASE_URL` pointing at that API; every page served, the full flow worked, and CORS
  allowed only the configured frontend origin.

## 1. The code on GitHub

The project lives at `https://github.com/aqshanto/SalaryBridge` (branch `main`) and already contains everything both
hosts need: `render.yaml`, `backend/.python-version`, the pinned `backend/requirements.txt` and the trained models in
`backend/artifacts/` (11 MB, committed on purpose; the API loads them at startup). `seed.db`, `.venv/`,
`node_modules/`, `.next/` and `.env` files are git-ignored and must stay that way.

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

## Environment variables

### Render (backend)

| Variable | Value | Required | Notes |
|---|---|---|---|
| `PYTHON_VERSION` | `3.11.9` | yes | Set by `render.yaml`. The models were pickled on 3.11; Render's default is newer. |
| `CORS_ORIGINS` | `https://<your-app>.vercel.app` | yes | Exact origin, no trailing slash. Comma-separate several (production, preview). |
| `SEED` | `42` | yes | Set by `render.yaml`. The same seed always builds the same synthetic world. |
| `ANTHROPIC_API_KEY` | your key | no | Enables the "AI wording" button. Without it the template explanation is used. Set it only in the Render dashboard; never commit it. |
| `LLM_MODEL` | `claude-opus-5-5` | no | Set by `render.yaml`. |
| `LLM_TIMEOUT_S` | `20` | no | Seconds before the explanation falls back to the template. |
| `SESSION_TTL_HOURS` | `6` | no | Idle sandbox sessions are deleted after this. |
| `SIM_DIR` | (unset) | no | Folder for per-session databases; defaults to a temp folder. |
| `DATABASE_URL` | (unset) | no | Leave unset: the seed database is built next to the code at startup. |
| `POLICY__<NAME>` | e.g. `POLICY__FEE_FLAT_BDT=49` | no | Overrides any policy number in `docs/assumptions.md` (double underscore). |

`PORT` is provided by Render and used by the start command; do not set it.

### Vercel (frontend)

| Variable | Value | Required | Notes |
|---|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `https://<your-service>.onrender.com` | yes | No trailing slash. Read at build time: redeploy after changing it. Set it for Production (and Preview if you use preview URLs). |

### Local development

`backend/.env.example` and `frontend/.env.example` hold the local defaults (`http://localhost:8000` and
`http://localhost:3000`). Copy them to `.env` / `.env.local` only if you need to change something.

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
