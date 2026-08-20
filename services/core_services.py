import secrets
from datetime import date, datetime, time, timedelta, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


from io import BytesIO
import re
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from services.auth_service import hash_password
from models.schema import (
    Assignment,
    AuditLog,
    Department,
    Permission,
    Evaluation,
    FacultySubject,
    Practical,
    Program,
    Role,
    Student,
    Subject,
    Submission,
    User,
)

# Accepts:
#   Repo root:  https://github.com/<user>/<repo>
#   File blob:  https://github.com/<user>/<repo>/blob/<branch>/<path/to/file.ext>
#   Raw file:   https://raw.githubusercontent.com/<user>/<repo>/<branch>/<path/to/file.ext>
GITHUB_REPO_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?$"
)
GITHUB_FILE_RE = re.compile(
    r"^https://(?:github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/blob/"
    r"|raw\.githubusercontent\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/)"
    r".+\.[A-Za-z0-9]+$"
)

def _is_valid_github_url(url: str) -> bool:
    """Return True when *url* is a GitHub repo root OR a direct file link."""
    u = url.strip()
    return bool(GITHUB_REPO_RE.match(u) or GITHUB_FILE_RE.match(u))


def _github_url_type(url: str) -> str:
    """Return 'file' if the URL points to a specific file, otherwise 'repo'."""
    u = url.strip()
    return "file" if GITHUB_FILE_RE.match(u) else "repo"


GVP_STUDENT_EMAIL_RE = re.compile(r"^(\d{9}|\d{12})\.gvp@gujaratvidyapith\.org$", re.IGNORECASE)
ENROLLMENT_NO_RE = re.compile(r"^(\d{9}|\d{12})$")
VALID_GRADES = {"A", "B", "C", "D", "E", "F"}
FACULTY_ROLE = "Faculty"


def audit(db: Session, actor_id: int | None, action: str, entity: str, entity_id: int | None = None, details: str = "") -> None:
    db.add(AuditLog(actor_id=actor_id, action=action, entity=entity, entity_id=entity_id, details=details))


def assign_practical(db: Session, practical: Practical, actor_id: int, student_ids: list[int] | None = None) -> int:
    """Create missing assignments for the given students (or every enrolled student)."""
    statement = select(Student)
    if student_ids:
        statement = statement.where(Student.id.in_(student_ids))
    students = db.scalars(statement).all()
    existing = set(db.scalars(select(Assignment.student_id).where(Assignment.practical_id == practical.id)))
    created = 0
    deadline = (
        datetime.combine(practical.submission_date, time.max)
        if practical.submission_date
        else utc_now() + timedelta(days=practical.submission_days)
    )
    for student in students:
        if student.id not in existing:
            db.add(Assignment(practical_id=practical.id, student_id=student.id, deadline=deadline))
            created += 1
    audit(db, actor_id, "ASSIGN_PRACTICAL", "Practical", practical.id, f"Assigned to {created} students")
    db.commit()
    return created


def save_submission(db: Session, assignment_id: int, github_url: str, actor_id: int, **fields) -> Submission:
    """Persist a practical submission.

    *github_url* may be:
    - A GitHub repository root URL  (https://github.com/<user>/<repo>)
    - A GitHub file blob URL        (https://github.com/<user>/<repo>/blob/<branch>/<file.ext>)
    - A raw GitHub file URL         (https://raw.githubusercontent.com/...)

    commit_hash is no longer required; it is silently ignored if passed in *fields*.
    """
    fields.pop("commit_hash", None)  # kept in DB schema for backward-compat; not required
    if not _is_valid_github_url(github_url):
        raise ValueError(
            "Enter a valid GitHub URL (repository root or a direct file link like "
            "https://github.com/user/repo/blob/main/Solution.java)."
        )
    assignment = db.scalar(select(Assignment).options(joinedload(Assignment.submission)).where(Assignment.id == assignment_id))
    if not assignment:
        raise ValueError("Assignment not found.")
    if assignment.submission is None:
        submission = Submission(assignment=assignment, github_url=github_url.strip(), is_late=utc_now() > assignment.deadline, **fields)
        db.add(submission)
    else:
        submission = assignment.submission
        submission.github_url = github_url.strip()
        submission.is_late = utc_now() > assignment.deadline  # recalculate on every update
        for key, value in fields.items():
            setattr(submission, key, value)
    assignment.status = "Late" if submission.is_late else "Submitted"
    url_type = _github_url_type(github_url)
    audit(db, actor_id, "SUBMIT_REPOSITORY", "Assignment", assignment.id, f"[{url_type}] {submission.github_url}")
    db.commit()
    db.refresh(submission)
    return submission


def grade_submission(db: Session, submission_id: int, evaluator_id: int, grade: str, remarks: str, suggestions: str) -> Evaluation:
    grade = grade.strip().upper()
    if grade not in VALID_GRADES:
        raise ValueError("Grade must be one of A, B, C, D, E, or F.")
    submission = db.get(Submission, submission_id)
    if not submission:
        raise ValueError("Submission not found.")
    evaluation = submission.evaluation or Evaluation(submission_id=submission.id)
    db.add(evaluation)
    evaluation.grade = grade
    evaluation.evaluator_id = evaluator_id
    evaluation.remarks = remarks
    evaluation.suggestions = suggestions
    evaluation.published = True
    submission.assignment.status = "Evaluated"
    audit(db, evaluator_id, "PUBLISH_EVALUATION", "Submission", submission.id, grade)
    db.commit()
    db.refresh(evaluation)
    return evaluation


def dashboard_counts(db: Session) -> dict[str, int | float]:
    counts = {
        "students": db.scalar(select(func.count(Student.id))) or 0,
        "assignments": db.scalar(select(func.count(Assignment.id))) or 0,
        "submissions": db.scalar(select(func.count(Submission.id))) or 0,
        "evaluated": db.scalar(select(func.count(Evaluation.id))) or 0,
    }
    counts["pending"] = counts["assignments"] - counts["submissions"]
    counts["late"] = db.scalar(select(func.count(Assignment.id)).where(Assignment.status == "Late")) or 0
    counts["average"] = float(db.scalar(select(func.avg(Evaluation.total_marks))) or 0)
    return counts


def ensure_role(db: Session, role_name: str) -> Role:
    role = db.scalar(select(Role).where(Role.name == role_name))
    if role is None:
        role = Role(name=role_name)
        db.add(role)
        db.flush()
    return role


def ensure_permission(db: Session, code: str, description: str = "") -> Permission:
    perm = db.scalar(select(Permission).where(Permission.code == code))
    if perm is None:
        perm = Permission(code=code, description=description)
        db.add(perm)
        db.flush()
    return perm


def grant_role_permission(db: Session, role: Role, permission: Permission) -> None:
    from models.schema import RolePermission

    exists = db.scalar(select(RolePermission).where(RolePermission.role_id == role.id, RolePermission.permission_id == permission.id))
    if not exists:
        db.add(RolePermission(role_id=role.id, permission_id=permission.id))
        db.flush()


def build_bulk_import_template(user_type: str) -> bytes:
    """Return an Excel template for the requested bulk-import type."""
    templates = {
        "student": pd.DataFrame([
            {
                "Enrollment No.": "250160450310",
                "Student Name": "Student Name",
                "Email": "250160450310.gvp@gujaratvidyapith.org",
                "Programme": "MCA",
                "Semester": 1,
            }
        ]),
        "faculty": pd.DataFrame([
            {"Faculty ID": "F1001", "Name": "Faculty Name", "Email": "faculty@example.com", "Mobile": "9876543210", "Department": "Computer Science", "Designation": "Assistant Professor"}
        ]),
        "admin": pd.DataFrame([
            {"Employee ID": "A1001", "Name": "Admin Name", "Email": "admin@example.com", "Mobile": "9876543210", "Role": "Administrator"}
        ]),
    }
    if user_type not in templates:
        raise ValueError("Unsupported import type")
    workbook = BytesIO()
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        templates[user_type].to_excel(writer, index=False, sheet_name="Import")
    return workbook.getvalue()


def validate_bulk_user_import(rows: pd.DataFrame, user_type: str, db: Session) -> list[dict[str, object]]:
    """Validate rows for bulk user import and return a preview-style list of records."""
    if user_type not in {"student", "faculty", "admin"}:
        raise ValueError("Unsupported import type")

    if user_type == "student":
        cols = set(rows.columns)
        has_enrollment = "Enrollment No." in cols or "Enrollment No" in cols or "Enrollment" in cols
        has_name = "Student Name" in cols or "Name" in cols
        has_email = "Email" in cols
        has_programme = "Programme" in cols or "Program" in cols or "Course" in cols
        has_semester = "Semester" in cols
        missing_cols = []
        if not has_enrollment:
            missing_cols.append("Enrollment No.")
        if not has_name:
            missing_cols.append("Student Name")
        if not has_email:
            missing_cols.append("Email")
        if not has_programme:
            missing_cols.append("Programme")
        if not has_semester:
            missing_cols.append("Semester")
        if missing_cols:
            raise ValueError(f"Missing columns: {', '.join(missing_cols)}")
    else:
        required_columns = {
            "faculty": ["Faculty ID", "Name", "Email", "Mobile", "Department", "Designation"],
            "admin": ["Employee ID", "Name", "Email", "Mobile", "Role"],
        }[user_type]
        missing_columns = [column for column in required_columns if column not in rows.columns]
        if missing_columns:
            raise ValueError(f"Missing columns: {', '.join(missing_columns)}")

    preview: list[dict[str, object]] = []
    seen_enrollment: set[str] = set()
    seen_email: set[str] = set()
    seen_employee: set[str] = set()

    # Build a lookup of valid programme codes and names for student import validation
    if user_type == "student":
        all_programs = list(db.scalars(select(Program)))
        valid_programme_codes = {p.code.lower() for p in all_programs}
        valid_programme_names = {p.name.lower() for p in all_programs}
        programme_by_identifier = {p.code.lower(): p for p in all_programs}
        programme_by_identifier.update({p.name.lower(): p for p in all_programs})
    else:
        all_programs = []
        valid_programme_codes = set()
        valid_programme_names = set()
        programme_by_identifier = {}

    for index, row in rows.fillna("").iterrows():
        record: dict[str, object] = {
            "row": index + 2,
            "status": "Ready",
            "reason": "Ready to import",
            "ready": True,
            "duplicate": False,
        }
        values = {key: str(value).strip() if isinstance(value, str) else str(value).strip() for key, value in row.items()}

        if not any(values.values()):
            record.update({"status": "Error", "reason": "Empty row", "ready": False})
            preview.append(record)
            continue

        if user_type == "student":
            enrollment = values.get("Enrollment No.") or values.get("Enrollment No") or values.get("Enrollment", "")
            student_name = values.get("Student Name") or values.get("Name", "")
            email = values.get("Email", "")
            programme = values.get("Programme") or values.get("Program") or values.get("Course", "")
            semester_raw = values.get("Semester", "")

            errors = []
            if not enrollment:
                errors.append("Missing Enrollment No. (required)")
            elif not ENROLLMENT_NO_RE.match(enrollment):
                errors.append(f"Enrollment No. '{enrollment}' must be 9 or 12 digits (e.g. 210160450 or 250160450310)")
            elif enrollment in seen_enrollment or db.scalar(select(User).where(User.username == enrollment)) is not None:
                record.update({"status": "Warning", "reason": "Duplicate Enrollment No", "ready": False, "duplicate": True})
            else:
                seen_enrollment.add(enrollment)

            if not student_name:
                errors.append("Missing Student Name")

            if not email:
                errors.append("Missing Email (required)")
            else:
                email_match = GVP_STUDENT_EMAIL_RE.match(email)
                if not email_match:
                    errors.append(f"Student email '{email}' must follow format '<9-or-12-digit-enrollment>.gvp@gujaratvidyapith.org'")
                elif enrollment and ENROLLMENT_NO_RE.match(enrollment) and email_match.group(1) != enrollment:
                    errors.append(f"Email enrollment number ({email_match.group(1)}) does not match Enrollment No. ({enrollment})")
                elif email in seen_email or db.scalar(select(User).where(User.email == email)) is not None:
                    record.update({"status": "Warning", "reason": "Duplicate Email", "ready": False, "duplicate": True})
                else:
                    seen_email.add(email)

            if not programme:
                errors.append("Missing Programme")
            elif programme.lower() not in valid_programme_codes and programme.lower() not in valid_programme_names:
                errors.append(
                    f"Programme '{programme}' does not exist. "
                    f"Please ask the administrator to add it under Master Data → Programmes first."
                )
            else:
                # validate semester against programme's total_semesters
                matched_prog = programme_by_identifier.get(programme.lower())
                if matched_prog and semester_raw:
                    try:
                        sem_int = int(float(str(semester_raw).strip()))
                        if sem_int > matched_prog.total_semesters:
                            errors.append(
                                f"Semester {sem_int} exceeds the total semesters ({matched_prog.total_semesters}) "
                                f"configured for '{matched_prog.code}'."
                            )
                    except (ValueError, TypeError):
                        pass  # caught below

            if not semester_raw:
                errors.append("Missing Semester")
            else:
                try:
                    sem_int = int(float(str(semester_raw).strip()))
                    if not 1 <= sem_int <= 12:
                        errors.append("Semester must be between 1 and 12")
                except (ValueError, TypeError):
                    errors.append("Semester must be a valid number")

            if errors:
                record.update({"status": "Error", "reason": "; ".join(errors), "ready": False})

        elif user_type == "faculty":
            faculty_id = values.get("Faculty ID", "")
            name = values.get("Name", "")
            email = values.get("Email", "")
            if not (faculty_id or name or email):
                record.update({"status": "Error", "reason": "Missing mandatory fields", "ready": False})
                preview.append(record)
                continue
            if not faculty_id:
                record.update({"status": "Error", "reason": "Missing Faculty ID (required)", "ready": False})
            elif faculty_id in seen_employee or db.scalar(select(User).where(User.username == faculty_id)) is not None:
                record.update({"status": "Warning", "reason": "Duplicate Faculty ID", "ready": False, "duplicate": True})
            else:
                seen_employee.add(faculty_id)
            if email in seen_email or db.scalar(select(User).where(User.email == email)) is not None:
                record.update({"status": "Warning", "reason": "Duplicate Email", "ready": False, "duplicate": True})
            else:
                seen_email.add(email)
        else:
            employee_id = values.get("Employee ID", "")
            name = values.get("Name", "")
            email = values.get("Email", "")
            if not (employee_id or name or email):
                record.update({"status": "Error", "reason": "Missing mandatory fields", "ready": False})
                preview.append(record)
                continue
            if not employee_id:
                record.update({"status": "Error", "reason": "Missing Employee ID (required)", "ready": False})
            elif employee_id in seen_employee or db.scalar(select(User).where(User.username == employee_id)) is not None:
                record.update({"status": "Warning", "reason": "Duplicate Employee ID", "ready": False, "duplicate": True})
            else:
                seen_employee.add(employee_id)
            if email in seen_email or db.scalar(select(User).where(User.email == email)) is not None:
                record.update({"status": "Warning", "reason": "Duplicate Email", "ready": False, "duplicate": True})
            else:
                seen_email.add(email)

        preview.append(record)

    return preview


def import_bulk_users_from_dataframe(rows: pd.DataFrame, user_type: str, db: Session, actor_id: int) -> dict[str, int]:
    """Create users from a validated import sheet. Returns a summary dictionary."""
    preview = validate_bulk_user_import(rows, user_type, db)
    summary = {"imported": 0, "updated": 0, "skipped": 0, "failed": 0}
    role_name = {"student": "Student", "faculty": "Faculty", "admin": "Administrator"}[user_type]
    role = ensure_role(db, role_name)
    for index, row in rows.fillna("").iterrows():
        if not preview[index].get("ready", False):
            summary["skipped"] += 1
            continue
        values = {key: str(value).strip() if isinstance(value, str) else str(value).strip() for key, value in row.items()}
        try:
            if user_type == "student":
                username = values.get("Enrollment No.") or values.get("Enrollment No") or values.get("Enrollment", "")
                full_name = values.get("Student Name") or values.get("Name", "")
            elif user_type == "faculty":
                username = values.get("Faculty ID") or values.get("Email", "")
                full_name = values.get("Name", "")
            else:
                username = values.get("Employee ID") or values.get("Email", "")
                full_name = values.get("Name", "")

            email = values.get("Email", "")
            password = f"Tmp!{secrets.token_urlsafe(8)}"
            if not username or not email or not full_name:
                summary["failed"] += 1
                continue
            existing = db.scalar(select(User).where((User.username == username) | (User.email == email)))
            if existing:
                summary["skipped"] += 1
                continue
            user = User(
                username=username,
                full_name=full_name,
                email=email,
                password_hash=hash_password(password),
                role_id=role.id,
                is_active=True,
            )
            db.add(user)
            db.flush()
            if user_type == "student":
                enrollment_no = username
                semester_raw = values.get("Semester", 1)
                try:
                    semester = int(float(str(semester_raw).strip()))
                except (ValueError, TypeError):
                    semester = 1
                course_val = values.get("Programme") or values.get("Program") or values.get("Course", "MCA")
                prog = db.scalar(select(Program).where((Program.code == course_val) | (Program.name == course_val)))
                prog_id = prog.id if prog else None
                prog_code = prog.code if prog else course_val
                student = Student(user_id=user.id, enrollment_no=enrollment_no, semester=semester, program=prog_code, program_id=prog_id)
                db.add(student)
            audit(db, actor_id, "BULK_IMPORT", "User", user.id, f"{user_type}:{username}")
            summary["imported"] += 1
        except Exception:
            db.rollback()
            summary["failed"] += 1
            break
    db.commit()
    return summary


def build_subject_import_template() -> bytes:
    """Return an Excel template for bulk subject import."""
    template = pd.DataFrame([
        {
            "Subject Code": "010101010101",
            "Subject Name": "Database Management Systems",
            "Semester": 3,
            "Course": "MCA",
            "Department": "Computer Science",
            "Credits": 4,
            "Subject Type": "Theory",
            "Status": "Active",
        }
    ])
    workbook = BytesIO()
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        template.to_excel(writer, index=False, sheet_name="Subjects")
    return workbook.getvalue()


def validate_subject_import(rows: pd.DataFrame, db: Session) -> list[dict[str, object]]:
    """Validate subject import rows and return preview-style records."""
    required_columns = ["Subject Code", "Subject Name", "Semester", "Course", "Department", "Credits", "Subject Type", "Status"]
    missing_columns = [column for column in required_columns if column not in rows.columns]
    if missing_columns:
        raise ValueError(f"Missing columns: {', '.join(missing_columns)}")

    preview: list[dict[str, object]] = []
    seen_codes: set[str] = set()
    seen_names: set[str] = set()
    department_lookup = {item.code.lower(): item.id for item in db.scalars(select(Department))} | {item.name.lower(): item.id for item in db.scalars(select(Department))}
    program_lookup = {item.code.lower(): item.id for item in db.scalars(select(Program))} | {item.name.lower(): item.id for item in db.scalars(select(Program))}

    for index, row in rows.fillna("").iterrows():
        values = {key: str(value).strip() if isinstance(value, str) else str(value).strip() for key, value in row.items()}
        record: dict[str, object] = {
            "row": index + 2,
            "subject_code": values.get("Subject Code", ""),
            "subject_name": values.get("Subject Name", ""),
            "semester": values.get("Semester", ""),
            "course": values.get("Course", ""),
            "department": values.get("Department", ""),
            "credits": values.get("Credits", ""),
            "subject_type": values.get("Subject Type", ""),
            "status": values.get("Status", ""),
            "validation_status": "Valid",
            "error_message": "",
            "ready": True,
        }
        if not any(values.values()):
            record.update({"validation_status": "Error", "error_message": "Empty row", "ready": False})
            preview.append(record)
            continue

        code = values.get("Subject Code", "")
        name = values.get("Subject Name", "")
        semester = values.get("Semester", "")
        course = values.get("Course", "")
        department = values.get("Department", "")
        credits = values.get("Credits", "")
        subject_type = values.get("Subject Type", "")
        status = values.get("Status", "")

        errors: list[str] = []
        if not code:
            errors.append("Subject Code is required")
        elif not re.fullmatch(r"\d{12}", code):
            errors.append("Subject Code must be exactly 12 numeric digits")
        elif code in seen_codes or db.scalar(select(Subject).where(Subject.code == code)) is not None:
            errors.append("Subject Code already exists")
        else:
            seen_codes.add(code)

        if not name:
            errors.append("Subject Name is required")
        elif len(name) > 200:
            errors.append("Subject Name exceeds 200 characters")
        elif name.lower() in seen_names or db.scalar(select(Subject).where(Subject.name == name)) is not None:
            errors.append("Subject Name already exists")
        else:
            seen_names.add(name.lower())

        try:
            semester_int = int(str(semester).strip())
        except ValueError:
            semester_int = None
            errors.append("Semester must be an integer")
        if semester_int is not None and not 1 <= semester_int <= 10:
            errors.append("Semester must be between 1 and 10")

        if not course:
            errors.append("Course is required")
        elif course.lower() not in program_lookup:
            errors.append(f"Programme/Course '{course}' does not exist")

        if not department:
            errors.append("Department is required")
        elif department.lower() not in department_lookup:
            errors.append("Department does not exist")

        if credits not in {"", None}:
            try:
                credits_value = float(str(credits).strip())
            except ValueError:
                credits_value = None
                errors.append("Credits must be numeric")
            if credits_value is not None and not 0 <= credits_value <= 20:
                errors.append("Credits must be between 0 and 20")

        if subject_type not in {"Theory", "Practical", "Theory + Practical", "Project", "Internship", "Elective"}:
            errors.append("Invalid Subject Type")

        if status not in {"Active", "Inactive"}:
            errors.append("Invalid Status")

        if errors:
            record.update({"validation_status": "Error", "error_message": "; ".join(errors), "ready": False})
        else:
            record.update({"validation_status": "Valid", "error_message": "", "ready": True})

        preview.append(record)

    return preview


def import_subjects_from_dataframe(rows: pd.DataFrame, db: Session, actor_id: int, mode: str = "insert") -> dict[str, int]:
    """Import validated subject rows using a transaction-safe workflow."""
    preview = validate_subject_import(rows, db)
    summary = {"imported": 0, "updated": 0, "skipped": 0, "failed": 0}
    try:
        with db.begin():
            for index, row in rows.fillna("").iterrows():
                record = preview[index]
                if not record.get("ready", False):
                    summary["skipped"] += 1
                    continue
                values = {key: str(value).strip() if isinstance(value, str) else str(value).strip() for key, value in row.items()}
                department = db.scalar(select(Department).where((Department.name == values.get("Department", "")) | (Department.code == values.get("Department", ""))))
                program = db.scalar(select(Program).where((Program.code == values.get("Course", "")) | (Program.name == values.get("Course", ""))))
                if not department and program and program.department:
                    department = program.department
                if not department:
                    summary["failed"] += 1
                    continue
                program_id = program.id if program else None
                existing = db.scalar(select(Subject).where(Subject.code == values.get("Subject Code", "")))
                if existing:
                    if mode == "update":
                        existing.name = values.get("Subject Name", "")
                        existing.semester = int(values.get("Semester", 1))
                        existing.department_id = department.id
                        existing.program_id = program_id
                        existing.credits = float(values.get("Credits", 0)) if values.get("Credits", "") not in {"", None} else None
                        existing.subject_type = values.get("Subject Type", "Theory")
                        existing.status = values.get("Status", "Active")
                        summary["updated"] += 1
                    else:
                        summary["skipped"] += 1
                        continue
                else:
                    db.add(
                        Subject(
                            code=values.get("Subject Code", ""),
                            name=values.get("Subject Name", ""),
                            semester=int(values.get("Semester", 1)),
                            department_id=department.id,
                            program_id=program_id,
                            credits=float(values.get("Credits", 0)) if values.get("Credits", "") not in {"", None} else None,
                            subject_type=values.get("Subject Type", "Theory"),
                            status=values.get("Status", "Active"),
                        )
                    )
                    summary["imported"] += 1
                audit(db, actor_id, "BULK_IMPORT_SUBJECTS", "Subject", None, f"Imported {values.get('Subject Code', '')}")
            db.flush()
    except IntegrityError:
        db.rollback()
        summary["failed"] += 1
    return summary


# --------------------------------------------------------------------------
# Subject-wise faculty assignment
# --------------------------------------------------------------------------


def subjects_for_faculty(db: Session, faculty_id: int) -> list[Subject]:
    """Subjects explicitly assigned to a faculty member, ordered by code."""
    return list(
        db.scalars(
            select(Subject)
            .join(FacultySubject, FacultySubject.subject_id == Subject.id)
            .where(FacultySubject.faculty_id == faculty_id)
            .order_by(Subject.code)
        )
    )


def faculty_subject_ids(db: Session, faculty_id: int) -> set[int]:
    return {item[0] for item in db.execute(select(FacultySubject.subject_id).where(FacultySubject.faculty_id == faculty_id)).all()}


def assign_faculty_subjects(db: Session, faculty_id: int, subject_ids: list[int], actor_id: int) -> None:
    """Replace a faculty member's subject assignments with the given set (subject-wise Faculty)."""
    subject_ids = list(dict.fromkeys(subject_ids))  # de-duplicate, keep order
    current = faculty_subject_ids(db, faculty_id)
    to_add = [subject_id for subject_id in subject_ids if subject_id not in current]
    to_remove = [subject_id for subject_id in current if subject_id not in subject_ids]
    for subject_id in to_add:
        db.add(FacultySubject(faculty_id=faculty_id, subject_id=subject_id, assigned_by=actor_id))
    for subject_id in to_remove:
        link = db.scalar(
            select(FacultySubject).where(FacultySubject.faculty_id == faculty_id, FacultySubject.subject_id == subject_id)
        )
        if link:
            db.delete(link)
    if to_add or to_remove:
        audit(db, actor_id, "UPDATE_FACULTY_SUBJECTS", "User", faculty_id, f"Subjects: +{to_add} -{to_remove}")
        db.commit()


# --------------------------------------------------------------------------
# Practical management (faculty-facing, scoped to assigned subjects)
# --------------------------------------------------------------------------


def next_practical_number(db: Session, subject_id: int) -> int:
    current = db.scalar(
        select(func.max(Practical.practical_number)).where(Practical.subject_id == subject_id)
    )
    return (current or 0) + 1


def create_practical(
    db: Session,
    subject_id: int,
    title: str,
    description: str,
    grade: str,
    submission_date: date,
    creator_id: int,
    learning_outcome: str = "",
    difficulty: str = "Medium",
) -> Practical:
    grade = grade.strip().upper()
    if grade not in VALID_GRADES:
        raise ValueError("Grade must be one of A, B, C, D, E, or F.")
    practical = Practical(
        subject_id=subject_id,
        practical_number=next_practical_number(db, subject_id),
        title=title.strip(),
        description=description,
        learning_outcome=learning_outcome,
        difficulty=difficulty,
        max_marks=100,
        grade=grade,
        submission_date=submission_date,
        created_by=creator_id,
    )
    db.add(practical)
    audit(db, creator_id, "CREATE_PRACTICAL", "Practical", None, f"{subject_id}:{practical.title}")
    db.commit()
    db.refresh(practical)
    return practical


def update_practical(db: Session, practical_id: int, actor_id: int, **fields) -> Practical:
    practical = db.get(Practical, practical_id)
    if not practical:
        raise ValueError("Practical not found.")
    # Submission date is intentionally immutable after creation once assignments exist.
    for key in ("title", "description", "learning_outcome", "difficulty", "max_marks"):
        if key in fields:
            setattr(practical, key, fields[key])
    if "grade" in fields:
        grade = fields["grade"].strip().upper()
        if grade not in VALID_GRADES:
            raise ValueError("Grade must be one of A, B, C, D, E, or F.")
        practical.grade = grade
    if "subject_id" in fields and fields["subject_id"] != practical.subject_id:
        raise ValueError("A practical cannot be moved to another subject after creation.")
    audit(db, actor_id, "UPDATE_PRACTICAL", "Practical", practical.id, f"{practical.subject_id}:{practical.title}")
    db.commit()
    db.refresh(practical)
    return practical


def delete_practical(db: Session, practical_id: int, actor_id: int) -> None:
    practical = db.get(Practical, practical_id, options=[joinedload(Practical.assignments)])
    if not practical:
        raise ValueError("Practical not found.")
    for assignment in practical.assignments:
        if assignment.submission:
            raise ValueError("Cannot delete a practical that already has student submissions.")
    audit(db, actor_id, "DELETE_PRACTICAL", "Practical", practical.id, f"{practical.subject_id}:{practical.title}")
    db.delete(practical)
    db.commit()


# --------------------------------------------------------------------------
# Bulk practical import (Faculty, scoped to their assigned subjects)
# --------------------------------------------------------------------------

# Only these two columns are strictly required; the rest are optional
PRACTICAL_IMPORT_REQUIRED_COLUMNS = ["Subject Code", "Practical Title"]
PRACTICAL_IMPORT_COLUMNS = ["Subject Code", "Practical Title"]  # kept for backward compat alias


def build_practical_import_template() -> bytes:
    """Return an Excel template for bulk practical import.

    Required columns: Subject Code, Practical Title.
    Optional columns: Description, Learning Outcome, Difficulty, Submission Date.
    """
    template = pd.DataFrame(
        [
            {
                "Subject Code": "CS301",
                "Practical Title": "Build a REST API",
                "Description": "Implement and document a small REST API.",
                "Learning Outcome": "Apply API design principles.",
                "Difficulty": "Medium",
                "Submission Date": "2024-12-31",
            },
            {
                "Subject Code": "CS301",
                "Practical Title": "Database Schema Design",
                "Description": "Design and normalize a relational schema.",
                "Learning Outcome": "Apply normalization principles.",
                "Difficulty": "Hard",
                "Submission Date": "",
            },
        ]
    )
    workbook = BytesIO()
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        template.to_excel(writer, index=False, sheet_name="Practicals")
    return workbook.getvalue()


def validate_practical_import(rows: pd.DataFrame, db: Session, faculty_id: int) -> list[dict[str, object]]:
    """Validate bulk practical rows, scoped to the faculty member's assigned subjects.

    Required columns: Subject Code, Practical Title.
    Optional columns: Description, Learning Outcome, Difficulty, Submission Date.
    Returns preview-style records with a ready flag and error messages.
    """
    missing_columns = [column for column in PRACTICAL_IMPORT_REQUIRED_COLUMNS if column not in rows.columns]
    if missing_columns:
        raise ValueError(f"Missing columns: {', '.join(missing_columns)}")

    assigned = faculty_subject_ids(db, faculty_id)
    subject_map = {subject.code: subject.id for subject in db.scalars(select(Subject))}
    subject_owner = {subject.code: subject.id for subject in db.scalars(select(Subject)) if subject.id in assigned}

    preview: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()  # (subject_code, title) to catch duplicates within the sheet

    for index, row in rows.fillna("").iterrows():
        values = {key: str(value).strip() if isinstance(value, str) else str(value).strip() for key, value in row.items()}
        subject_code = values.get("Subject Code", "")
        title = values.get("Practical Title", "")
        difficulty = values.get("Difficulty", "Medium") or "Medium"
        submission_date_raw = values.get("Submission Date", "")

        record: dict[str, object] = {
            "row": index + 2,
            "subject_code": subject_code,
            "title": title,
            "difficulty": difficulty,
            "submission_date": submission_date_raw,
            "validation_status": "Valid",
            "error_message": "",
            "ready": True,
        }

        if not any(values.values()):
            record.update({"validation_status": "Error", "error_message": "Empty row", "ready": False})
            preview.append(record)
            continue

        errors: list[str] = []

        if not subject_code:
            errors.append("Subject Code is required")
        elif subject_code not in subject_map:
            errors.append("Subject does not exist in the system")
        elif subject_code not in subject_owner:
            errors.append(f"Subject '{subject_code}' is not assigned to you")

        if not title:
            errors.append("Practical Title is required")
        elif len(title) > 200:
            errors.append("Practical Title exceeds 200 characters")

        if difficulty and difficulty not in {"Easy", "Medium", "Hard"}:
            errors.append("Difficulty must be Easy, Medium, or Hard (leave blank for Medium)")

        if submission_date_raw:
            try:
                date.fromisoformat(submission_date_raw)
            except ValueError:
                errors.append("Submission Date must be in YYYY-MM-DD format (e.g. 2024-12-31) or left blank")

        # duplicate detection within the uploaded sheet and in the database
        if subject_code and title:
            key = (subject_code, title)
            if key in seen:
                errors.append("Duplicate practical in this sheet")
            else:
                seen.add(key)

            if subject_code in subject_owner:
                subj_id = subject_owner[subject_code]
                exists_in_db = db.scalar(
                    select(Practical.id).where(Practical.subject_id == subj_id, Practical.title == title)
                )
                if exists_in_db is not None:
                    errors.append(f"Practical '{title}' already exists for subject '{subject_code}'")

        if errors:
            record.update({"validation_status": "Error", "error_message": "; ".join(errors), "ready": False})
        preview.append(record)

    return preview


def import_practicals_from_dataframe(rows: pd.DataFrame, db: Session, actor_id: int, faculty_id: int) -> dict[str, int]:
    """Import validated practical rows scoped to the faculty member's assigned subjects.

    Auto-assigns the next practical_number per subject and sets created_by to the faculty member.
    Grade defaults to 'O' (Outstanding) — can be updated per-submission during evaluation.
    """
    preview = validate_practical_import(rows, db, faculty_id)
    summary = {"imported": 0, "failed": 0, "skipped": 0}
    subject_by_code = {subject.code: subject.id for subject in db.scalars(select(Subject))}
    
    # Track next practical_number per subject_id in memory to prevent collision during batch inserts
    next_num_tracker: dict[int, int] = {}

    for index, row in rows.fillna("").iterrows():
        record = preview[index]
        if not record.get("ready", False):
            summary["skipped"] += 1
            continue
        values = {key: str(value).strip() if isinstance(value, str) else str(value).strip() for key, value in row.items()}
        subject_code = values.get("Subject Code", "")
        subject_id = subject_by_code.get(subject_code)
        if subject_id is None:
            summary["failed"] += 1
            continue
        title = values.get("Practical Title", "")
        # Check DB duplicate guard
        existing = db.scalar(select(Practical.id).where(Practical.subject_id == subject_id, Practical.title == title))
        if existing is not None:
            summary["skipped"] += 1
            continue

        difficulty = values.get("Difficulty", "").strip() or "Medium"
        if difficulty not in {"Easy", "Medium", "Hard"}:
            difficulty = "Medium"
        submission_date_raw = values.get("Submission Date", "")
        submission_date = None
        if submission_date_raw:
            try:
                submission_date = date.fromisoformat(str(submission_date_raw).strip())
            except ValueError:
                submission_date = None

        if subject_id not in next_num_tracker:
            next_num_tracker[subject_id] = next_practical_number(db, subject_id)
        current_pnum = next_num_tracker[subject_id]
        next_num_tracker[subject_id] += 1

        practical = Practical(
            subject_id=subject_id,
            practical_number=current_pnum,
            title=title,
            description=values.get("Description", ""),
            learning_outcome=values.get("Learning Outcome", ""),
            difficulty=difficulty,
            max_marks=100,
            grade="A",  # default grade; updated by faculty during evaluation
            submission_days=14,     # default deadline window; no longer imported from sheet
            submission_date=submission_date,
            created_by=actor_id,
        )
        db.add(practical)
        db.flush()
        audit(db, actor_id, "BULK_IMPORT_PRACTICALS", "Practical", practical.id, f"{subject_code}:{title}")
        summary["imported"] += 1
    db.commit()
    return summary


# --------------------------------------------------------------------------
# Guarded master-data deletion (Administrator)
# --------------------------------------------------------------------------


def delete_department(db: Session, department_id: int, actor_id: int) -> None:
    department = db.get(Department, department_id)
    if not department:
        raise ValueError("Department not found.")
    subject_count = db.scalar(select(func.count(Subject.id)).where(Subject.department_id == department_id)) or 0
    if subject_count:
        raise ValueError(f"Cannot delete a department that still has {subject_count} subject(s). Move or delete them first.")
    audit(db, actor_id, "DELETE_DEPARTMENT", "Department", department.id, department.code)
    db.delete(department)
    db.commit()


def delete_subject(db: Session, subject_id: int, actor_id: int) -> None:
    subject = db.get(Subject, subject_id)
    if not subject:
        raise ValueError("Subject not found.")
    practical_count = db.scalar(select(func.count(Practical.id)).where(Practical.subject_id == subject_id)) or 0
    if practical_count:
        raise ValueError("Cannot delete a subject that still has practicals. Delete its practicals first.")
    faculty_count = db.scalar(select(func.count(FacultySubject.id)).where(FacultySubject.subject_id == subject_id)) or 0
    if faculty_count:
        raise ValueError("Cannot delete a subject that is assigned to faculty. Unassign it first.")
    audit(db, actor_id, "DELETE_SUBJECT", "Subject", subject.id, subject.code)
    db.delete(subject)
    db.commit()


def delete_user(db: Session, user_id: int, actor_id: int) -> None:
    if user_id == actor_id:
        raise ValueError("You cannot delete your own account.")
    user = db.get(User, user_id)
    if not user:
        raise ValueError("User not found.")
    practical_count = db.scalar(select(func.count(Practical.id)).where(Practical.created_by == user_id)) or 0
    if practical_count:
        raise ValueError("Cannot delete a faculty member who created practicals. Deactivate the account instead.")
    if user.student:
        raise ValueError("Cannot delete a user with a linked student profile. Deactivate the account instead.")
    evaluation_count = db.scalar(select(func.count(Evaluation.id)).where(Evaluation.evaluator_id == user_id)) or 0
    if evaluation_count:
        raise ValueError("Cannot delete a user who published evaluations. Deactivate the account instead.")
    audit(db, actor_id, "DELETE_USER", "User", user.id, user.username)
    db.delete(user)
    db.commit()


# --------------------------------------------------------------------------
# Programme (course) master data management (Administrator)
# --------------------------------------------------------------------------


def create_program(
    db: Session,
    code: str,
    name: str,
    duration_months: int = 24,
    total_semesters: int = 4,
    department_id: int | None = None,
    actor_id: int = 0,
) -> Program:
    """Create a new programme (course), e.g. BCA, MCA, M.Sc.IT, PGDCA."""
    code = code.strip().upper()
    name = name.strip()
    if not code or not name:
        raise ValueError("Programme code and name are required.")
    existing = db.scalar(select(Program).where((Program.code == code) | (Program.name == name)))
    if existing:
        raise ValueError("A programme with that code or name already exists.")
    program = Program(
        code=code,
        name=name,
        duration_months=int(duration_months),
        total_semesters=int(total_semesters),
        department_id=department_id,
    )
    db.add(program)
    audit(db, actor_id, "CREATE_PROGRAM", "Program", None, f"{code}:{name}")
    db.commit()
    db.refresh(program)
    return program


def update_program(db: Session, program_id: int, actor_id: int, **fields) -> Program:
    """Update an existing programme's code/name/duration/department fields."""
    program = db.get(Program, program_id)
    if not program:
        raise ValueError("Programme not found.")
    if "code" in fields:
        new_code = fields["code"].strip().upper()
        if not new_code:
            raise ValueError("Programme code is required.")
        dup = db.scalar(select(Program).where(Program.code == new_code, Program.id != program_id))
        if dup:
            raise ValueError("That programme code is already in use.")
        program.code = new_code
    if "name" in fields:
        new_name = fields["name"].strip()
        if not new_name:
            raise ValueError("Programme name is required.")
        dup = db.scalar(select(Program).where(Program.name == new_name, Program.id != program_id))
        if dup:
            raise ValueError("That programme name is already in use.")
        program.name = new_name
    if "duration_months" in fields:
        program.duration_months = int(fields["duration_months"])
    if "total_semesters" in fields:
        program.total_semesters = int(fields["total_semesters"])
    if "department_id" in fields:
        program.department_id = fields["department_id"]
    audit(db, actor_id, "UPDATE_PROGRAM", "Program", program.id, f"{program.code}:{program.name}")
    db.commit()
    db.refresh(program)
    return program


def bulk_assign_subjects_to_program(
    db: Session, subject_ids: list[int], program_id: int, semester: int | None = None, actor_id: int = 0
) -> int:
    """Assign multiple subjects to a given programme and optionally semester."""
    program = db.get(Program, program_id)
    if not program:
        raise ValueError("Programme not found.")
    updated_count = 0
    for sid in subject_ids:
        subject = db.get(Subject, sid)
        if subject:
            subject.program_id = program_id
            if program.department_id:
                subject.department_id = program.department_id
            if semester is not None and semester > 0:
                subject.semester = int(semester)
            updated_count += 1
    if updated_count > 0:
        audit(db, actor_id, "BULK_ASSIGN_SUBJECTS_PROGRAM", "Program", program_id, f"Assigned {updated_count} subjects to {program.code}")
        db.commit()
    return updated_count


def delete_program(db: Session, program_id: int, actor_id: int) -> None:
    """Delete a programme. Cannot delete a programme with enrolled students."""
    program = db.get(Program, program_id)
    if not program:
        raise ValueError("Programme not found.")
    student_count = db.scalar(select(func.count(Student.id)).where(Student.program_id == program_id)) or 0
    if student_count:
        raise ValueError(f"Cannot delete a programme that has {student_count} enrolled student(s).")
    audit(db, actor_id, "DELETE_PROGRAM", "Program", program.id, f"{program.code}:{program.name}")
    db.delete(program)
    db.commit()


# --------------------------------------------------------------------------
# Faculty-scoped evaluation queue
# --------------------------------------------------------------------------


def submissions_for_faculty(db: Session, faculty_id: int, subject_id: int | None = None) -> list[Submission]:
    """Unpublished (or published) submissions belonging to practicals on subjects assigned to the faculty member."""
    statement = (
        select(Submission)
        .join(Assignment, Assignment.id == Submission.assignment_id)
        .join(Practical, Practical.id == Assignment.practical_id)
        .join(FacultySubject, FacultySubject.subject_id == Practical.subject_id)
        .where(FacultySubject.faculty_id == faculty_id, Assignment.status.in_(["Submitted", "Late"]))
    )
    if subject_id:
        statement = statement.where(Practical.subject_id == subject_id)
    return list(db.scalars(statement.order_by(Submission.submitted_at)))


def faculty_practicals(db: Session, faculty_id: int, subject_id: int | None = None) -> list[Practical]:
    """Practicals on the subjects assigned to the faculty member."""
    statement = (
        select(Practical)
        .join(FacultySubject, FacultySubject.subject_id == Practical.subject_id)
        .where(FacultySubject.faculty_id == faculty_id)
        .order_by(FacultySubject.subject_id, Practical.practical_number)
    )
    if subject_id:
        statement = statement.where(Practical.subject_id == subject_id)
    return list(db.scalars(statement))
