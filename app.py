from datetime import datetime, timezone
import streamlit as st
from core.database import SessionLocal, init_db, run_migrations
from ui.auth import render_login
from models.schema import User
from ui.admin import administrator_page
from ui.faculty import faculty_page
from ui.dashboard import dashboard, student_dashboard
from ui.student import student_page
from core.config import settings
from core.session_manager import verify_session_token, create_session_token
from core.logger import get_logger

logger = get_logger(__name__)

from ui.theme import apply_theme

st.set_page_config(page_title="TPEMS | Gujarat Vidyapith", page_icon="🎓", layout="wide")
apply_theme()




def render_brand_header() -> None:
  return None





run_migrations()

# ── Auto-seed on cold start ───────────────────────────────────────────────────
# Bootstraps roles, permissions, and academic master data on cold start if empty.
def _bootstrap_db_if_empty() -> None:
    try:
        from sqlalchemy import text
        from seed import seed
        with SessionLocal() as _db:
            role_count = _db.execute(text("SELECT COUNT(*) FROM roles")).scalar()
            if role_count == 0:
                logger.info("Roles table empty — running seed() to bootstrap initial data.")
                seed()
    except Exception as _e:
        logger.warning(f"Auto-seed skipped: {_e}")

_bootstrap_db_if_empty()
if "user_id" not in st.session_state:

    st.session_state.user_id = None


def login() -> None:
    render_login()






with SessionLocal() as db:
    # Restore session from signed query token on browser reload if present
    if not st.session_state.get("user_id"):
        session_token = st.query_params.get("session")
        if session_token:
            payload = verify_session_token(session_token)
            if payload:
                restored_user = db.get(User, payload["user_id"])
                if restored_user and restored_user.is_active and not restored_user.account_locked:
                    logger.info("Session restored from query token", extra={"user_id": restored_user.id, "role": restored_user.role.name})
                    st.session_state.user_id = restored_user.id
                    st.session_state.name = restored_user.full_name
                    st.session_state.role = restored_user.role.name
                    st.session_state.email = restored_user.email
                    st.session_state.department = getattr(restored_user, 'department', None)
                    st.session_state.login_time = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            else:
                st.query_params.clear()

    # Handle Google OAuth callback if code or error is present in query parameters
    if not st.session_state.get("user_id"):
        oauth_error_param = st.query_params.get("error")
        if oauth_error_param:
            err_desc = st.query_params.get("error_description") or oauth_error_param
            st.session_state["google_auth_error"] = f"Google Sign-In failed: {err_desc}"
            st.query_params.clear()
            st.rerun()

        auth_code = st.query_params.get("code")
        if auth_code:
            from services.oauth_service import exchange_code_for_user_info, authenticate_google_user
            google_info, exchange_err = exchange_code_for_user_info(auth_code)
            if google_info:
                oauth_user, oauth_err = authenticate_google_user(db, google_info)
                if oauth_user:
                    logger.info("Google OAuth login successful", extra={"user_id": oauth_user.id, "role": oauth_user.role.name})
                    st.session_state.user_id = oauth_user.id
                    st.session_state.name = oauth_user.full_name
                    st.session_state.role = oauth_user.role.name
                    st.session_state.email = oauth_user.email
                    st.session_state.department = getattr(oauth_user, "department", None)
                    st.session_state.login_time = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                    st.query_params.clear()
                    st.query_params["session"] = create_session_token(oauth_user.id, oauth_user.role.name)
                    st.rerun()
                elif oauth_err in ["FIRST_TIME_STUDENT_SETUP", "NEEDS_STUDENT_PROFILE"]:
                    st.session_state["google_pending_registration"] = google_info
                    st.query_params.clear()
                    st.rerun()
                elif oauth_err == "FIRST_TIME_FACULTY_SETUP":
                    # Non-student institutional email — auto-register as Faculty and log in
                    from services.oauth_service import register_google_faculty
                    faculty_user, reg_err = register_google_faculty(db, google_info)
                    if faculty_user:
                        logger.info("Google OAuth faculty auto-registered", extra={"user_id": faculty_user.id})
                        st.session_state.user_id = faculty_user.id
                        st.session_state.name = faculty_user.full_name
                        st.session_state.role = faculty_user.role.name
                        st.session_state.email = faculty_user.email
                        st.session_state.department = getattr(faculty_user, "department", None)
                        st.session_state.login_time = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                        st.query_params.clear()
                        st.query_params["session"] = create_session_token(faculty_user.id, faculty_user.role.name)
                        st.rerun()
                    else:
                        st.session_state["google_auth_error"] = reg_err or "Faculty account creation failed."
                        st.query_params.clear()
                        st.rerun()
                else:
                    st.session_state["google_auth_error"] = oauth_err
                    st.query_params.clear()
                    st.rerun()
            else:
                st.session_state["google_auth_error"] = exchange_err or "Failed to exchange authorization code with Google."
                st.query_params.clear()
                st.rerun()

    if not st.session_state.get("user_id"):
        if "google_pending_registration" in st.session_state:
            from ui.auth import render_student_onboarding
            render_student_onboarding(db, st.session_state["google_pending_registration"])
        else:
            render_login()
    else:
        login_time_str = st.session_state.get("login_time")
        if login_time_str:
            try:
                login_time = datetime.fromisoformat(login_time_str)
                if (datetime.now(timezone.utc).replace(tzinfo=None) - login_time).total_seconds() > settings.session_timeout_minutes * 60:
                    logger.info("Session timed out due to inactivity", extra={"user_id": st.session_state.get("user_id")})
                    st.session_state.clear()
                    st.query_params.clear()
                    st.warning("Your session has timed out due to inactivity. Please sign in again.")
                    st.rerun()
            except Exception:
                pass
        user = db.get(User, st.session_state.user_id)
        if not user:
            st.session_state.user_id = None
            st.query_params.clear()
            st.rerun()
      # brand header removed
        with st.sidebar:
          st.markdown("**Practical Evaluation System**")
          st.caption(f"{user.full_name} · {user.role.name}")
          
          # View As role switcher for users with elevated/multi-role capabilities
          if user.role.name == "Administrator":
            view_choices = ["Administrator", "Faculty"]
            active_view = st.selectbox(
                "👁️ View as",
                view_choices,
                index=0,
                key="app_active_view",
                help="Switch between Administrator management view and Faculty teaching view",
            )
          else:
            active_view = user.role.name

          if active_view == "Administrator":
            workspace_options = ["Dashboard", "Administration"]
            welcome = "Manages master data, faculty, and users."
          elif active_view == "Faculty":
            workspace_options = ["Dashboard", "My subjects"]
            welcome = "Works within the subjects assigned to you."
          else:
            workspace_options = ["Dashboard", "Practicals"]
            welcome = ""

          if welcome:
            st.caption(welcome)
          st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
          page = st.radio("Workspace", workspace_options, label_visibility="visible")
          st.markdown("---")

          if st.button("Sign out"):
            logger.info("User signed out", extra={"user_id": st.session_state.get("user_id")})
            st.session_state.clear()
            st.query_params.clear()
            st.rerun()

        if page == "Dashboard":
            if active_view == "Student":
                if user.student:
                    student_dashboard(db, user.student)
                else:
                    st.error("Your student profile is incomplete. Please contact the administrator.")
            else:
                dashboard(db, user, active_role=active_view)
        elif page == "Administration" and user.role.name == "Administrator" and active_view == "Administrator":
            administrator_page(db, user)
        elif page == "My subjects" and user.role.name in ["Faculty", "Administrator"]:
            faculty_page(db, user)
        elif page == "Practicals" and user.role.name == "Student":
            if user.student:
                student_page(db, user.student)
            else:
                st.error("Your student profile is incomplete. Please contact the administrator.")
        else:
            st.error("You do not have permission to access this page.")
            
        with st.sidebar:
          st.markdown(
            """
            <div class="sidebar-footer">
              <div class="sidebar-footer-divider"></div>
              <div class="sidebar-footer-brand">🎓 Gujarat Vidyapith</div>
              <div class="sidebar-footer-sub">Dept. of Computer Science</div>
              <div class="sidebar-footer-copy">© 2026 · Practical Evaluation System</div>
            </div>
            """,
            unsafe_allow_html=True,
          )

