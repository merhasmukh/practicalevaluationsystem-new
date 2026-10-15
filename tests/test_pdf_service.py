"""Tests for institutional practical list PDF generation service."""

from datetime import date, datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.database import Base
from models.schema import (
    Assignment,
    Department,
    Evaluation,
    FacultySubject,
    Practical,
    Program,
    Role,
    Student,
    Subject,
    SubjectHeaderConfig,
    Submission,
    User,
)
from services.pdf_service import (
    generate_blank_master_pdf,
    generate_practical_list_pdf,
)


def _setup_test_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_generate_blank_master_pdf():
    with _setup_test_db() as db:
        dept = Department(code="CS", name="Department of Computer Science")
        prog = Program(code="PGDCA", name="Post Graduate Diploma in Computer Applications", department=dept)
        teacher = User(username="hmer", full_name="Shri Hasmukh Mer", email="hmer@example.com", password_hash="dummy", role=Role(name="Faculty"))
        student_user = User(username="stud1", full_name="Student One", email="stud1@example.com", password_hash="dummy", role=Role(name="Student"))
        db.add_all([dept, prog, teacher, student_user])
        db.flush()

        subj = Subject(code="AI101", name="Lab based on Python Ecosystem for AI", department_id=dept.id, program_id=prog.id, semester=1)
        db.add(subj)
        db.flush()

        # Add practical with rich description
        p1 = Practical(
            subject_id=subj.id,
            practical_number=1,
            practical_code="U1-P1",
            unit_name="Unit-1 & Unit-2",
            title="Develop a Python program to manage AI model configuration",
            description="The program should:\n  ● Create variables\n    ○ Model Name\n    ○ Temperature\n      ■ Display size",
            learning_outcome="Understand AI configurations",
            assigned_date=date(2026, 7, 31),
            submission_date=date(2026, 8, 6),
            created_by=teacher.id,
        )
        db.add(p1)
        db.commit()

        # Generate blank PDF
        pdf_buf = generate_blank_master_pdf(db, subj.id)
        content = pdf_buf.getvalue()
        assert content.startswith(b"%PDF-")
        assert len(content) > 1000


def test_generate_student_pdf_with_evaluations():
    with _setup_test_db() as db:
        dept = Department(code="CS", name="Department of Computer Science")
        prog = Program(code="PGDCA", name="PGDCA", department=dept)
        teacher = User(username="hmer2", full_name="Shri Hasmukh Mer", email="hmer2@example.com", password_hash="dummy", role=Role(name="Faculty"))
        stud_user = User(username="stud2", full_name="Student Two", email="stud2@example.com", password_hash="dummy", role=Role(name="Student"))
        db.add_all([dept, prog, teacher, stud_user])
        db.flush()

        student = Student(user_id=stud_user.id, enrollment_no="2026001234", semester=1, program_id=prog.id)
        db.add(student)
        db.flush()

        subj = Subject(code="AI102", name="Python AI Lab", department_id=dept.id, program_id=prog.id, semester=1)
        db.add(subj)
        db.flush()

        # Custom header config
        cfg = SubjectHeaderConfig(
            subject_id=subj.id,
            institution_name="Gujarat Vidyapith",
            department_name="Department of Computer Science",
            course_name="PGDCA Semester-I",
            academic_year="2026-27",
            subject_teacher="Shri Hasmukh Mer",
            subject_title="Python Ecosystem for AI",
        )
        db.add(cfg)

        p1 = Practical(
            subject_id=subj.id,
            practical_number=1,
            practical_code="U1-P1",
            title="P1 Title",
            description="Simple description",
            assigned_date=date(2026, 7, 31),
            submission_date=date(2026, 8, 6),
            created_by=teacher.id,
        )
        db.add(p1)
        db.flush()

        asgn = Assignment(
            practical_id=p1.id,
            student_id=student.id,
            assigned_at=datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc),
            deadline=datetime(2026, 8, 6, 23, 59, tzinfo=timezone.utc),
            status="Evaluated",
        )
        db.add(asgn)
        db.flush()

        subm = Submission(
            assignment_id=asgn.id,
            github_url="https://github.com/student/repo",
            submitted_at=datetime(2026, 8, 5, 14, 30, tzinfo=timezone.utc),
        )
        db.add(subm)
        db.flush()

        evaln = Evaluation(
            submission_id=subm.id,
            evaluator_id=teacher.id,
            grade="A",
            remarks="Excellent work and adherence to guidelines",
            published=True,
        )
        db.add(evaln)
        db.commit()

        # Generate student PDF
        pdf_buf = generate_practical_list_pdf(db, subj.id, student_id=student.id)
        content = pdf_buf.getvalue()
        assert content.startswith(b"%PDF-")
        assert len(content) > 1000


def test_invalid_subject_raises():
    with _setup_test_db() as db:
        with pytest.raises(ValueError, match="Subject with id=9999 not found"):
            generate_practical_list_pdf(db, subject_id=9999)


def test_subject_without_practicals_raises():
    with _setup_test_db() as db:
        dept = Department(code="CS", name="Dept of CS")
        subj = Subject(code="EMPTY101", name="Empty Subject", department=dept, semester=1)
        db.add_all([dept, subj])
        db.commit()

        with pytest.raises(ValueError, match="No practicals found for subject"):
            generate_practical_list_pdf(db, subject_id=subj.id)


def test_generate_student_pdf_with_signature_image():
    with _setup_test_db() as db:
        dept = Department(code="CS", name="Department of Computer Science")
        prog = Program(code="PGDCA", name="PGDCA", department=dept)
        teacher = User(
            username="hmer3",
            full_name="Shri Hasmukh Mer",
            email="hmer3@example.com",
            password_hash="dummy",
            role=Role(name="Faculty"),
            signature_image_path="assets/gujarat-vidyapith-logo.png",
        )
        stud_user = User(username="stud3", full_name="Student Three", email="stud3@example.com", password_hash="dummy", role=Role(name="Student"))
        db.add_all([dept, prog, teacher, stud_user])
        db.flush()

        student = Student(user_id=stud_user.id, enrollment_no="2026009999", semester=1, program_id=prog.id)
        db.add(student)
        db.flush()

        subj = Subject(code="AI103", name="Python AI Lab", department_id=dept.id, program_id=prog.id, semester=1)
        db.add(subj)
        db.flush()

        p1 = Practical(
            subject_id=subj.id,
            practical_number=1,
            practical_code="U1-P1",
            title="P1 Title",
            description="Practical with signature image test",
            created_by=teacher.id,
        )
        db.add(p1)
        db.flush()

        asgn = Assignment(
            practical_id=p1.id,
            student_id=student.id,
            assigned_at=datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc),
            deadline=datetime(2026, 8, 6, 23, 59, tzinfo=timezone.utc),
            status="Evaluated",
        )
        db.add(asgn)
        db.flush()

        subm = Submission(
            assignment_id=asgn.id,
            github_url="https://github.com/student/repo",
            submitted_at=datetime(2026, 8, 5, 14, 30, tzinfo=timezone.utc),
        )
        db.add(subm)
        db.flush()

        evaln = Evaluation(
            submission_id=subm.id,
            evaluator_id=teacher.id,
            grade="A",
            remarks="Very well done",
            published=True,
            evaluated_at=datetime(2026, 8, 10, 11, 0, tzinfo=timezone.utc),
        )
        db.add(evaln)
        db.commit()

        # Generate student PDF with signature image
        pdf_buf = generate_practical_list_pdf(db, subj.id, student_id=student.id)
        content = pdf_buf.getvalue()
        assert content.startswith(b"%PDF-")
        assert len(content) > 1000


def test_massive_practical_description_chunks_across_pages():
    """Verify that a practical with an exceptionally large description (>800 pt)

    chunks automatically across table rows and renders across pages without
    triggering a ReportLab LayoutError ('too large in frame').
    """
    with _setup_test_db() as db:
        dept = Department(code="CS", name="Dept of CS")
        subj = Subject(code="AI101", name="AI Lab", department=dept, semester=1)
        teacher = User(username="prof", full_name="Professor One", email="prof@univ.edu", password_hash="dummy", role=Role(name="Faculty"))
        db.add_all([dept, subj, teacher])
        db.commit()

        massive_desc = "Develop an advanced AI system with the following detailed components:\n" + "\n".join([
            f"- Requirement {i}: Perform detailed step {i} involving multiple configuration checks and data validation."
            for i in range(1, 90)
        ])

        p_massive = Practical(
            subject_id=subj.id,
            practical_number=1,
            practical_code="P1",
            title="Massive Practical Title",
            description=massive_desc,
            created_by=teacher.id,
        )
        db.add(p_massive)
        db.commit()

        # Should generate without any LayoutError
        pdf_buf = generate_blank_master_pdf(db, subj.id)
        content = pdf_buf.getvalue()
        assert content.startswith(b"%PDF-")
        assert len(content) > 2000

