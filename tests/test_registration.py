"""Tests for services/registration_service.py — student self-registration."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.database import Base
from models.schema import Department, Program, Student, User
from services.core_services import ensure_role
from services.registration_service import (
    parse_enrollment_from_email,
    validate_student_email,
    register_student,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        # Seed roles and master data
        ensure_role(session, "Student")
        dept = Department(name="Dept of CS", code="CS")
        session.add(dept)
        session.flush()
        prog = Program(
            code="MCA",
            name="Master of Computer Applications",
            duration_months=24,
            total_semesters=4,
            department_id=dept.id,
        )
        session.add(prog)
        session.commit()
        yield session


# ── parse_enrollment_from_email ──────────────────────────────────────────────

def test_parse_enrollment_9_digits():
    assert parse_enrollment_from_email("202301234@gujaratvidyapith.org") == "202301234"


def test_parse_enrollment_12_digits():
    assert parse_enrollment_from_email("202301234567@gujaratvidyapith.org") == "202301234567"


def test_parse_enrollment_gvp_suffix():
    assert parse_enrollment_from_email("202301234.gvp@gujaratvidyapith.org") == "202301234"


def test_parse_enrollment_non_student_email_returns_none():
    assert parse_enrollment_from_email("hasmukh@gujaratvidyapith.org") is None


def test_parse_enrollment_empty_returns_none():
    assert parse_enrollment_from_email("") is None


# ── validate_student_email ───────────────────────────────────────────────────

def test_validate_good_email_returns_none():
    assert validate_student_email("202301234@gujaratvidyapith.org") is None


def test_validate_wrong_domain_returns_error():
    err = validate_student_email("202301234@gmail.com")
    assert err is not None
    assert "gujaratvidyapith.org" in err


def test_validate_non_numeric_prefix_returns_error():
    err = validate_student_email("rahul@gujaratvidyapith.org")
    assert err is not None


def test_validate_empty_returns_error():
    err = validate_student_email("")
    assert err is not None


# ── register_student ─────────────────────────────────────────────────────────

def _prog_id(db):
    return db.query(Program).filter_by(code="MCA").first().id


def test_register_student_success(db):
    user, err = register_student(
        db=db,
        email="202301234@gujaratvidyapith.org",
        password="secret123",
        confirm_password="secret123",
        full_name="Rahul Patel",
        program_id=_prog_id(db),
        semester=1,
    )
    assert err is None
    assert user is not None
    assert user.username == "202301234"
    assert user.email == "202301234@gujaratvidyapith.org"
    assert user.role.name == "Student"
    # Student profile linked
    assert user.student is not None
    assert user.student.enrollment_no == "202301234"
    assert user.student.semester == 1


def test_register_student_password_mismatch(db):
    _, err = register_student(
        db=db,
        email="202301235@gujaratvidyapith.org",
        password="abc123",
        confirm_password="xyz999",
        full_name="Test User",
        program_id=_prog_id(db),
        semester=1,
    )
    assert err is not None
    assert "match" in err.lower()


def test_register_student_password_too_short(db):
    _, err = register_student(
        db=db,
        email="202301236@gujaratvidyapith.org",
        password="ab",
        confirm_password="ab",
        full_name="Test User",
        program_id=_prog_id(db),
        semester=1,
    )
    assert err is not None
    assert "6" in err  # MIN_PASSWORD_LENGTH


def test_register_student_invalid_email(db):
    _, err = register_student(
        db=db,
        email="notanemail@gmail.com",
        password="secret123",
        confirm_password="secret123",
        full_name="Test User",
        program_id=_prog_id(db),
        semester=1,
    )
    assert err is not None


def test_register_student_duplicate_email(db):
    register_student(
        db=db,
        email="202301237@gujaratvidyapith.org",
        password="secret123",
        confirm_password="secret123",
        full_name="First User",
        program_id=_prog_id(db),
        semester=1,
    )
    _, err = register_student(
        db=db,
        email="202301237@gujaratvidyapith.org",
        password="another123",
        confirm_password="another123",
        full_name="Duplicate",
        program_id=_prog_id(db),
        semester=1,
    )
    assert err is not None
    assert "already exists" in err.lower()


def test_register_student_semester_out_of_range(db):
    _, err = register_student(
        db=db,
        email="202301238@gujaratvidyapith.org",
        password="secret123",
        confirm_password="secret123",
        full_name="Test",
        program_id=_prog_id(db),
        semester=99,   # MCA has 4 semesters
    )
    assert err is not None
    assert "semester" in err.lower()


def test_register_student_missing_name(db):
    _, err = register_student(
        db=db,
        email="202301239@gujaratvidyapith.org",
        password="secret123",
        confirm_password="secret123",
        full_name="",
        program_id=_prog_id(db),
        semester=1,
    )
    assert err is not None
    assert "name" in err.lower()
