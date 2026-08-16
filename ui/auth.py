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


def render_login() -> None:
    logo_b64 = _get_logo_base64()
    logo_img_tag = (
        f'<img src="data:image/png;base64,{logo_b64}" alt="Gujarat Vidyapith" style="width: 140px; max-width: 45vw; height: auto; margin: 0 auto; display: block;" />'
        if logo_b64
        else ""
    )

    # Centered card layout
    col_left, col_center, col_right = st.columns([1, 2.2, 1])
    with col_center:
        st.markdown(
            f"""
            <div style='text-align: center; margin-bottom: 1.25rem;'>
                <div style='display: flex; justify-content: center; align-items: center; margin-bottom: 0.85rem;'>
                    {logo_img_tag}
                </div>
                <div style='font-size: 1.6rem; font-weight: 700; color: var(--ink); line-height: 1.25; margin-bottom: 0.25rem;'>
                    Gujarat Vidyapith
                </div>
                <div style='font-size: 0.95rem; color: var(--muted); font-weight: 500; margin-bottom: 0.35rem;'>
                    Department of Computer Science
                </div>
                <div style='font-size: 1.05rem; font-weight: 600; color: var(--accent-strong); line-height: 1.35;'>
                    Transparent Practical Evaluation &amp; Management System
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if "google_auth_error" in st.session_state:
            st.error(st.session_state.pop("google_auth_error"))

        # Google Sign-In Button
        from services.oauth_service import is_google_auth_configured, get_google_auth_url

        if is_google_auth_configured():
            google_url = get_google_auth_url()
            st.link_button(
                "🌐 Sign in with Google",
                google_url,
                type="primary",
                use_container_width=True,
                help="Sign in with your @gujaratvidyapith.org institutional Google account",
            )
            st.markdown(
                """
                <div style='text-align: center; margin-top: 0.85rem; font-size: 0.8rem; color: var(--muted); line-height: 1.45;'>
                    Institutional single sign-on for <strong>Students</strong>, <strong>Faculty</strong> &amp; <strong>Administrators</strong>.
                    <br>
                    <span style='font-size: 0.76rem; opacity: 0.85;'>Use your official <code>@gujaratvidyapith.org</code> account</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.warning("⚠️ **Google Sign-In is not configured yet.**")
            st.markdown(
                """
                To enable **Sign in with Google**, configure the following variables in `.streamlit/secrets.toml` or `.env`:
                ```toml
                GOOGLE_CLIENT_ID     = "your-client-id.apps.googleusercontent.com"
                GOOGLE_CLIENT_SECRET = "your-client-secret"
                GOOGLE_REDIRECT_URI  = "http://localhost:8501"
                GOOGLE_HOSTED_DOMAIN = "gujaratvidyapith.org"
                ```
                """
            )




def render_student_onboarding(db, google_info: dict) -> None:
    """Render the first-time student profile onboarding form."""
    from models.schema import Department, Program
    from services.oauth_service import register_google_student, parse_student_enrollment_from_email
    
    email = (google_info.get("email") or "").strip()
    google_name = (google_info.get("name") or email.split("@")[0]).strip()
    extracted_enrollment = parse_student_enrollment_from_email(email) or email.split("@")[0].split(".gvp")[0]
    logo_b64 = _get_logo_base64()
    logo_img_tag = (
        f'<img src="data:image/png;base64,{logo_b64}" alt="Gujarat Vidyapith" style="width: 100px; height: auto; margin: 0 auto; display: block;" />'
        if logo_b64
        else ""
    )

    st.markdown("<div style='max-width: 620px; margin: 0 auto;'>", unsafe_allow_html=True)
    if logo_img_tag:
        st.markdown(f"<div style='display: flex; justify-content: center; margin-bottom: 0.5rem;'>{logo_img_tag}</div>", unsafe_allow_html=True)
    st.markdown("<div style='text-align: center; margin-bottom: 1rem;'><h2 style='margin-top: 0;'>🎓 First-Time Student Profile Setup</h2><div style='font-size: 0.9rem; color: var(--muted);'>Please select your academic details to complete registration and access your practicals.</div></div>", unsafe_allow_html=True)
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
            submitted = st.form_submit_button("Complete Setup & Enter Dashboard", type="primary", use_container_width=True)
        with cancel_col:
            cancelled = st.form_submit_button("Cancel", use_container_width=True)

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
                    st.success("Profile setup complete! Redirecting...")
                    st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


if __name__ == "__main__":
    render_login()
