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

        if not admin_emails:
            print("Notice: No ADMIN_EMAILS configured in environment/secrets. Admins will be auto-detected on Google login once configured.")
        else:
            for email in admin_emails:
                user = db.query(User).filter(User.email.ilike(email)).first()
                if not user:
                    user = User(
                        username=email,
                        full_name=f"Admin ({email.split('@')[0]})",
                        email=email.lower(),
                        password_hash=hash_password(secrets.token_urlsafe(16)),
                        role=admin_role,
                        is_active=True,
                    )
                    db.add(user)
                    print(f"Administrator account pre-provisioned: {email}")
                else:
                    user.role = admin_role
                    user.is_active = True
                    db.add(user)
                    print(f"Administrator role ensured for existing user: {email}")
            db.commit()


if __name__ == "__main__":
    try:
        seed()
    except Exception as e:
        print(f"Seed error: {e}", file=sys.stderr)
        sys.exit(1)

