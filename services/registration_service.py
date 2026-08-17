"""Student self-registration service.

Handles new student account creation via the local registration form
(not Google OAuth). Validates the GVP institutional email, extracts
the enrollment number, and creates User + Student records.
"""
import re
import secrets
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.schema import Program, Student, User
from services.auth_service import ensure_role, hash_password, record_login, reset_failed_attempts, utc_now

# Same regex used in oauth_service — 9 or 12 digit enrollment prefix
STUDENT_EMAIL_RE = re.compile(
    r"^(\d{9}|\d{12})(?:\.gvp)?@gujaratvidyapith\.org$", re.IGNORECASE
)

MIN_PASSWORD_LENGTH = 6


def parse_enrollment_from_email(email: str) -> str | None:
    """Extract 9 or 12 digit enrollment number from a GVP student email."""
    if not email:
        return None
    m = STUDENT_EMAIL_RE.match(email.strip())
    if m:
        return m.group(1)
    prefix = email.strip().split("@")[0].split(".gvp")[0]
    if re.match(r"^(\d{9}|\d{12})$", prefix):
        return prefix
    return None


def validate_student_email(email: str) -> str | None:
    """Return an error string if the email is not a valid GVP student email, else None."""
    if not email or "@" not in email:
        return "Please enter a valid email address."
    if not email.lower().endswith("@gujaratvidyapith.org"):
        return "Only @gujaratvidyapith.org institutional emails are accepted for student registration."
    if not parse_enrollment_from_email(email):
        return (
            "Your email must begin with a 9-digit or 12-digit enrollment number "
            "(e.g. 202301234@gujaratvidyapith.org)."
        )
    return None


def register_student(
    db: Session,
    email: str,
    password: str,
    confirm_password: str,
    full_name: str,
    program_id: int,
    semester: int,
    ip: str | None = None,
) -> tuple[User | None, str | None]:
    """Create a new Student account + profile from the local registration form.

    Returns (user, None) on success, or (None, error_message) on failure.
    """
    # ── Email validation ──────────────────────────────────────────────────────
    email = (email or "").strip().lower()
    email_err = validate_student_email(email)
    if email_err:
        return None, email_err

    enrollment_no = parse_enrollment_from_email(email)
    if not enrollment_no:
        return None, "Unable to determine enrollment number from your email address."

    # ── Name validation ───────────────────────────────────────────────────────
    full_name = (full_name or "").strip()
    if not full_name:
        return None, "Please enter your full name."

    # ── Password validation ───────────────────────────────────────────────────
    if not password:
        return None, "Please enter a password."
    if len(password) < MIN_PASSWORD_LENGTH:
        return None, f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
    if password != confirm_password:
        return None, "Passwords do not match. Please re-enter."

    # ── Programme / Semester validation ───────────────────────────────────────
    program = db.get(Program, program_id)
    if not program:
        return None, "Selected programme does not exist. Please contact your administrator."
    if semester < 1 or semester > program.total_semesters:
        return None, f"Semester must be between 1 and {program.total_semesters} for {program.code}."

    # ── Duplicate checks ──────────────────────────────────────────────────────
    existing_email = db.scalar(select(User).where(func.lower(User.email) == email))
    if existing_email:
        return None, "An account with this email address already exists. Please sign in instead."

    existing_username = db.scalar(select(User).where(User.username == enrollment_no))
    if existing_username:
        return None, (
            f"An account with enrollment number '{enrollment_no}' already exists. "
            "Please sign in with your enrollment number and password."
        )

    existing_enrollment = db.scalar(select(Student).where(Student.enrollment_no == enrollment_no))
    if existing_enrollment:
        return None, f"Enrollment number '{enrollment_no}' is already registered."

    # ── Create User + Student ─────────────────────────────────────────────────
    student_role = ensure_role(db, "Student")
    user = User(
        username=enrollment_no,
        full_name=full_name,
        email=email,
        password_hash=hash_password(password),
        role_id=student_role.id,
        is_active=True,
        account_locked=False,
    )
    db.add(user)
    db.flush()  # get user.id

    student = Student(
        user_id=user.id,
        enrollment_no=enrollment_no,
        semester=semester,
        program=program.code,
        program_id=program.id,
    )
    db.add(student)
    db.flush()

    # ── Finalise ──────────────────────────────────────────────────────────────
    reset_failed_attempts(db, user)
    user.last_login = utc_now()
    db.add(user)
    record_login(db, user, user.username, "Student", "success:local-registration", ip)
    db.commit()
    return user, None
