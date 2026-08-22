from dataclasses import dataclass
import os
import urllib.parse
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
    """Load key-value pairs from .env / env.dev files into os.environ if present."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(base_dir, ".env"),
        os.path.join(os.getcwd(), "env.dev"),
        os.path.join(base_dir, "env.dev"),
        os.path.join(os.getcwd(), ".env.dev"),
        os.path.join(base_dir, ".env.dev"),
        os.path.join(os.getcwd(), ".env.local"),
        os.path.join(base_dir, ".env.local"),
    ]
    loaded = False
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
                loaded = True
            except Exception:
                pass
            if loaded and path.endswith(".env"):
                break


_load_env_file()


def _clean_str(val: any) -> str:
    """Clean and strip quotes from string configuration."""
    if val is None:
        return ""
    s = str(val).strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()
    return s


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
    cleaned = _clean_str(val)
    return cleaned if cleaned != "" else default


def _get_bool_setting(key: str, default: bool = True) -> bool:
    """Get boolean setting from Streamlit secrets or environment.
    Accepts: true/1/yes/on (case-insensitive) as True, everything else as False.
    """
    raw = _get_setting_raw(key)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("true", "1", "yes", "on")



def _resolve_database_url() -> str:
    """Resolve database URL from explicit DATABASE_URL or discrete MySQL settings."""
    explicit_url = _clean_str(_get_setting("DATABASE_URL", ""))
    if explicit_url:
        # Auto-encode passwords containing special characters (like '@') in explicit URLs
        if "://" in explicit_url:
            scheme, rest = explicit_url.split("://", 1)
            authority = rest.split("/", 1)[0]
            path_part = rest[len(authority):]
            if authority.count("@") > 1 and ":" in authority:
                userinfo, host_part = authority.rsplit("@", 1)
                if ":" in userinfo:
                    u, p = userinfo.split(":", 1)
                    explicit_url = f"{scheme}://{urllib.parse.quote_plus(u)}:{urllib.parse.quote_plus(p)}@{host_part}{path_part}"
        return explicit_url

    # Check discrete MySQL environment/secrets
    host = _clean_str(_get_setting("MYSQL_HOST") or _get_setting("DB_HOST"))
    user = _clean_str(_get_setting("MYSQL_USER") or _get_setting("DB_USER"))
    password = _clean_str(_get_setting("MYSQL_PASSWORD") or _get_setting("DB_PASSWORD"))
    db_name = _clean_str(_get_setting("MYSQL_DATABASE") or _get_setting("DB_NAME"))
    port = _clean_str(_get_setting("MYSQL_PORT") or _get_setting("DB_PORT") or "3306")

    # Also check [mysql] section in secrets.toml if available
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "mysql" in st.secrets:
            sec = st.secrets["mysql"]
            host = host or _clean_str(sec.get("host", ""))
            user = user or _clean_str(sec.get("user", ""))
            password = password or _clean_str(sec.get("password", ""))
            db_name = db_name or _clean_str(sec.get("database", "") or sec.get("db", ""))
            port = port or _clean_str(sec.get("port", "3306"))
    except Exception:
        pass

    if host and user and db_name:
        # Properly URL-encode username and password to handle special characters (e.g. '@', ':', '#')
        encoded_user = urllib.parse.quote_plus(user)
        encoded_password = urllib.parse.quote_plus(password)
        return f"mysql+pymysql://{encoded_user}:{encoded_password}@{host}:{port}/{db_name}"

    # SQLite fallback — filename is configurable via DB_FILE env variable
    db_file = _clean_str(_get_setting("DB_FILE", "")) or "tpems.db"
    return f"sqlite:///{db_file}"



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
    # Set ENABLE_GOOGLE_LOGIN=true in .env/secrets.toml to show Google OAuth sign-in
    # (only for public deployments with a registered Google redirect URI).
    # Defaults to False — hidden on private/local IP deployments.
    enable_google_login: bool = _get_bool_setting("ENABLE_GOOGLE_LOGIN", default=False)
    # Password applied to all admin accounts during seed. Set in .env/secrets.toml.
    admin_password: str = _get_setting("ADMIN_PASSWORD", "")
    # Sarvam AI API Key for code evaluation
    sarvam_api_key: str = _get_setting("SARVAM_API_KEY", "")

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
        enable_google_login=_get_bool_setting("ENABLE_GOOGLE_LOGIN", default=False),
        admin_password=_get_setting("ADMIN_PASSWORD", ""),
        sarvam_api_key=_get_setting("SARVAM_API_KEY", ""),
    )


settings = load_settings()

if settings.secret_key == "development-only-change-me":
    warnings.warn(
        "SECRET_KEY is set to the default development key. Please configure SECRET_KEY in your environment for production.",
        UserWarning,
    )



