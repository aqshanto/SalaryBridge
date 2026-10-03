# SalaryBridge

Earned-wage advance sandbox for upay (DIU CPC x upay AI Hackathon 2026). All data is synthetic.
See `plan.md` (how it works), `plot.md` (what users see), `prompt.md` (feature order), `CLAUDE.md` (build rules).

## Backend (FastAPI, Python 3.11)

```bash
cd backend
uv venv --python 3.11 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

On first start the API builds `backend/seed.db` from the fixed seed (about 2 seconds). To rebuild it by hand: `.venv/Scripts/python.exe -m data.seed`. Delete `seed.db` to force a rebuild; the same seed always gives the same data.

On macOS/Linux use `.venv/bin/python` instead of `.venv/Scripts/python.exe`. Copy `.env.example` to `.env` to override settings.

## Frontend (Next.js)

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000. The page shows the API health. Set `NEXT_PUBLIC_API_BASE_URL` (see `frontend/.env.example`) when the API is not on `http://localhost:8000`.
