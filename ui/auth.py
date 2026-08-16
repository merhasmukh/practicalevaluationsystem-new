import streamlit as st
from core.session_manager import create_session_token
from datetime import datetime, timezone
import re



def _rerun() -> None:
    try:
        st.experimental_rerun()
    except AttributeError:
        try:
            st.rerun()
        except AttributeError:
            pass


def render_login() -> None:
    # Layout: left branding, right auth card
    left, right = st.columns([1.2, 1])
    with left:
        st.markdown("&nbsp;")
        st.image("assets/gujarat-vidyapith-logo.png", width=130)
        st.markdown("### Department of Computer Science")
        st.markdown("#### Transparent Practical Evaluation & Management System")
        st.caption("A unified digital platform for practical assignments, GitHub code submissions, automated grading, and academic progress monitoring.")
    with right:
        st.markdown("&nbsp;")
        st.markdown("### Institutional Sign In")
        st.caption("Sign in with your official Gujarat Vidyapith Google Workspace account.")

        if "google_auth_error" in st.session_state:
            st.error(st.session_state.pop("google_auth_error"))

        # Google Sign-In
        from services.oauth_service import is_google_auth_configured, get_google_auth_url

        if is_google_auth_configured():
            google_url = get_google_auth_url()
            st.markdown("<div style='height: 0.5rem;'></div>", unsafe_allow_html=True)
            st.link_button(
                "🌐 Sign in with Google",
                google_url,
                type="primary",
                use_container_width=True,
                help="Sign in with your @gujaratvidyapith.org institutional Google account",
            )
            st.markdown("<div style='height: 0.8rem;'></div>", unsafe_allow_html=True)
            st.info(
                "🎓 **Role-Based Access**:\n"
                "- **Students**: Sign in with your enrollment email (`<enrollment>.gvp@gujaratvidyapith.org`)\n"
                "- **Faculty**: Sign in with your departmental email\n"
                "- **Administrators**: Sign in with your configured admin email",
                icon="ℹ️",
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

    st.markdown("<div style='max-width: 620px; margin: 0 auto;'>", unsafe_allow_html=True)
    st.image("assets/gujarat-vidyapith-logo.png", width=90)
    st.markdown("## 🎓 First-Time Student Profile Setup")
    st.caption("Please select your academic details to complete registration and access your practicals.")
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
