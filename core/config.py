from dataclasses import dataclass
import os
import warnings


def _parse_env_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "=" not in line:
        return None
    key, val = line.split("=", 1)
    key = key.strip()
    val = val.strip()

    if val.startswith('"'):
        end_idx = val.find('"', 1)
        if end_idx != -1:
            val = val[1:end_idx]
        else:
            val = val.strip('"')
    elif val.startswith("'"):
        end_idx = val.find("'", 1)
        if end_idx != -1:
            val = val[1:end_idx]
        else:
            val = val.strip("'")
    else:
        if "#" in val:
            val = val.split("#", 1)[0].strip()
    return key, val


def _load_env_file() -> None:
    """Load key-value pairs from .env file into os.environ if present."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(base_dir, ".env"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        parsed = _parse_env_line(line)
                        if parsed:
                            key, val = parsed
                            # Overwrite or set in environ
                            os.environ[key] = val
            except Exception:
                pass
            break


_load_env_file()


def _get_setting_raw(key: str, default: any = None) -> any:
    """Get raw setting from Streamlit secrets (with nested support) or environment."""
    try:
        import streamlit as st
        if hasattr(st, "secrets") and st.secrets:
            # Direct key match
            if key in st.secrets:
                return st.secrets[key]
            # Lowercase key match
            if key.lower() in st.secrets:
                return st.secrets[key.lower()]
            # Nested section match (e.g. [mysql] host = "..." for MYSQL_HOST)
            if "_" in key:
                section, subkey = key.lower().split("_", 1)
                if section in st.secrets:
                    sec_obj = st.secrets[section]
                    if hasattr(sec_obj, "get") and subkey in sec_obj:
                        return sec_obj[subkey]
                    if hasattr(sec_obj, subkey):
                        return getattr(sec_obj, subkey)
    except Exception:
        pass

    val = os.getenv(key)
    if val is not None:
        return val

    return default


def _get_setting(key: str, default: str = "") -> str:
    """Get string setting from Streamlit secrets or environment."""
    val = _get_setting_raw(key, default)
    if val is None:
        return default
    if isinstance(val, (list, tuple, dict)):
        return str(val)
    return str(val)


def _resolve_database_url() -> str:
    """Resolve database URL from explicit DATABASE_URL or discrete MySQL settings."""
    explicit_url = _get_setting("DATABASE_URL", "").strip()
    if explicit_url:
        return explicit_url

    # Check discrete MySQL environment/secrets
    host = _get_setting("MYSQL_HOST") or _get_setting("DB_HOST")
    user = _get_setting("MYSQL_USER") or _get_setting("DB_USER")
    password = _get_setting("MYSQL_PASSWORD") or _get_setting("DB_PASSWORD")
    db_name = _get_setting("MYSQL_DATABASE") or _get_setting("DB_NAME")
    port = _get_setting("MYSQL_PORT") or _get_setting("DB_PORT") or "3306"

    # Also check [mysql] section in secrets.toml if available
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "mysql" in st.secrets:
            sec = st.secrets["mysql"]
            host = host or str(sec.get("host", ""))
            user = user or str(sec.get("user", ""))
            password = password or str(sec.get("password", ""))
            db_name = db_name or str(sec.get("database", "") or sec.get("db", ""))
            port = port or str(sec.get("port", "3306"))
    except Exception:
        pass

    if host and user and db_name:
        # Build MySQL connection URL
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{db_name}"

    return "sqlite:///tpems.db"


def _resolve_admin_emails() -> tuple[str, ...]:
    """Parse and normalize admin email list from secrets/environment."""
    raw = _get_setting_raw("ADMIN_EMAILS") or _get_setting_raw("admin_emails")
    if not raw:
        # Fallback to single ADMIN_EMAIL or [admin] email
        raw = _get_setting_raw("ADMIN_EMAIL") or _get_setting_raw("admin_email")

    if not raw:
        try:
            import streamlit as st
            if hasattr(st, "secrets") and "admin" in st.secrets:
                admin_sec = st.secrets["admin"]
                raw = admin_sec.get("emails") or admin_sec.get("email")
        except Exception:
            pass

    emails: list[str] = []
    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            s = str(item).strip().lower()
            if s and "@" in s and s not in emails:
                emails.append(s)
    elif isinstance(raw, str) and raw.strip():
        for part in raw.replace(";", ",").split(","):
            s = part.strip().strip('"').strip("'").lower()
            if s and "@" in s and s not in emails:
                emails.append(s)

    return tuple(emails)


@dataclass(frozen=True)
class Settings:
    database_url: str = _resolve_database_url()
    admin_emails: tuple[str, ...] = _resolve_admin_emails()
    secret_key: str = _get_setting("SECRET_KEY", "development-only-change-me")
    smtp_host: str = _get_setting("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = int(_get_setting("SMTP_PORT", "587"))
    smtp_user: str = _get_setting("SMTP_USER", "")
    smtp_password: str = _get_setting("SMTP_PASSWORD", "")
    mail_from: str = _get_setting("MAIL_FROM", "")
    session_timeout_minutes: int = int(_get_setting("SESSION_TIMEOUT_MINUTES", "60"))
    google_client_id: str = _get_setting("GOOGLE_CLIENT_ID", "")
    google_client_secret: str = _get_setting("GOOGLE_CLIENT_SECRET", "")
    google_redirect_uri: str = _get_setting("GOOGLE_REDIRECT_URI", "http://localhost:8501")
    google_hosted_domain: str = _get_setting("GOOGLE_HOSTED_DOMAIN", "")  # e.g. gujaratvidyapith.org

    def is_admin_email(self, email: str | None) -> bool:
        if not email:
            return False
        return email.strip().lower() in self.admin_emails


def load_settings() -> Settings:
    """Reload and return fresh Settings instance."""
    _load_env_file()
    return Settings(
        database_url=_resolve_database_url(),
        admin_emails=_resolve_admin_emails(),
    )


settings = load_settings()

if settings.secret_key == "development-only-change-me":
    warnings.warn(
        "SECRET_KEY is set to the default development key. Please configure SECRET_KEY in your environment for production.",
        UserWarning,
    )



