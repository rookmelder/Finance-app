# System: Deployment
> Container packaging, environment configuration, local startup scripts, and runtime entrypoint.

Back to [Systems](systems.md).

## What it does
Runs the Finance application locally or inside a Docker container.
Packages Python dependencies and application code into a container image.
Mounts a persistent volume for the SQLite database and loads environment secrets from `.env`.
Starts the server on host 0.0.0.0 and port 8080 via the main entrypoint.

## Files
- `Dockerfile`: container specification using `python:3.12-slim`, installs `requirements.txt`, copies `src`, sets `FINANCE_DB=/data/finance.db`, runs as non-root user `app` (UID 1000), exposes port 8080, declares `/data` volume, and runs `python -m src.app`.
- `docker-compose.yml`: defines the `finance` service, maps port `8080:8080`, sets `restart: unless-stopped`, loads environment from `.env`, and attaches named volume `finance-data` to `/data`.
- `run.sh`: local startup script that exports environment variables from `.env` and executes `python3 -m src.app`.
- `requirements.txt`: Python dependencies (`Flask>=3.0,<4`).
- `.dockerignore`: excludes `.git`, `.env`, `.worker`, `data`, `tests`, `agent`, `knowledge`, `templates`, `.venv`, and `__pycache__` from the Docker build context.
- `src/app.py`: contains `if __name__ == "__main__":` entrypoint calling `config.load()`, `create_app`, and `app.run(host=cfg["HOST"], port=cfg["PORT"], debug=False)`.
- `tests/test_auth.py`: tests `config.load` configuration validation in `ConfigTests`; no automated tests exist for Docker or shell deployment scripts.

## How it connects
- Uses: [system-auth-config.md](system-auth-config.md), [system-web-app.md](system-web-app.md), [system-database.md](system-database.md)
- Used by: operators and developers running or deploying the service.
- Data: reads configuration and secrets from `.env` (`FINANCE_PASSWORD`, `SECRET_KEY`, `FINANCE_PORT`, `FINANCE_DB`, `FINANCE_HOST`, and optional `GOOGLE_*` keys). Persists SQLite data to `/data/finance.db` in Docker via named volume `finance-data` (or `data/finance.db` locally). Serves HTTP traffic on TCP port 8080.

## Why it is built this way
- `Dockerfile` copies `src` directly with `COPY src ./src`. Because files are copied into the image rather than bind-mounted, code changes require rebuilding the image using `docker compose up -d --build`.
- Named volume `finance-data` mounts to `/data` so SQLite database files persist across container rebuilds and restarts.
- Container runs under a dedicated non-root user `app` (`uid 1000`) who owns `/data` for least-privilege security.
- `PYTHONDONTWRITEBYTECODE=1` and `PYTHONUNBUFFERED=1` prevent `.pyc` file generation and ensure container logs are emitted immediately to standard output.
- `run.sh` uses `set -a; . ./.env; set +a` so developers can run the app locally with the same `.env` file used by `docker compose`.
- `.dockerignore` excludes secrets (`.env`) and local data (`data`) from being baked into the Docker image.

## Known issues
- Running `docker compose up -d` without `--build` after editing code will keep running the old image code.
- `src/app.py` runs Flask's built-in development server with `app.run` instead of a production WSGI server.
