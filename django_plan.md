# TPEMS: Streamlit to Django Migration Implementation Plan

## Executive Overview
This implementation plan outlines the side-by-side migration of **TPEMS** (Transparent Practical Evaluation & Monitoring System) from Streamlit to **Django 4.2 LTS**, using the existing SQLite database (`tpems.db`). The architecture preserves existing data, business logic, and security rules while introducing dynamic HTMX-driven interactions and enhanced AI code evaluation for multi-file GitHub repositories.

---

## 1. Architectural Blueprint & Target Structure

```
practicalevaluationsystem-new/
├── tpems.db                   # Existing shared SQLite database
├── tpems_django/              # Django Project Root
│   ├── manage.py
│   ├── tpems_project/         # Project Settings & URLs
│   │   ├── __init__.py
│   │   ├── settings.py        # Configured for tpems.db, Auth, Static, HTMX
│   │   ├── urls.py            # Global URL router
│   │   ├── wsgi.py
│   │   └── asgi.py
│   ├── apps/
│   │   ├── core/              # Shared context, middleware, base templates, audit logs
│   │   ├── accounts/          # Custom User model, Google OAuth, bcrypt auth backend
│   │   ├── academics/         # Departments, Programs, Subjects, Practicals, Assignments
│   │   ├── evaluations/       # Submissions, Evaluations, HTMX AI Review workflow
│   │   └── dashboard/         # Role-based analytics (Admin, Faculty, Student)
│   ├── static/                # University CSS design system, logos, HTMX.js
│   └── templates/             # Jinja-style Django templates
│       ├── base.html
│       ├── components/        # HTMX partials (modals, AI spinner, grade forms)
│       ├── accounts/
│       ├── academics/
│       ├── evaluations/
│       └── dashboard/
├── services/                  # Shared Business Logic & AI Services (reused by both)
│   ├── ai_service.py          # UPGRADED: Multi-file GitHub repo scanner + Sarvam AI
│   ├── oauth_service.py
│   └── email_service.py
├── app.py                     # Streamlit entry point (kept intact for coexistence)
└── ui/                        # Streamlit UI modules (kept intact for coexistence)
```

---

## 2. Phase-by-Phase Implementation Roadmap

### Phase 1: Database Mapping & Custom Authentication
- **Objective:** Connect Django to `tpems.db` without altering existing tables, preserving all rows and relations.
- **Key Tasks:**
  1. Initialize Django project structure inside `tpems_django/`.
  2. Configure `DATABASES['default']` to point to `BASE_DIR.parent / 'tpems.db'`.
  3. Define custom `User` model inheriting from `AbstractBaseUser` with `Meta.db_table = "users"`.
  4. Implement custom `BcryptAuthenticationBackend` to seamlessly verify existing passwords hashed with `$2b$`.
  5. Define Django ORM models matching existing tables (`departments`, `programs`, `subjects`, `practicals`, `assignments`, `submissions`, `evaluations`, `audit_logs`, `permissions`, `role_permissions`) with `managed = False` or explicit `db_table` mappings so existing database schemas remain untouched.
  6. Verify queries match current SQLAlchemy outputs.

### Phase 2: AI Engine Upgrade (Multi-File & Full Repo Analysis)
- **Objective:** Upgrade `services/ai_service.py` to ingest full GitHub repositories and multi-file submissions.
- **Key Tasks:**
  1. Enhance `services/ai_service.py`:
     - Add repository parsing to support both single-file URLs (`.../blob/main/file.py`) and root repo URLs (`https://github.com/user/repo`).
     - Utilize GitHub REST API tree fetching (`/repos/{owner}/{repo}/git/trees/{branch}?recursive=1`) or zip archive extraction.
     - Implement code file filter (`.py`, `.java`, `.c`, `.cpp`, `.js`, `.ts`, `.html`, `.css`, `.sql`, etc.), skipping binary assets, build artifacts (`.class`, `.o`, `__pycache__`), and dependencies (`node_modules`, `venv`).
     - Concatenate files with clear hierarchical path demarcations, truncating intelligently to fit within token context limits.
  2. Enhance Sarvam AI prompt engineering:
     - Multi-file architectural analysis.
     - Structured viva question generation with model answers for faculty.
     - Actionable suggestions for code structure, modularity, and error handling.
  3. Create an HTMX-compatible endpoint (`/evaluations/submission/<id>/ai-analyze/`) returning partial HTML containing remarks, suggestions, and viva questions.

### Phase 3: Core Views & HTMX UI Implementation
- **Objective:** Recreate and polish all Streamlit interfaces as server-rendered Django templates with dynamic HTMX components.
- **Key Tasks:**
  1. **Base Layout & Theme:**
     - University header, responsive sidebar, role indicator, and sign-out button.
     - Clean styling honoring the Gujarat Vidyapith brand colors (Navy `#1E3A8A`, Gold `#D97706`, Slate `#0F172A`).
     - Embed HTMX for reactive interactions without full-page reloads.
  2. **Authentication Flow (`apps/accounts/`):**
     - Institutional Google OAuth 2.0 flow with `@gujaratvidyapith.org` domain enforcement.
     - Standard credentials fallback for administrators.
     - First-time student onboarding workflow (Enrollment ID & Program linking).
  3. **Role-Based Workspaces:**
     - **Administrator:** Master data CRUD for Departments, Programs, Subjects, Faculty assignment, Users & Permissions.
     - **Faculty:** Subject list, practical creation form, student submissions list, and interactive evaluation panel with one-click AI code analysis.
     - **Student:** Registered subjects, assigned practicals, submission form (GitHub repo URL + branch + remarks), and published grade view.
     - **Analytics Dashboard:** Performance metrics, submission rates, and grade distribution charts.

### Phase 4: Side-by-Side Coexistence & Verification
- **Objective:** Ensure both Streamlit (`streamlit run app.py`) and Django (`python manage.py runserver`) can operate simultaneously against `tpems.db`.
- **Key Tasks:**
  1. Run concurrent write/read tests across both systems.
  2. Ensure transaction safety and SQLite concurrency settings (WAL mode: `PRAGMA journal_mode=WAL;`).
  3. End-to-end user workflow testing across Admin, Faculty, and Student roles.
  4. Document migration completion and production deployment instructions.

---

## 3. Detailed Technical Specifications

### A. Database Concurrency Configuration (SQLite WAL Mode)
Because both Streamlit and Django will query `tpems.db`, SQLite must run in Write-Ahead Logging (WAL) mode to allow concurrent reads during writes:
```sql
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
```

### B. Custom Auth Backend Specification
In `apps/accounts/backends.py`:
```python
import bcrypt
from django.contrib.auth.backends import BaseBackend
from .models import User

class TPEMSAuthBackend(BaseBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        try:
            user = User.objects.filter(username=username).first() or User.objects.filter(email=username).first()
            if not user or not user.is_active or user.account_locked:
                return None
            if bcrypt.checkpw(password.encode(), user.password_hash.encode()):
                return user
        except Exception:
            return None
        return None
```

### C. Multi-File GitHub AI Ingestion Pipeline
In `services/ai_service.py`:
```python
def fetch_github_repository_code(github_url: str, max_files: int = 15, max_chars_per_file: int = 4000) -> str:
    """Fetch and aggregate code files from a GitHub repository or folder."""
    # 1. Parse owner, repo, branch, subpath
    # 2. Query GitHub Git Trees API (recursive=1)
    # 3. Filter relevant programming source extensions
    # 4. Fetch raw content and assemble into a formatted code dossier for Sarvam AI
```

---

## 4. Acceptance Criteria & Verification Matrix

| Area | Success Metric | Verification Method |
| :--- | :--- | :--- |
| **Data Preservation** | Zero data loss or table truncation | Compare row counts & checksums in `tpems.db` before and after Django setup |
| **Authentication** | Existing passwords and Google OAuth both work | Log in with existing seed accounts + test Google Workspace login |
| **AI Evaluation** | Full GitHub repo links analyzed successfully | Test submission with a multi-file Java/Python repo URL; verify remarks, suggestions, and viva questions |
| **HTMX Interactivity** | AI analysis runs asynchronously without full page refresh | Click "Analyze Code with AI", observe spinner, verify form pre-populates |
| **Coexistence** | Streamlit continues to function normally | Run `streamlit run app.py` on port 8501 and Django on port 8000 simultaneously |

---

## 5. Next Steps
Upon user approval, we will proceed immediately to **Phase 1: Project Setup & Database Mapping**.
