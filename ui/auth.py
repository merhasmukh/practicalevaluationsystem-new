import base64
import os
import re
from datetime import datetime, timezone
import streamlit as st
from sqlalchemy import select
from core.session_manager import create_session_token


def _rerun() -> None:
    try:
        st.experimental_rerun()
    except AttributeError:
        try:
            st.rerun()
        except AttributeError:
            pass


@st.cache_data
def _get_logo_base64() -> str:
    logo_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "gujarat-vidyapith-logo.png")
    if os.path.exists(logo_path):
        try:
            with open(logo_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception:
            return ""
    return ""


def _logo_html(width: str = "120px") -> str:
    logo_b64 = _get_logo_base64()
    if not logo_b64:
        return ""
    return (
        f'<img src="data:image/png;base64,{logo_b64}" alt="Gujarat Vidyapith" '
        f'style="width:{width}; max-width:45vw; height:auto; margin:0 auto; display:block;" />'
    )


# ─────────────────────────────────────────────────────────────────────────────
# Login page
# ─────────────────────────────────────────────────────────────────────────────

def render_login() -> None:
    """Render the sign-in page.

    Shows a username/password form as the primary auth method.
    Google Sign-In is shown only when settings.enable_google_login is True
    AND Google OAuth credentials are configured (for future use / public deployments).
    """
    from core.config import settings
    from services.auth_service import authenticate
    from core.session_manager import create_session_token

    col_left, col_center, col_right = st.columns([1, 2.2, 1])
    with col_center:
        # ── Header ────────────────────────────────────────────────────────────
        st.markdown(
            f"""
            <div style='text-align:center; margin-bottom:1.25rem;'>
                <div style='display:flex; justify-content:center; align-items:center; margin-bottom:0.85rem;'>
                    {_logo_html("140px")}
                </div>
                <div style='font-size:1.6rem; font-weight:700; color:var(--ink); line-height:1.25; margin-bottom:0.25rem;'>
                    Gujarat Vidyapith
                </div>
                <div style='font-size:0.95rem; color:var(--muted); font-weight:500; margin-bottom:0.35rem;'>
                    Department of Computer Science
                </div>
                <div style='font-size:1.05rem; font-weight:600; color:var(--accent-strong); line-height:1.35;'>
                    Transparent Practical Evaluation &amp; Management System
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # ── Error messages ────────────────────────────────────────────────────
        if "google_auth_error" in st.session_state:
            st.error(st.session_state.pop("google_auth_error"))
        if "login_error" in st.session_state:
            st.error(st.session_state.pop("login_error"))

        # ── Username / Password form ──────────────────────────────────────────
        with st.form("login_form", clear_on_submit=False):
            username = st.text_input(
                "Email or Enrollment Number",
                placeholder="e.g. 202301234 or faculty@gujaratvidyapith.org",
                key="login_username_input",
            )
            password = st.text_input(
                "Password",
                type="password",
                placeholder="Enter your password",
                key="login_password_input",
            )
            submitted = st.form_submit_button("Sign In", type="primary", width="stretch")

        if submitted:
            if not username.strip() or not password:
                st.error("Please enter both your username/email and password.")
            else:
                # authenticate() is imported here so it runs inside SessionLocal context
                # The db session is passed through via the caller (app.py calls render_login
                # inside `with SessionLocal() as db:` block).
                # Since render_login doesn't receive db directly, we open a short-lived session.
                from core.database import SessionLocal
                with SessionLocal() as _db:
                    user = authenticate(_db, username.strip(), password)
                if user is None:
                    st.error(
                        "Invalid credentials or your account has been locked. "
                        "Please check your username/password and try again."
                    )
                else:
                    st.session_state.user_id = user.id
                    st.session_state.name = user.full_name
                    st.session_state.role = user.role.name
                    st.session_state.email = user.email
                    st.session_state.department = getattr(user, "department", None)
                    st.session_state.login_time = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                    st.query_params["session"] = create_session_token(user.id, user.role.name)
                    st.rerun()

        # ── Register link (students only) ─────────────────────────────────────
        st.markdown(
            "<div style='text-align:center; margin-top:0.6rem; font-size:0.88rem;'>"
            "New student? "
            "</div>",
            unsafe_allow_html=True,
        )
        if st.button("Register here →", key="go_to_register_btn"):
            st.session_state["show_register"] = True
            st.rerun()

        # ── Google Sign-In (hidden on private/local deployments) ──────────────
        # This block is preserved so it can be re-enabled by setting
        # ENABLE_GOOGLE_LOGIN=true in .env or secrets.toml once you have a
        # public redirect URI registered with Google.
        if settings.enable_google_login:
            from services.oauth_service import is_google_auth_configured, get_google_auth_url
            if is_google_auth_configured():
                st.markdown(
                    "<div style='text-align:center; margin:0.75rem 0; color:var(--muted); font-size:0.82rem;'>── or ──</div>",
                    unsafe_allow_html=True,
                )
                google_url = get_google_auth_url()
                st.link_button(
                    "🌐 Sign in with Google",
                    google_url,
                    type="secondary",
                    width="stretch",
                    help="Sign in with your @gujaratvidyapith.org institutional Google account",
                )
                st.markdown(
                    """
                    <div style='text-align:center; margin-top:0.6rem; font-size:0.78rem; color:var(--muted);'>
                        Use your official <code>@gujaratvidyapith.org</code> account
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


# ─────────────────────────────────────────────────────────────────────────────
# Student self-registration page
# ─────────────────────────────────────────────────────────────────────────────

def render_student_register(db) -> None:
    """Render the student self-registration form.

    Students supply their GVP institutional email (enrollment no. is auto-extracted),
    choose their department/programme/semester, and create a password.
    Mirrors the Google onboarding flow for students who use local auth.
    """
    from sqlalchemy import select
    from models.schema import Department, Program
    from services.registration_service import parse_enrollment_from_email, validate_student_email

    col_left, col_center, col_right = st.columns([1, 2.2, 1])
    with col_center:
        # ── Header ────────────────────────────────────────────────────────────
        logo_html = _logo_html("100px")
        if logo_html:
            st.markdown(
                f"<div style='display:flex; justify-content:center; margin-bottom:0.5rem;'>{logo_html}</div>",
                unsafe_allow_html=True,
            )
        st.markdown(
            "<div style='text-align:center; margin-bottom:1rem;'>"
            "<h2 style='margin-top:0;'>🎓 New Student Registration</h2>"
            "<div style='font-size:0.9rem; color:var(--muted);'>"
            "Create your account using your Gujarat Vidyapith institutional email."
            "</div></div>",
            unsafe_allow_html=True,
        )

        # ── Live email preview for enrollment extraction ───────────────────────
        email_key = "reg_email_input"
        email_val = st.session_state.get(email_key, "")
        enrollment_preview = parse_enrollment_from_email(email_val) if email_val else None

        # ── Load departments ──────────────────────────────────────────────────
        departments = list(db.scalars(select(Department).order_by(Department.name)))
        if not departments:
            st.error("No departments have been configured yet. Please contact your administrator.")
            if st.button("Back to Login", key="reg_back_no_dept"):
                st.session_state.pop("show_register", None)
                st.rerun()
            return

        dept_labels = {d.id: f"{d.code} · {d.name}" for d in departments}

        # ── Form ─────────────────────────────────────────────────────────────
        with st.form("student_register_form", clear_on_submit=False):
            email = st.text_input(
                "Institutional Email",
                placeholder="e.g. 202301234@gujaratvidyapith.org",
                key=email_key,
                help="Your Gujarat Vidyapith email must start with your 9 or 12-digit enrollment number.",
            )

            # Enrollment preview (computed from email, shown read-only)
            live_enrollment = parse_enrollment_from_email(email) if email else None
            if live_enrollment:
                st.text_input("Enrollment Number (auto-detected)", value=live_enrollment, disabled=True)
            elif email:
                st.caption("⚠️ Enter a valid GVP student email to auto-detect your enrollment number.")

            full_name = st.text_input("Full Name", placeholder="e.g. Rahul Patel")

            # Department → Programme cascade
            selected_dept_id = st.selectbox(
                "Department",
                list(dept_labels.keys()),
                format_func=lambda x: dept_labels[x],
                key="reg_dept_select",
            )

            programs = list(
                db.scalars(
                    select(Program)
                    .where(Program.department_id == selected_dept_id)
                    .order_by(Program.code)
                )
            )
            if not programs:
                programs = list(db.scalars(select(Program).order_by(Program.code)))

            if not programs:
                st.error("No programmes configured. Please contact your administrator.")
                st.form_submit_button("Create Account", disabled=True)
                return

            prog_labels = {p.id: f"{p.code} · {p.name}" for p in programs}
            selected_prog_id = st.selectbox(
                "Programme",
                list(prog_labels.keys()),
                format_func=lambda x: prog_labels[x],
                key="reg_prog_select",
            )

            sel_prog = db.get(Program, selected_prog_id)
            max_semesters = sel_prog.total_semesters if sel_prog else 8

            semester = st.selectbox(
                "Current Semester",
                options=list(range(1, max_semesters + 1)),
                format_func=lambda s: f"Semester {s}",
                key="reg_semester_select",
            )

            password = st.text_input(
                "Password",
                type="password",
                placeholder="Minimum 6 characters",
                key="reg_password_input",
            )
            confirm_password = st.text_input(
                "Confirm Password",
                type="password",
                placeholder="Re-enter password",
                key="reg_confirm_input",
            )

            col_submit, col_back = st.columns([2, 1])
            with col_submit:
                submitted = st.form_submit_button("Create Account", type="primary", width="stretch")
            with col_back:
                back = st.form_submit_button("Back to Login", width="stretch")

        if back:
            st.session_state.pop("show_register", None)
            st.rerun()

        if submitted:
            from services.registration_service import register_student
            user, err = register_student(
                db=db,
                email=email.strip(),
                password=password,
                confirm_password=confirm_password,
                full_name=full_name.strip(),
                program_id=selected_prog_id,
                semester=int(semester),
            )
            if err:
                st.error(err)
            else:
                # Auto-login on successful registration
                st.session_state.pop("show_register", None)
                st.session_state.user_id = user.id
                st.session_state.name = user.full_name
                st.session_state.role = user.role.name
                st.session_state.email = user.email
                st.session_state.department = getattr(user, "department", None)
                st.session_state.login_time = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                st.query_params.clear()
                st.query_params["session"] = create_session_token(user.id, user.role.name)
                st.success("Account created! Redirecting to your dashboard…")
                st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Google-OAuth first-time student onboarding  (kept intact — do not remove)
# ─────────────────────────────────────────────────────────────────────────────

def render_student_onboarding(db, google_info: dict) -> None:
    """Render the first-time student profile onboarding form (Google OAuth flow)."""
    from models.schema import Department, Program
    from services.oauth_service import register_google_student, parse_student_enrollment_from_email

    email = (google_info.get("email") or "").strip()
    google_name = (google_info.get("name") or email.split("@")[0]).strip()
    extracted_enrollment = parse_student_enrollment_from_email(email) or email.split("@")[0].split(".gvp")[0]
    logo_html = _logo_html("100px")

    st.markdown("<div style='max-width:620px; margin:0 auto;'>", unsafe_allow_html=True)
    if logo_html:
        st.markdown(f"<div style='display:flex; justify-content:center; margin-bottom:0.5rem;'>{logo_html}</div>", unsafe_allow_html=True)
    st.markdown(
        "<div style='text-align:center; margin-bottom:1rem;'>"
        "<h2 style='margin-top:0;'>🎓 First-Time Student Profile Setup</h2>"
        "<div style='font-size:0.9rem; color:var(--muted);'>Please select your academic details to complete registration and access your practicals.</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.info(f"Signing in as **{email}**")

    departments = list(db.scalars(select(Department).order_by(Department.name)))
    if not departments:
        st.error("No departments have been configured by the administrator yet. Please contact your administrator.")
        if st.button("Back to Login"):
            st.session_state.pop("google_pending_registration", None)
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
        return

    dept_labels = {d.id: f"{d.code} · {d.name}" for d in departments}
    selected_dept_id = st.selectbox(
        "Department",
        list(dept_labels.keys()),
        format_func=lambda x: dept_labels[x],
        key="onboarding_dept_select",
    )

    programs = list(
        db.scalars(
            select(Program)
            .where(Program.department_id == selected_dept_id)
            .order_by(Program.code)
        )
    )
    if not programs:
        programs = list(db.scalars(select(Program).order_by(Program.code)))

    if not programs:
        st.error("No programmes configured for this department yet. Please contact your administrator.")
        if st.button("Back to Login"):
            st.session_state.pop("google_pending_registration", None)
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
        return

    prog_labels = {p.id: f"{p.code} · {p.name}" for p in programs}
    selected_prog_id = st.selectbox(
        "Programme",
        list(prog_labels.keys()),
        format_func=lambda x: prog_labels[x],
        key="onboarding_prog_select",
    )

    sel_prog = db.get(Program, selected_prog_id)
    max_semesters = sel_prog.total_semesters if sel_prog else 4

    with st.form("student_onboarding_form"):
        st.text_input("Enrollment Number", value=extracted_enrollment, disabled=True, help="Auto-detected from your GVP institutional email")
        full_name = st.text_input("Full Name", value=google_name)
        semester = st.selectbox(
            "Current Semester",
            options=list(range(1, max_semesters + 1)),
            index=0,
            format_func=lambda s: f"Semester {s}",
        )

        submit_col, cancel_col = st.columns([2, 1])
        with submit_col:
            submitted = st.form_submit_button("Complete Setup & Enter Dashboard", type="primary", width="stretch")
        with cancel_col:
            cancelled = st.form_submit_button("Cancel", width="stretch")

        if cancelled:
            st.session_state.pop("google_pending_registration", None)
            st.rerun()

        if submitted:
            if not full_name.strip():
                st.error("Please enter your full name.")
            else:
                user, err = register_google_student(
                    db=db,
                    google_info=google_info,
                    program_id=selected_prog_id,
                    semester=int(semester),
                    full_name=full_name.strip(),
                )
                if err:
                    st.error(err)
                else:
                    st.session_state.pop("google_pending_registration", None)
                    st.session_state.user_id = user.id
                    st.session_state.name = user.full_name
                    st.session_state.role = user.role.name
                    st.session_state.email = user.email
                    st.session_state.department = getattr(user, "department", None)
                    st.session_state.login_time = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                    st.query_params.clear()
                    st.query_params["session"] = create_session_token(user.id, user.role.name)
                    st.success("Profile setup complete! Redirecting…")
                    st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


if __name__ == "__main__":
    render_login()
