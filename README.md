# GEO Command Center — Backend

FastAPI + SQLAlchemy backend for the GEO Command Center dashboard
(OCSiAl / TUBALL). Provides CRUD APIs for prompts, AI results, sources,
URLs, recommendations, and tasks plus an OpenAI-driven recommendation
generator (spec §13).

## Run locally

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # then fill in OPENAI_API_KEY
uvicorn app.main:app --reload --port 8765
```

OpenAPI docs at http://localhost:8765/docs

## Deploy to Render

`render.yaml` declares the web service + a free Postgres database. Push
to GitHub, then in Render dashboard click **New → Blueprint** and pick
this repo. Set `OPENAI_API_KEY` in the dashboard after the service is
created.
