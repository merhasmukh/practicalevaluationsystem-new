"""Seed script — safe to commit to a public repository.

Initializes RBAC roles, core permissions, default academic master data,
and pre-provisions Administrator accounts from ADMIN_EMAILS.

Configuration
-------------
In .env or .streamlit/secrets.toml:
    ADMIN_EMAILS="admin@gujaratvidyapith.org,hod.cs@gujaratvidyapith.org"
"""
import sys
import secrets
from core.config import settings
from core.database import SessionLocal, init_db
from services.core_services import ensure_role, ensure_permission, grant_role_permission
from services.auth_service import hash_password
from models.schema import Department, Program, Role, User

LOGIN_ROLES = ["Administrator", "Faculty", "Student", "External Examiner", "Coordinator"]

# permission_code → (description, list of role names that should receive it)
CORE_PERMISSIONS = {
    "admin.access":   ("Access administrator workspace and management features", ["Administrator"]),
    "faculty.access": ("Access the faculty workspace and manage assigned subjects", ["Administrator", "Faculty"]),
    "student.access": ("Access the student practicals and dashboard views", ["Administrator", "Student"]),
}


def seed() -> None:
    init_db()
    with SessionLocal() as db:
        # 1. Seed RBAC roles (idempotent)
        roles = {name: ensure_role(db, name) for name in LOGIN_ROLES}

        # 2. Seed RBAC permissions and grant to roles (idempotent)
        for code, (description, role_names) in CORE_PERMISSIONS.items():
            perm = ensure_permission(db, code, description)
            for role_name in role_names:
                grant_role_permission(db, roles[role_name], perm)
        db.commit()
        print("Permissions synced:", ", ".join(CORE_PERMISSIONS))

        # 3. Seed default base Department & Program if not existing
        department = db.query(Department).filter_by(code="CS").first()
        if not department:
            department = Department(name="Department of Computer Science", code="CS")
            db.add(department)
            db.flush()

        program = db.query(Program).filter_by(code="MCA").first()
        if not program:
            program = Program(
                code="MCA",
                name="Master of Computer Applications",
                duration_months=24,
                total_semesters=4,
                department=department,
            )
            db.add(program)
            db.flush()
        db.commit()

        # 4. Provision Administrators from settings.admin_emails
        admin_role = roles["Administrator"]
        admin_emails = list(settings.admin_emails)

        # Resolve admin password — must be set in ADMIN_PASSWORD env variable.
        # Falls back to a random secret if unconfigured (unusable for local login).
        admin_pw = settings.admin_password.strip() if settings.admin_password else ""
        if not admin_pw:
            print(
                "Warning: ADMIN_PASSWORD is not set in .env/secrets.toml. "
                "Admin accounts will have a random unknown password and cannot log in locally. "
                "Add ADMIN_PASSWORD=<your-password> to env.dev or .env."
            )
            admin_pw = secrets.token_urlsafe(16)  # random fallback — account unusable without Google

        if not admin_emails:
            print("Notice: No ADMIN_EMAILS configured. Add ADMIN_EMAILS to env.dev.")
        else:
            for email in admin_emails:
                user = db.query(User).filter(User.email.ilike(email)).first()
                if not user:
                    user = User(
                        username=email,
                        full_name=f"Admin ({email.split('@')[0]})",
                        email=email.lower(),
                        password_hash=hash_password(admin_pw),
                        role=admin_role,
                        is_active=True,
                    )
                    db.add(user)
                    print(f"Administrator account provisioned: {email}")
                else:
                    # Always sync role, active state, AND password so re-running seed
                    # applies any ADMIN_PASSWORD change from the env file.
                    user.role = admin_role
                    user.is_active = True
                    user.password_hash = hash_password(admin_pw)
                    db.add(user)
                    print(f"Administrator account updated: {email}")
            db.commit()



if __name__ == "__main__":
    try:
        seed()
    except Exception as e:
        print(f"Seed error: {e}", file=sys.stderr)
        sys.exit(1)

