# Self-hosted Langfuse

Langfuse is the observability and evaluation service for the UI/UX Agent Company.
It is **not** the company control room. It stores traces, spans, token/cost data,
scores, datasets, and experiments. Runs, approvals, gates, artifacts, Target_ui,
worktrees, rollback, and company history stay in our local dashboard/database.

This stack follows the official Langfuse Docker Compose deployment:
<https://langfuse.com/self-hosting/deployment/docker-compose>

## Components

| Service | Purpose | Port |
|---|---|---|
| `langfuse-web` | UI and API | `3000` |
| `langfuse-worker` | ingestion / background jobs | `3030` (localhost only) |
| `postgres` | transactional data | `5432` (localhost only) |
| `clickhouse` | traces and analytics | `8123`, `9000` (localhost only) |
| `redis` | queues and cache | `6379` (localhost only) |
| `minio` | event/media object storage | `9090`, console `9091` |

Docker Compose is appropriate for a local machine or a single VM. It is **not**
high availability and has no built-in backup or scaling. For production, use
Kubernetes/Helm or a VM with managed Postgres/ClickHouse/S3 and tested backups.


## Company dashboard integration

Langfuse runs from the official prebuilt image. The company control room calls
the Langfuse read API from FastAPI and shows compact trace, session, score,
dataset, and experiment summaries with deep links into Langfuse. No custom
Langfuse image is built.

The company dashboard uses the Langfuse visual language: OLED black, white text,
green icons/active states, and Langfuse-style tables, cards, and tabs.

## First install

```bash
cd infra/langfuse
cp .env.example .env

# Replace every CHANGEME in .env.
openssl rand -hex 32   # use for NEXTAUTH_SECRET, SALT, ENCRYPTION_KEY
openssl rand -hex 24   # use for database/minio/redis passwords

docker compose up -d
docker compose ps
```

Wait until all services are healthy, then open <http://localhost:3000>.

The `.env.example` includes optional `LANGFUSE_INIT_*` variables. When set, a
fresh database is provisioned with:

- organization `UIUX Agent Company`
- project `UIUX Agent Company`
- project id `uiux-company-project`
- public key `pk-lf-uiux-local`
- secret key from `LANGFUSE_INIT_PROJECT_SECRET_KEY`
- a Dictator login from `LANGFUSE_INIT_USER_*`

If you prefer the interactive path, remove the `LANGFUSE_INIT_*` lines, start
the stack, sign up the first user, create an organization and project, then
create API keys in **Project Settings → API Keys**.

## Connect the company

Put the same project keys in the company `.env` at the repository root:

```bash
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-uiux-local
LANGFUSE_SECRET_KEY=sk-lf-uiux-local-...
LANGFUSE_BASE_URL=http://127.0.0.1:3000
LANGFUSE_TRACING_ENVIRONMENT=local
LANGFUSE_DATASET=uiux-golden-tasks
LANGFUSE_PROJECT_ID=uiux-company-project
```

Restart the company server. In the dashboard open **Improvement → Observability**
and press **Check connection**. A healthy stack shows `connected`.

Deep links use `LANGFUSE_PROJECT_ID`. If it is missing, the company tries to
resolve the project id from Langfuse and otherwise shows only the Langfuse base
URL.

## What is sent

- one trace per company run (`session_id = run_id`)
- agent spans for every model call
- `code_worker`, `intent_packet`, gate, rescan, and rollback spans
- scores: critic score/pass, release pass, intent coverage, visual QA, rework,
  duration, target fidelity when reported
- golden-task datasets and three-lane experiments
- experiment evaluators and aggregate run scores

## Failure behavior

Langfuse is non-blocking by design:

- disabled or missing keys → tracing is a no-op
- Langfuse unreachable → the company run continues and exports are dropped
- export/score/experiment failure → local SQLite results remain authoritative
- evaluation experiments always finish locally even when Langfuse sync fails

## Operations

```bash
# logs
docker compose logs -f langfuse-web langfuse-worker

# stop (keeps data)
docker compose stop

# start again
docker compose start

# upgrade
docker compose pull
docker compose up -d

# stop and remove containers (volumes survive)
docker compose down
```

Back up at minimum the Postgres and ClickHouse volumes, plus the MinIO bucket if
you keep media. Test a restore on a separate host before relying on it.

## Security / offline notes

- Keep Postgres, ClickHouse, Redis, and MinIO bound to localhost or a private
  network. Only expose `langfuse-web` (and MinIO if needed for media) to users.
- Use a reverse proxy with TLS for anything beyond a trusted local machine.
- Docker images must be pulled once; after that the stack runs without internet.
- Langfuse core is open source. Some optional add-on features require an EE
  license key; the tracing, datasets, experiments, and scores used here do not.
- Offline Langfuse does not make the model provider offline. OpenCode/DeepSeek
  still needs network access unless you configure a local OpenAI-compatible
  model endpoint.
