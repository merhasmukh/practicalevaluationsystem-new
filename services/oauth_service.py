import urllib.parse
import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from core.config import settings
from models.schema import User
from services.auth_service import record_login, reset_failed_attempts, utc_now

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"


def is_google_auth_configured() -> bool:
    """Return True if Google OAuth client ID and secret are configured."""
    return bool(settings.google_client_id and settings.google_client_secret)


def get_google_auth_url(state: str | None = None) -> str:
    """Generate Google OAuth 2.0 authorization URL."""
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    if settings.google_hosted_domain:
        params["hd"] = settings.google_hosted_domain
    if state:
        params["state"] = state
    return f"{GOOGLE_AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"


def exchange_code_for_user_info(code: str, redirect_uri: str | None = None) -> tuple[dict | None, str | None]:
    """Exchange authorization code for Google user profile information.
    
    Returns (user_info_dict, error_message).
    """
    if not is_google_auth_configured():
        return None, "Google OAuth is not configured. Please set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
    
    redirect = redirect_uri or settings.google_redirect_uri
    payload = {
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect,
    }
    try:
        response = requests.post(GOOGLE_TOKEN_ENDPOINT, data=payload, timeout=10)
        if response.status_code != 200:
            try:
                err_data = response.json()
                err_desc = err_data.get("error_description") or err_data.get("error") or response.text
            except Exception:
                err_desc = response.text
            return None, f"Google OAuth Error ({response.status_code}): {err_desc}"
        token_data = response.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return None, "Google returned a success response but no access token was found."

        userinfo_resp = requests.get(
            GOOGLE_USERINFO_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if userinfo_resp.status_code != 200:
            return None, f"Failed to fetch Google profile info ({userinfo_resp.status_code}): {userinfo_resp.text}"
        return userinfo_resp.json(), None
    except Exception as e:
        return None, f"Connection error contacting Google OAuth: {str(e)}"


import re
import secrets
from models.schema import Program, Student, User
from services.auth_service import ensure_role, hash_password, record_login, reset_failed_attempts, utc_now

STUDENT_EMAIL_RE = re.compile(r"^(\d{9}|\d{12})(?:\.gvp)?@gujaratvidyapith\.org$", re.IGNORECASE)


def parse_student_enrollment_from_email(email: str) -> str | None:
    """Extract a 9 or 12 digit enrollment number from a student institutional email if present."""
    if not email:
        return None
    m = STUDENT_EMAIL_RE.match(email.strip())
    if m:
        return m.group(1)
    # Also check if prefix before @ or .gvp is exactly 9 or 12 digits
    prefix = email.strip().split("@")[0].split(".gvp")[0]
    if re.match(r"^(\d{9}|\d{12})$", prefix):
        return prefix
    return None


def authenticate_google_user(db: Session, google_info: dict, ip: str | None = None) -> tuple[User | None, str | None]:
    """Authenticate or verify a user from Google profile info.
    
    If the email is listed in settings.admin_emails, automatically assigns Administrator role.
    If an existing user is found, logs them in.
    If a student signs in for the first time, returns ('FIRST_TIME_STUDENT_SETUP')
    so the frontend can prompt for Department, Programme, and Semester selection.
    If a faculty signs in for the first time, returns ('FIRST_TIME_FACULTY_SETUP').
    """
    email = (google_info.get("email") or "").strip()
    if not email:
        return None, "Google account does not provide an email address."

    # Domain restriction check if configured
    if settings.google_hosted_domain:
        required_suffix = f"@{settings.google_hosted_domain.lower()}"
        if not email.lower().endswith(required_suffix):
            return None, f"Only @{settings.google_hosted_domain} institutional accounts are permitted to sign in."

    # Administrator detection via configured admin_emails list
    if settings.is_admin_email(email):
        admin_role = ensure_role(db, "Administrator")
        user = db.scalar(
            select(User).where(
                (func.lower(User.email) == email.lower())
                | (func.lower(User.username) == email.lower())
            )
        )
        google_name = (google_info.get("name") or email.split("@")[0]).strip()
        if not user:
            user = User(
                username=email,
                full_name=google_name or "System Administrator",
                email=email.lower(),
                password_hash=hash_password(secrets.token_urlsafe(16)),
                role_id=admin_role.id,
                is_active=True,
                account_locked=False,
            )
            db.add(user)
            db.flush()
        else:
            user.role_id = admin_role.id
            user.is_active = True
            user.account_locked = False
            if google_name and (not user.full_name or user.full_name == user.username):
                user.full_name = google_name

        reset_failed_attempts(db, user)
        user.last_login = utc_now()
        db.add(user)
        record_login(db, user, user.username, "Administrator", "success:google-oauth-admin", ip)
        db.commit()
        return user, None

    user = db.scalar(select(User).where((func.lower(User.email) == email.lower()) | (func.lower(User.username) == email.lower())))
    
    if not user:
        enrollment_no = parse_student_enrollment_from_email(email)
        if enrollment_no or ".gvp@" in email.lower():
            # Student email detected — trigger first-time student onboarding
            return None, "FIRST_TIME_STUDENT_SETUP"
        # Non-student institutional domain email → auto-register as Faculty
        # (e.g. hasmukh.cs@gujaratvidyapith.org, bhavin.patel@gujaratvidyapith.org)
        return None, "FIRST_TIME_FACULTY_SETUP"

    if not user.is_active:
        record_login(db, user, email, user.role.name if user.role else None, "failed:account-inactive", ip)
        db.commit()
        return None, "Your account has been deactivated. Please contact the administrator."

    if user.account_locked:
        record_login(db, user, email, user.role.name if user.role else None, "failed:account-locked", ip)
        db.commit()
        return None, "Your account is locked due to too many failed attempts. Please contact the administrator."

    # If user exists but is a student missing a student profile
    if user.role and user.role.name == "Student" and not user.student:
        return None, "NEEDS_STUDENT_PROFILE"

    # If user has default username as full_name or empty, update with Google full name
    google_name = (google_info.get("name") or "").strip()
    if google_name and (not user.full_name or user.full_name == user.username):
        user.full_name = google_name

    # Successful login
    reset_failed_attempts(db, user)
    user.last_login = utc_now()
    db.add(user)
    record_login(db, user, user.username, user.role.name if user.role else None, "success:google-oauth", ip)
    db.commit()
    return user, None


def register_google_student(
    db: Session,
    google_info: dict,
    program_id: int,
    semester: int,
    full_name: str | None = None,
    ip: str | None = None,
) -> tuple[User | None, str | None]:
    """Create or complete a Student account during first-time Google sign-in."""
    email = (google_info.get("email") or "").strip()
    if not email:
        return None, "Google account does not provide an email address."

    enrollment_no = parse_student_enrollment_from_email(email) or email.split("@")[0].split(".gvp")[0]
    if not enrollment_no:
        return None, "Unable to determine enrollment number from email address."

    program = db.get(Program, program_id)
    if not program:
        return None, "Selected Programme does not exist."

    if semester < 1 or semester > program.total_semesters:
        return None, f"Semester must be between 1 and {program.total_semesters} for {program.code}."

    student_role = ensure_role(db, "Student")
    name = (full_name or google_info.get("name") or enrollment_no).strip()

    user = db.scalar(select(User).where((func.lower(User.email) == email.lower()) | (func.lower(User.username) == enrollment_no.lower())))
    if not user:
        user = User(
            username=enrollment_no,
            full_name=name,
            email=email.lower(),
            password_hash=hash_password(secrets.token_urlsafe(16)),
            role_id=student_role.id,
            is_active=True,
        )
        db.add(user)
        db.flush()
    else:
        user.full_name = name
        user.role_id = student_role.id
        user.is_active = True

    # Link or update Student profile
    student = db.scalar(select(Student).where(Student.user_id == user.id))
    if not student:
        # Check if enrollment_no already exists on another student
        existing_enrollment = db.scalar(select(Student).where(Student.enrollment_no == enrollment_no))
        if existing_enrollment and existing_enrollment.user_id != user.id:
            return None, f"Enrollment number '{enrollment_no}' is already linked to another account."

        student = Student(
            user_id=user.id,
            enrollment_no=enrollment_no,
            semester=semester,
            program=program.code,
            program_id=program.id,
        )
        db.add(student)
    else:
        student.semester = semester
        student.program = program.code
        student.program_id = program.id

    db.flush()

    # Record login and commit
    reset_failed_attempts(db, user)
    user.last_login = utc_now()
    db.add(user)
    record_login(db, user, user.username, "Student", "success:google-oauth-onboarding", ip)
    db.commit()
    return user, None


def register_google_faculty(
    db: Session,
    google_info: dict,
    ip: str | None = None,
) -> tuple[User | None, str | None]:
    """Auto-create a Faculty account on first Google sign-in with a non-student institutional email.

    The account is created immediately — no manual admin step required.
    Faculty still need subjects assigned by the admin before they can manage practicals.
    """
    email = (google_info.get("email") or "").strip()
    if not email:
        return None, "Google account does not provide an email address."

    # Safety guard: this function must never be called for student emails
    if parse_student_enrollment_from_email(email) or ".gvp@" in email.lower():
        return None, "Student emails must go through the student onboarding flow."

    # Check if user already exists (should not happen, but be safe)
    existing = db.scalar(select(User).where(func.lower(User.email) == email.lower()))
    if existing:
        # Already exists — just log them in
        reset_failed_attempts(db, existing)
        existing.last_login = utc_now()
        db.add(existing)
        record_login(db, existing, existing.username, existing.role.name if existing.role else None, "success:google-oauth", ip)
        db.commit()
        return existing, None

    faculty_role = ensure_role(db, "Faculty")
    google_name = (google_info.get("name") or email.split("@")[0]).strip()

    user = User(
        username=email,
        full_name=google_name,
        email=email.lower(),
        password_hash=hash_password(secrets.token_urlsafe(16)),
        role_id=faculty_role.id,
        is_active=True,
    )
    db.add(user)
    db.flush()

    reset_failed_attempts(db, user)
    user.last_login = utc_now()
    db.add(user)
    record_login(db, user, user.username, "Faculty", "success:google-oauth-faculty-auto-register", ip)
    db.commit()
    return user, None