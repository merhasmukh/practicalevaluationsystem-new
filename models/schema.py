from datetime import date, datetime, timezone
from decimal import Decimal
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)



class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(30), unique=True)
    users: Mapped[list["User"]] = relationship(back_populates="role")


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    subjects: Mapped[list["Subject"]] = relationship(back_populates="department")
    programs: Mapped[list["Program"]] = relationship(back_populates="department")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    account_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    signature_image_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[Role] = relationship(back_populates="users")
    student: Mapped["Student | None"] = relationship(back_populates="user", uselist=False)
    faculty_subjects: Mapped[list["FacultySubject"]] = relationship(
        back_populates="faculty", foreign_keys="FacultySubject.faculty_id"
    )


class Student(Base):
    __tablename__ = "students"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    enrollment_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    semester: Mapped[int] = mapped_column(Integer)
    program: Mapped[str] = mapped_column(String(80), default="MCA")
    program_id: Mapped[int | None] = mapped_column(ForeignKey("programs.id"), nullable=True)
    user: Mapped[User] = relationship(back_populates="student")
    program_ref: Mapped["Program | None"] = relationship(back_populates="students")
    assignments: Mapped[list["Assignment"]] = relationship(back_populates="student")


class Program(Base):
    __tablename__ = "programs"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    duration_months: Mapped[int] = mapped_column(Integer, default=24)
    total_semesters: Mapped[int] = mapped_column(Integer, default=4)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    department: Mapped[Department | None] = relationship(back_populates="programs")
    students: Mapped[list["Student"]] = relationship(back_populates="program_ref")
    subjects: Mapped[list["Subject"]] = relationship(back_populates="program")


class Subject(Base):
    __tablename__ = "subjects"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    semester: Mapped[int] = mapped_column(Integer)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"))
    program_id: Mapped[int | None] = mapped_column(ForeignKey("programs.id"), nullable=True)
    department: Mapped[Department] = relationship(back_populates="subjects")
    program: Mapped[Program | None] = relationship(back_populates="subjects")
    practicals: Mapped[list["Practical"]] = relationship(back_populates="subject")
    faculty_subjects: Mapped[list["FacultySubject"]] = relationship(
        back_populates="subject", foreign_keys="FacultySubject.subject_id"
    )
    header_config: Mapped["SubjectHeaderConfig | None"] = relationship(
        back_populates="subject", uselist=False
    )


class FacultySubject(Base):
    __tablename__ = "faculty_subjects"
    id: Mapped[int] = mapped_column(primary_key=True)
    faculty_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    assigned_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    faculty: Mapped[User] = relationship(back_populates="faculty_subjects", foreign_keys=[faculty_id])
    subject: Mapped[Subject] = relationship(back_populates="faculty_subjects", foreign_keys=[subject_id])
    __table_args__ = (UniqueConstraint("faculty_id", "subject_id"),)


class Practical(Base):
    __tablename__ = "practicals"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    practical_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    learning_outcome: Mapped[str] = mapped_column(Text, default="")
    difficulty: Mapped[str] = mapped_column(String(20), default="Medium")
    max_marks: Mapped[int] = mapped_column(Integer, default=100)
    grade: Mapped[str] = mapped_column(String(3), default="A")
    submission_days: Mapped[int] = mapped_column(Integer, default=7)
    submission_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # PDF display fields — nullable, added non-breaking
    unit_name: Mapped[str | None] = mapped_column(String(100), nullable=True)       # e.g. "Unit-1 & Unit-2"
    practical_code: Mapped[str | None] = mapped_column(String(30), nullable=True)   # e.g. "U1-P1"; fallback: "P{n}"
    assigned_date: Mapped[date | None] = mapped_column(Date, nullable=True)         # target start date for PDF
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    subject: Mapped[Subject] = relationship(back_populates="practicals")
    assignments: Mapped[list["Assignment"]] = relationship(back_populates="practical", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("subject_id", "practical_number"),)


class Assignment(Base):
    __tablename__ = "assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    practical_id: Mapped[int] = mapped_column(ForeignKey("practicals.id"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    deadline: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(25), default="Assigned")
    practical: Mapped[Practical] = relationship(back_populates="assignments")
    student: Mapped[Student] = relationship(back_populates="assignments")
    submission: Mapped["Submission | None"] = relationship(back_populates="assignment", uselist=False, cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("practical_id", "student_id"), Index("ix_assignment_status", "status"))


class Submission(Base):
    __tablename__ = "submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id"), unique=True)
    github_url: Mapped[str] = mapped_column(String(500))
    commit_hash: Mapped[str] = mapped_column(String(100), default="")
    branch: Mapped[str] = mapped_column(String(100), default="main")
    documentation: Mapped[str] = mapped_column(Text, default="")
    remarks: Mapped[str] = mapped_column(Text, default="")
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    is_late: Mapped[bool] = mapped_column(Boolean, default=False)
    assignment: Mapped[Assignment] = relationship(back_populates="submission")
    evaluation: Mapped["Evaluation | None"] = relationship(back_populates="submission", uselist=False, cascade="all, delete-orphan")


class Evaluation(Base):
    __tablename__ = "evaluations"
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), unique=True)
    evaluator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    code_quality: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    logic: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    output: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    documentation: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    git_usage: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    innovation: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    total_marks: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=0)
    grade: Mapped[str] = mapped_column(String(3), default="F")
    remarks: Mapped[str] = mapped_column(Text, default="")
    suggestions: Mapped[str] = mapped_column(Text, default="")
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    submission: Mapped[Submission] = relationship(back_populates="evaluation")
    evaluator: Mapped[User] = relationship(foreign_keys=[evaluator_id])


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")


class RolePermission(Base):
    __tablename__ = "role_permissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), index=True)
    permission_id: Mapped[int] = mapped_column(ForeignKey("permissions.id"), index=True)



class LoginLog(Base):
    __tablename__ = "login_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    username: Mapped[str | None] = mapped_column(String(120), nullable=True)
    role: Mapped[str | None] = mapped_column(String(60), nullable=True)
    login_time: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    logout_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(120), nullable=True)
    browser: Mapped[str | None] = mapped_column(String(200), nullable=True)
    os: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="failed")


class PasswordReset(Base):
    __tablename__ = "password_resets"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class SubjectHeaderConfig(Base):
    """Per-subject configurable PDF header set by the faculty.

    All fields have sensible defaults so the PDF can always be generated
    even if the faculty has not explicitly configured it yet.
    """
    __tablename__ = "subject_header_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), unique=True, index=True)
    institution_name: Mapped[str] = mapped_column(String(200), default="Gujarat Vidyapith")
    department_name: Mapped[str] = mapped_column(String(200), default="Department of Computer Science")
    course_name: Mapped[str] = mapped_column(String(150), default="")
    academic_year: Mapped[str] = mapped_column(String(50), default="2026-27")
    subject_teacher: Mapped[str] = mapped_column(String(150), default="")
    subject_title: Mapped[str] = mapped_column(String(300), default="")
    signature_image_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    subject: Mapped[Subject] = relationship(back_populates="header_config")


all_models = (
    Role,
    Department,
    Program,
    User,
    Student,
    Subject,
    FacultySubject,
    Practical,
    Assignment,
    Submission,
    Evaluation,
    AuditLog,
    LoginLog,
    PasswordReset,
    SubjectHeaderConfig,
)
