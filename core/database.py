from collections.abc import Generator
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from core.config import settings


class Base(DeclarativeBase):
    pass


is_sqlite = settings.database_url.startswith("sqlite")
if is_sqlite:
    connect_args = {"check_same_thread": False}
    engine = create_engine(
        settings.database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )
else:
    engine = create_engine(
        settings.database_url,
        pool_size=20,
        max_overflow=30,
        pool_recycle=3600,
        pool_pre_ping=True,
    )
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def run_migrations() -> None:
    """Run all pending Alembic migrations up to head.

    This is the production-grade schema upgrade path.
    Falls back silently to init_db() if Alembic is unavailable.
    """
    try:
        import os
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        alembic_ini_path = os.path.join(base_dir, "alembic.ini")
        alembic_cfg = Config(alembic_ini_path)
        alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)

        script = ScriptDirectory.from_config(alembic_cfg)
        with engine.begin() as conn:
            context = MigrationContext.configure(conn)
            current_rev = context.get_current_revision()
            head_rev = script.get_current_head()
            if current_rev != head_rev:
                # There are pending migrations — run them
                from alembic import command
                command.upgrade(alembic_cfg, "head")
    except Exception:
        # Alembic not configured or migrations dir missing — fall back to create_all
        init_db()


def init_db() -> None:
    """Dev/test fallback: create all tables directly from ORM metadata.

    This is kept for local development and the test suite.
    In production, run_migrations() should be used instead.
    """
    from models.schema import all_models  # noqa: F401
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        practical_columns = {column["name"] for column in inspect(engine).get_columns("practicals")}
        if "submission_date" not in practical_columns:
            connection.execute(text("ALTER TABLE practicals ADD COLUMN submission_date DATE"))
        if "grade" not in practical_columns:
            connection.execute(text("ALTER TABLE practicals ADD COLUMN grade VARCHAR(3) DEFAULT 'A'"))

        if inspect(engine).has_table("users"):
            user_columns = {column["name"] for column in inspect(engine).get_columns("users")}
            if "last_login" not in user_columns:
                connection.execute(text("ALTER TABLE users ADD COLUMN last_login DATETIME"))
            if "failed_attempts" not in user_columns:
                connection.execute(text("ALTER TABLE users ADD COLUMN failed_attempts INTEGER DEFAULT 0"))
            if "account_locked" not in user_columns:
                connection.execute(text("ALTER TABLE users ADD COLUMN account_locked BOOLEAN DEFAULT 0"))

        if inspect(engine).has_table("students"):
            student_columns = {column["name"] for column in inspect(engine).get_columns("students")}
            if "program_id" not in student_columns:
                connection.execute(text("ALTER TABLE students ADD COLUMN program_id INTEGER REFERENCES programs(id)"))

        if inspect(engine).has_table("programs"):
            program_columns = {column["name"] for column in inspect(engine).get_columns("programs")}
            if "department_id" not in program_columns:
                connection.execute(text("ALTER TABLE programs ADD COLUMN department_id INTEGER REFERENCES departments(id)"))

        if inspect(engine).has_table("subjects"):
            subject_columns = {column["name"] for column in inspect(engine).get_columns("subjects")}
            if "program_id" not in subject_columns:
                connection.execute(text("ALTER TABLE subjects ADD COLUMN program_id INTEGER REFERENCES programs(id)"))


def get_db() -> Generator:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

