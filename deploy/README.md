# Deployment (docker-compose)

Brings up the full stack locally or on any machine with Docker: the FastAPI app, Postgres, and n8n.

## Prerequisites

- Docker + Docker Compose
- A `.env` file at the repo root (copy `.env.example` and fill in at least one LLM provider key)

## Bring it up

```bash
cp .env.example .env   # if you haven't already; fill in your LLM provider key(s)
docker compose -f deploy/docker-compose.yml up --build
```

This starts three services:

| Service | URL | Purpose |
|---|---|---|
| `app` | http://localhost:8000 | The FastAPI service (`/docs` for the OpenAPI UI, `/health`, `/metrics`) |
| `db` | localhost:5432 | Postgres, storing the `leads` table |
| `n8n` | http://localhost:5678 | n8n's own setup/login screen |

## Run database migrations

The `app` container doesn't run migrations automatically on startup. After the stack is up, run:

```bash
docker compose -f deploy/docker-compose.yml exec app alembic upgrade head
```

## Using n8n

The three workflow files under `n8n/` (`01-trigger-ingestion.json`, `02-approval-and-send.json`,
`03-alerting.json`) are hand-authored and ready to import, but nothing has been imported or
credentialed yet — that's a manual step:

1. Open `http://localhost:5678` and complete n8n's first-run owner setup.
2. Import each workflow file (**Workflows → Import from File**).
3. Add the credentials each workflow's `meta.notes` field calls for: an `httpHeaderAuth` credential
   (header `X-API-Key`, value = your `.env`'s `API_KEY`) for calling the app, plus Slack and Gmail
   credentials for the approval/send/alerting workflows.
4. Set `N8N_ALERT_WEBHOOK_URL` in `.env` to workflow 03's webhook URL once it's imported and
   activated, then restart the `app` container so it picks up the change.
5. Activate each workflow once its credentials are configured.

## Tearing down

```bash
docker compose -f deploy/docker-compose.yml down        # stop containers, keep volumes (data)
docker compose -f deploy/docker-compose.yml down -v      # also delete volumes (fresh start)
```

## Production notes (not built, documented for later)

This docker-compose setup is for local/demo use. A real production deployment would move to:

- **Kubernetes / minikube** instead of docker-compose, for real scaling, rolling deploys, and
  resource limits.
- **Supabase** (or another managed Postgres provider) instead of the `db` container, so
  backups/failover/patching aren't self-managed.

Neither is built in this repo yet — noted here as the documented next step, not a gap in what
exists today.
