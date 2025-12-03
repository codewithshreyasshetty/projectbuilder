# System Design — AI-Powered Application Builder

## Architecture Overview

- Frontend: Streamlit application (single codebase) for UI where users can:
  - Authenticate (email/password)
  - Provide natural-language prompts
  - View, edit, run, export generated projects
- Backend: SQLite for persistence (lightweight, file-based) within the Streamlit app process.
- AI: Groq client integration inside `generate_project_code` (requires API key).
- Runtime: Generated projects are written to `./projects/project_<id>` and run as local Flask apps.
- DevOps: Dockerfile generated per project and CI via GitHub Actions to run tests and build images.

## Database Schema

- `users` (id, email, password_hash, created_at)
- `projects` (id, name, description, prompt, backend_code, frontend_code, created_at, last_modified, status, port, framework, owner_id)
- `project_runs` (id, project_id, pid, port, started_at, stopped_at, status)
- `generation_history` (id, project_id, prompt, response, tokens_used, created_at)

## Sequence Flow

1. User signs up / signs in.
2. User enters a prompt and clicks Generate.
3. Streamlit app calls `generate_project_code` which forwards the prompt to Groq (or another AI provider).
4. Response is parsed; backend + frontend code is extracted and presented to the user.
5. User saves the project → saved into `projects` table with `owner_id`.
6. User runs the project → files written to `projects/project_<id>`; Dockerfile generated; Flask process launched.
7. Project runs locally on an available port and is recorded in `project_runs`.

## CI/CD Pipeline Design

- GitHub Actions workflow (`.github/workflows/ci.yml`):
  - Install dependencies
  - Run `pytest`
  - Build Docker image and push to a registry (requires secrets)
  - Optional: Add a deploy job (Render/Fly/Railway) that will use the pushed image to deploy.

## Infra / DevOps Choices

- SQLite for starter assignment due to minimal setup and portability.
- Docker images built using official `python:3.12-slim` base for small size.
- GitHub Actions for CI due to native GitHub integration.
- Recommendations for production:
  - Use PostgreSQL (or managed DB) with proper migrations
  - Store secrets in a secrets manager
  - Run AI calls through a backend service with rate-limiting and retries
  - Add observability (logs, metrics)

