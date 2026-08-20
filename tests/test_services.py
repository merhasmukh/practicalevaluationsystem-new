from datetime import date, datetime, timedelta
import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from core.database import Base
from services.auth_service import ensure_role, hash_password, verify_password
from models.schema import Assignment, Department, FacultySubject, Practical, Student, Subject, Submission, User
from ui.reports import excel_report, pdf_report
from services.core_services import (
    assign_faculty_subjects,
    assign_practical,
    build_practical_import_template,
    create_practical,
    delete_department,
    delete_practical,
    delete_subject,
    delete_user,
    faculty_practicals,
    grade_submission,
    import_practicals_from_dataframe,
    save_submission,
    subjects_for_faculty,
    submissions_for_faculty,
    update_practical,
    validate_bulk_user_import,
    validate_practical_import,
)


def setup_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def seed_world(db):
    faculty_role = ensure_role(db, "Faculty")
    student_role = ensure_role(db, "Student")
    admin_role = ensure_role(db, "Administrator")
    department = Department(name="CS", code="CS")
    db.add(department); db.flush()
    admin = User(username="admin", full_name="Admin", email="a@x", password_hash="x", role=admin_role)
    faculty = User(username="faculty", full_name="Faculty", email="f@x", password_hash="x", role=faculty_role)
    subject = Subject(code="CS1", name="Lab", semester=1, department=department)
    other_subject = Subject(code="CS2", name="Other Lab", semester=1, department=department)
    student_user = User(username="student", full_name="Student", email="s@x", password_hash="x", role=student_role)
    student = Student(user=student_user, enrollment_no="E1", semester=1)
    other_student_user = User(username="student2", full_name="Student 2", email="s2@x", password_hash="x", role=student_role)
    other_student = Student(user=other_student_user, enrollment_no="E2", semester=1)
    db.add_all([admin, faculty, subject, other_subject, student_user, student, other_student_user, other_student]); db.commit()
    return {
        "db": db,
        "department": department,
        "admin": admin,
        "faculty": faculty,
        "subject": subject,
        "other_subject": other_subject,
        "student": student,
        "other_student": other_student,
    }


def test_password_roundtrip():
    value = hash_password("secret")
    assert verify_password("secret", value)
    assert not verify_password("wrong", value)


def test_assign_submit_and_grade():
    db = setup_db()
    world = seed_world(db)
    practical = Practical(subject=world["subject"], practical_number=1, title="Build", created_by=world["faculty"].id, submission_days=7)
    db.add(practical); db.commit()
    assert assign_practical(db, practical, world["faculty"].id) == 2
    assignment = db.scalar(select(Assignment).where(Assignment.student_id == world["student"].id))
    submission = save_submission(db, assignment.id, "https://github.com/example/repo", world["student"].user_id)
    evaluation = grade_submission(db, submission.id, world["faculty"].id, "A", "Good", "Keep testing")
    assert evaluation.grade == "A"
    assert len(excel_report(db).getvalue()) > 0
    assert len(pdf_report(db).getvalue()) > 0


def test_github_file_url_accepted():
    """Students can submit a direct file link (blob or raw) instead of a repo root."""
    db = setup_db()
    world = seed_world(db)
    practical = Practical(subject=world["subject"], practical_number=2, title="File Submit", created_by=world["faculty"].id, submission_days=7)
    db.add(practical); db.commit()
    assign_practical(db, practical, world["faculty"].id)
    assignment = db.scalar(select(Assignment).where(Assignment.student_id == world["student"].id, Assignment.practical_id == practical.id))

    # GitHub blob URL with .java extension
    blob_url = "https://github.com/student123/java-labs/blob/main/Practical1/Solution.java"
    sub = save_submission(db, assignment.id, blob_url, world["student"].user_id)
    assert sub.github_url == blob_url

    # Updating to a raw GitHub URL with .py extension
    raw_url = "https://raw.githubusercontent.com/student123/python-labs/main/lab1.py"
    sub2 = save_submission(db, assignment.id, raw_url, world["student"].user_id)
    assert sub2.github_url == raw_url


def test_commit_hash_kwarg_is_silently_ignored():
    """Passing commit_hash= should not raise; it's accepted but dropped."""
    db = setup_db()
    world = seed_world(db)
    practical = Practical(subject=world["subject"], practical_number=3, title="Commit Test", created_by=world["faculty"].id, submission_days=7)
    db.add(practical); db.commit()
    assign_practical(db, practical, world["faculty"].id)
    assignment = db.scalar(select(Assignment).where(Assignment.student_id == world["student"].id, Assignment.practical_id == practical.id))
    sub = save_submission(
        db, assignment.id, "https://github.com/example/repo",
        world["student"].user_id, commit_hash="abc1234",
    )
    assert sub is not None


def test_invalid_repository_url_is_rejected():
    db = setup_db()
    with pytest.raises(ValueError, match="valid GitHub URL"):
        save_submission(db, 999, "https://example.com/not-github", 1)


def test_partial_github_url_is_rejected():
    """A URL that looks like GitHub but isn't a valid repo or file path is rejected."""
    db = setup_db()
    with pytest.raises(ValueError, match="valid GitHub URL"):
        save_submission(db, 999, "https://github.com/", 1)


def test_invalid_grade_is_rejected():
    db = setup_db()
    with pytest.raises(ValueError, match="Grade must be one"):
        grade_submission(db, 999, 1, "A+", "Good", "Keep testing")


def test_assignment_uses_practical_submission_date():
    db = setup_db()
    world = seed_world(db)
    submission_date = date.today() + timedelta(days=10)
    practical = Practical(subject=world["subject"], practical_number=1, title="Build", created_by=world["faculty"].id, submission_date=submission_date)
    db.add(practical); db.commit()

    assign_practical(db, practical, world["faculty"].id)
    assignment = db.scalar(select(Assignment).where(Assignment.student_id == world["student"].id))
    assert assignment.deadline.date() == submission_date


def test_assign_faculty_subjects_and_scoping():
    db = setup_db()
    world = seed_world(db)
    assign_faculty_subjects(db, world["faculty"].id, [world["subject"].id], world["admin"].id)
    assert {subject.id for subject in subjects_for_faculty(db, world["faculty"].id)} == {world["subject"].id}
    assert subjects_for_faculty(db, world["other_student"].user_id) == []

    # replace the assignment set
    assign_faculty_subjects(db, world["faculty"].id, [world["other_subject"].id], world["admin"].id)
    assert {subject.id for subject in subjects_for_faculty(db, world["faculty"].id)} == {world["other_subject"].id}

    # idempotent
    assign_faculty_subjects(db, world["faculty"].id, [world["other_subject"].id], world["admin"].id)
    links = db.scalars(select(FacultySubject)).all()
    assert len(links) == 1


def test_faculty_only_sees_practicals_and_submissions_for_assigned_subjects():
    db = setup_db()
    world = seed_world(db)
    assign_faculty_subjects(db, world["faculty"].id, [world["subject"].id], world["admin"].id)
    my_practical = Practical(subject=world["subject"], practical_number=1, title="Mine", created_by=world["faculty"].id, submission_days=7)
    other_practical = Practical(subject=world["other_subject"], practical_number=1, title="Not mine", created_by=world["faculty"].id, submission_days=7)
    db.add_all([my_practical, other_practical]); db.commit()

    visible = faculty_practicals(db, world["faculty"].id)
    assert {p.title for p in visible} == {"Mine"}

    assign_practical(db, my_practical, world["faculty"].id)
    assignment = db.scalar(select(Assignment).where(Assignment.student_id == world["student"].id))
    save_submission(db, assignment.id, "https://github.com/example/repo", world["student"].user_id)
    queue = submissions_for_faculty(db, world["faculty"].id)
    assert {s.assignment.practical.title for s in queue} == {"Mine"}


def test_assign_practical_to_selected_students():
    db = setup_db()
    world = seed_world(db)
    practical = Practical(subject=world["subject"], practical_number=1, title="Build", created_by=world["faculty"].id, submission_days=7)
    db.add(practical); db.commit()
    created = assign_practical(db, practical, world["faculty"].id, student_ids=[world["student"].id])
    assert created == 1
    assignments = db.scalars(select(Assignment)).all()
    assert [a.student_id for a in assignments] == [world["student"].id]


def test_create_practical_stores_grade_and_increments_number():
    db = setup_db()
    world = seed_world(db)
    first = create_practical(db, world["subject"].id, "One", "d", "B", date.today(), world["faculty"].id)
    second = create_practical(db, world["subject"].id, "Two", "d", "A", date.today(), world["faculty"].id)
    assert (first.practical_number, second.practical_number) == (1, 2)
    assert first.grade == "B"
    assert second.grade == "A"


def test_invalid_grade_rejected_on_create():
    db = setup_db()
    world = seed_world(db)
    with pytest.raises(ValueError, match="Grade must be one"):
        create_practical(db, world["subject"].id, "One", "d", "A+", date.today(), world["faculty"].id)


def test_update_practical_changes_grade():
    db = setup_db()
    world = seed_world(db)
    practical = create_practical(db, world["subject"].id, "One", "d", "B", date.today(), world["faculty"].id)
    update_practical(db, practical.id, world["faculty"].id, grade="C", title="Updated")
    db.refresh(practical)
    assert practical.grade == "C"
    assert practical.title == "Updated"


def test_delete_practical_blocked_with_submissions():
    db = setup_db()
    world = seed_world(db)
    practical = create_practical(db, world["subject"].id, "Build", "d", "A", date.today(), world["faculty"].id)
    assign_practical(db, practical, world["faculty"].id)
    assignment = db.scalar(select(Assignment).where(Assignment.student_id == world["student"].id))
    save_submission(db, assignment.id, "https://github.com/example/repo", world["student"].user_id)
    with pytest.raises(ValueError, match="submissions"):
        delete_practical(db, practical.id, world["faculty"].id)


def test_delete_department_blocked_with_subjects():
    db = setup_db()
    world = seed_world(db)
    with pytest.raises(ValueError, match="subject"):
        delete_department(db, world["department"].id, world["admin"].id)


def test_delete_subject_blocked_with_practicals():
    db = setup_db()
    world = seed_world(db)
    create_practical(db, world["subject"].id, "Build", "d", "A", date.today(), world["faculty"].id)
    with pytest.raises(ValueError, match="practicals"):
        delete_subject(db, world["subject"].id, world["admin"].id)


def test_delete_subject_blocked_when_assigned_to_faculty():
    db = setup_db()
    world = seed_world(db)
    assign_faculty_subjects(db, world["faculty"].id, [world["subject"].id], world["admin"].id)
    with pytest.raises(ValueError, match="assigned to faculty"):
        delete_subject(db, world["subject"].id, world["admin"].id)


def test_delete_user_blocked_for_student_profile_and_self():
    db = setup_db()
    world = seed_world(db)
    with pytest.raises(ValueError, match="student profile"):
        delete_user(db, world["student"].user_id, world["admin"].id)
    with pytest.raises(ValueError, match="own account"):
        delete_user(db, world["admin"].id, world["admin"].id)


def test_validate_bulk_user_import_marks_missing_and_duplicate_rows():
    from models.schema import Program
    from services.core_services import create_program
    db = setup_db()
    world = seed_world(db)
    create_program(db, "MCA", "Master of Computer Applications", total_semesters=4, department_id=world["department"].id)

    rows = pd.DataFrame(
        [
            {"Enrollment No.": "250160450001", "Student Name": "Alice 12-digit", "Email": "250160450001.gvp@gujaratvidyapith.org", "Programme": "MCA", "Semester": 3},
            {"Enrollment No.": "210160450", "Student Name": "Dave 9-digit", "Email": "210160450.gvp@gujaratvidyapith.org", "Programme": "MCA", "Semester": 3},
            {"Enrollment No.": "", "Student Name": "Bob", "Email": "250160450002.gvp@gujaratvidyapith.org", "Programme": "MCA", "Semester": 3},
            {"Enrollment No.": "250160450001", "Student Name": "Alice 2", "Email": "250160450001.gvp@gujaratvidyapith.org", "Programme": "MCA", "Semester": 3},
            {"Enrollment No.": "12345", "Student Name": "Short", "Email": "12345.gvp@gujaratvidyapith.org", "Programme": "MCA", "Semester": 3},
            {"Enrollment No.": "1234567890", "Student Name": "TenDigits", "Email": "1234567890.gvp@gujaratvidyapith.org", "Programme": "MCA", "Semester": 3},
            {"Enrollment No.": "250160450003", "Student Name": "Charlie", "Email": "charlie@gmail.com", "Programme": "MCA", "Semester": 3},
        ]
    )

    preview = validate_bulk_user_import(rows, "student", db)

    assert preview[0]["ready"] is True
    assert preview[1]["ready"] is True
    assert preview[2]["ready"] is False
    assert "required" in preview[2]["reason"].lower()
    assert preview[3]["duplicate"] is True
    assert preview[4]["ready"] is False
    assert "9 or 12 digits" in preview[4]["reason"]
    assert preview[5]["ready"] is False
    assert "9 or 12 digits" in preview[5]["reason"]
    assert preview[6]["ready"] is False
    assert "gujaratvidyapith.org" in preview[6]["reason"]


def test_build_practical_import_template_returns_bytes():
    template = build_practical_import_template()
    assert len(template) > 0


def test_validate_practical_import_scopes_to_assigned_subjects():
    db = setup_db()
    world = seed_world(db)
    assign_faculty_subjects(db, world["faculty"].id, [world["subject"].id], world["admin"].id)
    rows = pd.DataFrame(
        [
            {"Subject Code": "CS1", "Practical Title": "Lab 1", "Description": "d", "Learning Outcome": "o", "Difficulty": "Medium", "Submission Date": ""},
            {"Subject Code": "CS2", "Practical Title": "Other", "Description": "d", "Learning Outcome": "o", "Difficulty": "Medium", "Submission Date": ""},
            {"Subject Code": "CS1", "Practical Title": "", "Description": "d", "Learning Outcome": "o", "Difficulty": "Medium", "Submission Date": ""},
        ]
    )
    preview = validate_practical_import(rows, db, world["faculty"].id)
    assert preview[0]["ready"] is True
    assert preview[1]["ready"] is False
    assert "not assigned" in preview[1]["error_message"]
    assert preview[2]["ready"] is False


def test_import_practicals_from_dataframe():
    db = setup_db()
    world = seed_world(db)
    assign_faculty_subjects(db, world["faculty"].id, [world["subject"].id], world["admin"].id)
    rows = pd.DataFrame(
        [
            {"Subject Code": "CS1", "Practical Title": "Lab 1", "Description": "d", "Learning Outcome": "o", "Difficulty": "Medium", "Submission Date": ""},
            {"Subject Code": "CS1", "Practical Title": "Lab 2", "Description": "d", "Learning Outcome": "o", "Difficulty": "Hard", "Submission Date": ""},
            {"Subject Code": "CS2", "Practical Title": "Other", "Description": "d", "Learning Outcome": "o", "Difficulty": "Easy", "Submission Date": ""},
        ]
    )
    summary = import_practicals_from_dataframe(rows, db, world["faculty"].id, world["faculty"].id)
    assert summary["imported"] == 2  # CS2 is not assigned so gets skipped
    assert summary["skipped"] == 1
    practicals = db.scalars(select(Practical)).all()
    titles = {p.title for p in practicals}
    assert titles == {"Lab 1", "Lab 2"}
    numbers = {p.practical_number for p in practicals}
    assert numbers == {1, 2}


def test_program_department_subject_student_linkages():
    from models.schema import Program
    from services.core_services import create_program, update_program, bulk_assign_subjects_to_program

    db = setup_db()
    world = seed_world(db)

    # 1. Create Program linked to Department
    prog = create_program(db, "MCA", "Master of Computer Applications", duration_months=24, total_semesters=4, department_id=world["department"].id)
    assert prog.department_id == world["department"].id
    assert prog.department.code == "CS"

    # 2. Update Program
    prog_updated = update_program(db, prog.id, world["admin"].id, name="MCA 2-Year", total_semesters=4)
    assert prog_updated.name == "MCA 2-Year"

    # 3. Bulk assign existing subjects to Program and Semester
    count = bulk_assign_subjects_to_program(db, [world["subject"].id, world["other_subject"].id], prog.id, semester=3)
    assert count == 2
    assert world["subject"].program_id == prog.id
    assert world["subject"].semester == 3
    assert world["other_subject"].program_id == prog.id

    # 4. Link student to program
    world["student"].program_id = prog.id
    world["student"].semester = 3
    db.commit()
    assert world["student"].program_ref.code == "MCA"

    # 5. Assign practical to specific student
    practical = Practical(subject=world["subject"], practical_number=1, title="Lab P1", created_by=world["faculty"].id, submission_days=7)
    db.add(practical); db.commit()

    # Assign only to world["student"]
    assigned_count = assign_practical(db, practical, world["faculty"].id, student_ids=[world["student"].id])
    assert assigned_count == 1
    assignments = db.scalars(select(Assignment).where(Assignment.practical_id == practical.id)).all()
    assert len(assignments) == 1
    assert assignments[0].student_id == world["student"].id


def test_student_bulk_import_simplified_format():
    from models.schema import Program, Student, User
    from services.core_services import create_program, import_bulk_users_from_dataframe

    db = setup_db()
    world = seed_world(db)
    prog = create_program(db, "MCA", "Master of Computer Applications", department_id=world["department"].id)

    rows = pd.DataFrame(
        [
            {"Enrollment No.": "250160450101", "Student Name": "Rohan Sharma", "Email": "250160450101.gvp@gujaratvidyapith.org", "Programme": "MCA", "Semester": 3},
            {"Enrollment No.": "250160450102", "Student Name": "Pooja Patel", "Email": "250160450102.gvp@gujaratvidyapith.org", "Programme": "MCA", "Semester": 3},
        ]
    )

    summary = import_bulk_users_from_dataframe(rows, "student", db, actor_id=world["admin"].id)
    assert summary["imported"] == 2
    assert summary["failed"] == 0

    student1 = db.scalar(select(Student).where(Student.enrollment_no == "250160450101"))
    assert student1 is not None
    assert student1.program_id == prog.id
    assert student1.user.full_name == "Rohan Sharma"
    assert student1.user.email == "250160450101.gvp@gujaratvidyapith.org"
    assert student1.program_id == prog.id
    assert student1.program == "MCA"
    assert student1.semester == 3


def test_late_submission_allowed_after_deadline():
    """A first-time submission after the deadline is accepted and flagged is_late=True."""
    db = setup_db()
    world = seed_world(db)
    # Create a practical whose deadline is already in the past
    past_deadline = datetime.utcnow() - timedelta(days=2)
    practical = Practical(
        subject=world["subject"],
        practical_number=10,
        title="Late Test",
        created_by=world["faculty"].id,
        submission_days=7,
    )
    db.add(practical); db.commit()
    assign_practical(db, practical, world["faculty"].id)

    # Force the assignment deadline to the past
    assignment = db.scalar(
        select(Assignment).where(
            Assignment.student_id == world["student"].id,
            Assignment.practical_id == practical.id,
        )
    )
    assignment.deadline = past_deadline
    db.commit()

    # Submission should succeed — no exception raised
    sub = save_submission(
        db, assignment.id,
        "https://github.com/example/late-repo",
        world["student"].user_id,
    )
    assert sub.is_late is True
    db.refresh(assignment)
    assert assignment.status == "Late"


def test_late_resubmission_updates_url():
    """Updating a submission after the deadline keeps is_late=True and updates the URL."""
    db = setup_db()
    world = seed_world(db)
    practical = Practical(
        subject=world["subject"],
        practical_number=11,
        title="Resubmit Late Test",
        created_by=world["faculty"].id,
        submission_days=7,
    )
    db.add(practical); db.commit()
    assign_practical(db, practical, world["faculty"].id)

    assignment = db.scalar(
        select(Assignment).where(
            Assignment.student_id == world["student"].id,
            Assignment.practical_id == practical.id,
        )
    )

    # First submit — on time
    save_submission(
        db, assignment.id,
        "https://github.com/example/on-time",
        world["student"].user_id,
    )

    # Move deadline to the past to simulate it passing
    assignment.deadline = datetime.utcnow() - timedelta(days=1)
    db.commit()

    # Update submission after deadline — should succeed and mark late
    sub = save_submission(
        db, assignment.id,
        "https://github.com/example/updated-late",
        world["student"].user_id,
    )
    assert sub.github_url == "https://github.com/example/updated-late"
    assert sub.is_late is True
    db.refresh(assignment)
    assert assignment.status == "Late"
