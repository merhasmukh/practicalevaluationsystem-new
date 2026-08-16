# TPEMS

Transparent Practical Evaluation & Monitoring System for the Department of Computer Science, Gujarat Vidyapith.

## What is included

- Institutional Google Workspace Sign-In only (`@gujaratvidyapith.org`), eliminating email/password authentication risks.
- Automatic Administrator detection via `ADMIN_EMAILS` configuration in `.env` or `secrets.toml`.
- First-time student onboarding workflow (Programme & Semester selection linked to institutional enrollment ID).
- Auto-provisioned Faculty accounts upon first Google sign-in.
- SQLAlchemy 2 ORM with MySQL 8 production connection pooling and SQLite development fallback.
- Practical creation, bulk assignment, deadline tracking, GitHub URL submission, duplicate-safe editing, evaluation, automatic grade calculation, and audit logs.
- Plotly dashboard, marks export to Excel/PDF, and automated test suite.

## Configuration & Quick Start

1. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` (or configure `.env`):
   ```toml
   # Google OAuth 2.0
   GOOGLE_CLIENT_ID     = "your-client-id.apps.googleusercontent.com"
   GOOGLE_CLIENT_SECRET = "your-client-secret"
   GOOGLE_REDIRECT_URI  = "http://localhost:8501"
   GOOGLE_HOSTED_DOMAIN = "gujaratvidyapith.org"

   # Administrator emails
   ADMIN_EMAILS = "admin@gujaratvidyapith.org,hod.cs@gujaratvidyapith.org"

   # MySQL Database (or leave empty for SQLite dev fallback)
   DATABASE_URL = "mysql+pymysql://tpems_user:password@localhost:3306/tpems"
   ```

2. Run the application:
   ```bash
   streamlit run app.py
   ```

3. Sign in via **Sign in with Google** using an authorized `@gujaratvidyapith.org` account. Any email specified in `ADMIN_EMAILS` automatically receives Administrator access.

## Production checklist

Use a secrets manager for `SECRET_KEY`, Google OAuth secrets, and the MySQL database URL. Put Streamlit behind HTTPS and an Nginx reverse proxy with WebSocket support (`proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade;`), use Alembic migrations instead of `create_all`, and back up MySQL daily. See [docs/deployment.md](docs/deployment.md) and [docs/security.md](docs/security.md).

