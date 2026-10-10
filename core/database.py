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
    """Run all pending Alembic migrations up to head, then ensure table guards.

    This is the production-grade schema upgrade path. Automatically runs
    on startup without requiring manual CLI commands.
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
        pass
    finally:
        # Guarantee that all tables and safe column additions are present
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

        # PDF display fields on practicals (nullable, non-breaking)
        if inspect(engine).has_table("practicals"):
            practical_columns = {column["name"] for column in inspect(engine).get_columns("practicals")}
            if "unit_name" not in practical_columns:
                connection.execute(text("ALTER TABLE practicals ADD COLUMN unit_name VARCHAR(100)"))
            if "practical_code" not in practical_columns:
                connection.execute(text("ALTER TABLE practicals ADD COLUMN practical_code VARCHAR(30)"))
            if "assigned_date" not in practical_columns:
                connection.execute(text("ALTER TABLE practicals ADD COLUMN assigned_date DATE"))

        # Signature image fields on users and subject_header_configs (nullable)
        if inspect(engine).has_table("users"):
            user_columns = {column["name"] for column in inspect(engine).get_columns("users")}
            if "signature_image_path" not in user_columns:
                connection.execute(text("ALTER TABLE users ADD COLUMN signature_image_path VARCHAR(255)"))

        if inspect(engine).has_table("subject_header_configs"):
            cfg_columns = {column["name"] for column in inspect(engine).get_columns("subject_header_configs")}
            if "institution_name" not in cfg_columns:
                connection.execute(text("ALTER TABLE subject_header_configs ADD COLUMN institution_name VARCHAR(200) DEFAULT 'Gujarat Vidyapith'"))
            if "department_name" not in cfg_columns:
                connection.execute(text("ALTER TABLE subject_header_configs ADD COLUMN department_name VARCHAR(200) DEFAULT 'Department of Computer Science'"))
            if "course_name" not in cfg_columns:
                connection.execute(text("ALTER TABLE subject_header_configs ADD COLUMN course_name VARCHAR(150) DEFAULT ''"))
            if "academic_year" not in cfg_columns:
                connection.execute(text("ALTER TABLE subject_header_configs ADD COLUMN academic_year VARCHAR(50) DEFAULT '2026-27'"))
            if "subject_teacher" not in cfg_columns:
                connection.execute(text("ALTER TABLE subject_header_configs ADD COLUMN subject_teacher VARCHAR(150) DEFAULT ''"))
            if "subject_title" not in cfg_columns:
                connection.execute(text("ALTER TABLE subject_header_configs ADD COLUMN subject_title VARCHAR(300) DEFAULT ''"))
            if "signature_image_path" not in cfg_columns:
                connection.execute(text("ALTER TABLE subject_header_configs ADD COLUMN signature_image_path VARCHAR(255)"))
            if "updated_at" not in cfg_columns:
                connection.execute(text("ALTER TABLE subject_header_configs ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"))


def get_db() -> Generator:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

