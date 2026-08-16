import pandas as pd
import streamlit as st
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from services.auth_service import hash_password
from models.schema import Department, Permission, Program, Role, Subject, User
from ui.reports import excel_report, marks_dataframe, pdf_report
from services.core_services import (
    assign_faculty_subjects,
    audit,
    build_bulk_import_template,
    build_subject_import_template,
    bulk_assign_subjects_to_program,
    create_program,
    delete_department,
    delete_program,
    delete_subject,
    delete_user,
    faculty_subject_ids,
    import_bulk_users_from_dataframe,
    import_subjects_from_dataframe,
    update_program,
    validate_bulk_user_import,
    validate_subject_import,
)
from services.core_services import ensure_role, ensure_permission, grant_role_permission
from core.rbac import has_permission


def _roles(db) -> dict[int, str]:
    return {role.id: role.name for role in db.scalars(select(Role).order_by(Role.name))}


def _role_id(db, name: str) -> int | None:
    return db.scalar(select(Role.id).where(Role.name == name))


def _department_labels(db) -> dict[int, str]:
    return {item.id: f"{item.code} · {item.name}" for item in db.scalars(select(Department).order_by(Department.code))}


def _program_labels(db) -> dict[int, str]:
    return {item.id: f"{item.code} · {item.name}" for item in db.scalars(select(Program).order_by(Program.code))}


def _subject_labels(db) -> dict[int, str]:
    return {item.id: f"{item.code} · {item.name}" for item in db.scalars(select(Subject).order_by(Subject.code))}


def _faculty_users(db) -> list[User]:
    faculty_role_id = _role_id(db, "Faculty")
    admin_role_id = _role_id(db, "Administrator")
    role_ids = [r for r in [faculty_role_id, admin_role_id] if r is not None]
    return list(
        db.scalars(
            select(User).where(User.role_id.in_(role_ids)).order_by(User.full_name)
        )
    )


def _commit_with_audit(db, user_id: int, action: str, entity: str, entity_id: int | None = None, details: str = "") -> None:
    audit(db, user_id, action, entity, entity_id, details)
    db.commit()


def _trigger_refresh() -> None:
    if "refresh_counter" not in st.session_state:
        st.session_state.refresh_counter = 0
    st.session_state.refresh_counter += 1
    try:
        st.rerun()
    except Exception:
        st.experimental_rerun()


def _department_crud(db, user_id: int) -> None:
    st.subheader("Department master data")
    departments = list(db.scalars(select(Department).order_by(Department.code)))
    if departments:
        st.dataframe(
            pd.DataFrame([{"Code": item.code, "Name": item.name, "Programmes": len(item.programs), "Subjects": len(item.subjects)} for item in departments]),
            hide_index=True, width="stretch",
        )
    with st.form("add_department", clear_on_submit=True):
        st.caption("Add department")
        code = st.text_input("Department code")
        name = st.text_input("Department name")
        if st.form_submit_button("Add department"):
            if not code.strip() or not name.strip():
                st.error("Enter both a department code and name.")
            else:
                db.add(Department(code=code.strip().upper(), name=name.strip()))
                try:
                    _commit_with_audit(db, user_id, "CREATE_DEPARTMENT", "Department")
                    st.success("Department added.")
                    _trigger_refresh()
                except IntegrityError:
                    db.rollback(); st.error("That department code or name already exists.")
    if departments:
        update_column, delete_column = st.columns(2)
        with update_column:
            labels = {item.id: f"{item.code} · {item.name}" for item in departments}
            selected = st.selectbox("Update department", list(labels), format_func=labels.get, key="update_department_select")
            with st.form("update_department", clear_on_submit=True):
                edited_code = st.text_input("Code", value=db.get(Department, selected).code)
                edited_name = st.text_input("Name", value=db.get(Department, selected).name)
                if st.form_submit_button("Save changes"):
                    department = db.get(Department, selected)
                    department.code = edited_code.strip().upper() or department.code
                    department.name = edited_name.strip() or department.name
                    try:
                        _commit_with_audit(db, user_id, "UPDATE_DEPARTMENT", "Department", department.id)
                        st.success("Department updated.")
                        _trigger_refresh()
                    except IntegrityError:
                        db.rollback(); st.error("That department code or name already exists.")
        with delete_column:
            st.caption("Delete department")
            st.caption("A department with subjects or programmes cannot be deleted.")
            if st.button("Delete selected department", type="secondary"):
                try:
                    delete_department(db, selected, user_id)
                    st.success("Department deleted.")
                    _trigger_refresh()
                except ValueError as error:
                    st.error(str(error))


def _program_crud(db, user_id: int) -> None:
    st.subheader("Programme (course) master data")
    programs = list(db.scalars(select(Program).order_by(Program.code)))
    departments = _department_labels(db)
    if programs:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Code": item.code,
                        "Name": item.name,
                        "Department": item.department.code if item.department else "—",
                        "Duration (months)": item.duration_months,
                        "Semesters": item.total_semesters,
                        "Subjects": len(item.subjects),
                        "Enrolled students": len(item.students),
                    }
                    for item in programs
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    if not departments:
        st.info("Create a department before adding programmes.")
        return
    with st.form("add_program", clear_on_submit=True):
        st.caption("Add programme")
        code = st.text_input("Programme code (e.g. MCA)")
        name = st.text_input("Programme name")
        department_id = st.selectbox("Department", list(departments), format_func=departments.get, key="add_program_department")
        duration_months = st.number_input("Duration (months)", min_value=1, max_value=72, value=24)
        total_semesters = st.number_input("Total semesters", min_value=1, max_value=12, value=4)
        if st.form_submit_button("Add programme"):
            try:
                p = create_program(
                    db,
                    code,
                    name,
                    int(duration_months),
                    int(total_semesters),
                    department_id=department_id,
                    actor_id=user_id,
                )
                _commit_with_audit(db, user_id, "CREATE_PROGRAM", "Program", p.id, p.code)
                st.success("Programme added.")
                _trigger_refresh()
            except ValueError as error:
                st.error(str(error))
            except IntegrityError:
                db.rollback()
                st.error("That programme code or name already exists.")
    if programs:
        update_column, delete_column = st.columns(2)
        with update_column:
            labels = {item.id: f"{item.code} · {item.name}" for item in programs}
            selected = st.selectbox("Update programme", list(labels), format_func=labels.get, key="update_program_select")
            program = db.get(Program, selected)
            with st.form("update_program", clear_on_submit=True):
                edited_code = st.text_input("Code", value=program.code)
                edited_name = st.text_input("Name", value=program.name)
                default_dept_idx = list(departments).index(program.department_id) if program.department_id in departments else 0
                edited_department = st.selectbox("Department", list(departments), format_func=departments.get, index=default_dept_idx, key="update_program_dept")
                edited_duration = st.number_input("Duration (months)", min_value=1, max_value=72, value=program.duration_months)
                edited_semesters = st.number_input("Total semesters", min_value=1, max_value=12, value=program.total_semesters)
                if st.form_submit_button("Save changes"):
                    try:
                        p = update_program(
                            db,
                            program.id,
                            user_id,
                            code=edited_code,
                            name=edited_name,
                            duration_months=int(edited_duration),
                            total_semesters=int(edited_semesters),
                            department_id=edited_department,
                        )
                        _commit_with_audit(db, user_id, "UPDATE_PROGRAM", "Program", p.id, p.code)
                        st.success("Programme updated.")
                        _trigger_refresh()
                    except ValueError as error:
                        st.error(str(error))
                    except IntegrityError:
                        db.rollback()
                        st.error("That programme code or name already exists.")
        with delete_column:
            st.caption("Delete programme")
            st.caption("A programme with enrolled students cannot be deleted.")
            if st.button("Delete selected programme", type="secondary"):
                try:
                    delete_program(db, selected, user_id)
                    st.success("Programme deleted.")
                    _trigger_refresh()
                except ValueError as error:
                    st.error(str(error))


def _subject_crud(db, user_id: int) -> None:
    st.subheader("Subject master data")
    subjects = list(db.scalars(select(Subject).order_by(Subject.code)))
    departments = _department_labels(db)
    programs = _program_labels(db)

    with st.expander("Bulk Subject Import", expanded=False):
        st.caption("Upload semester-wise subject data from Excel with validation and preview.")
        uploaded = st.file_uploader("Choose Excel file", type=["xlsx", "xls"], key="subject_import_file")
        if uploaded is not None:
            try:
                rows = pd.read_excel(uploaded)
                preview = validate_subject_import(rows, db)
                st.success(f"Validated {len(preview)} row(s).")
                st.dataframe(pd.DataFrame(preview), hide_index=True, width="stretch")
                if st.button("Import validated subjects"):
                    summary = import_subjects_from_dataframe(rows, db, user_id)
                    st.success(f"Imported {summary['imported']} subject(s); updated {summary['updated']}; skipped {summary['skipped']}; failed {summary['failed']}")
                    _trigger_refresh()
            except Exception as error:
                st.error(f"Import failed: {error}")
        cols = st.columns(2)
        with cols[0]:
            st.download_button("Download sample template", build_subject_import_template(), file_name="subject_template.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        with cols[1]:
            st.info("The template uses the expected columns for code, name, semester, course, department, credits, subject type, and status.")

    if programs and subjects:
        with st.expander("Bulk Assign Subjects to Programme & Semester", expanded=False):
            st.caption("Link multiple existing subjects to a specific Programme and Semester in one click.")
            b_col1, b_col2 = st.columns(2)
            with b_col1:
                b_prog_id = st.selectbox("Target Programme", list(programs), format_func=programs.get, key="bulk_subj_prog")
                b_prog = db.get(Program, b_prog_id)
            with b_col2:
                b_max_sem = b_prog.total_semesters if b_prog else 10
                b_sem = st.number_input("Semester", min_value=1, max_value=b_max_sem, value=1, key="bulk_subj_sem")
            
            all_subjs_labels = {
                s.id: f"{s.code} · {s.name} [Current: {s.program.code if s.program else 'No Prog'} - Sem {s.semester}]"
                for s in subjects
            }
            selected_subjs = st.multiselect("Select subjects to link", list(all_subjs_labels), format_func=all_subjs_labels.get, key="bulk_subj_select")
            if st.button("Assign Selected Subjects to Programme"):
                if not selected_subjs:
                    st.error("Please select at least one subject.")
                else:
                    count = bulk_assign_subjects_to_program(db, selected_subjs, b_prog_id, int(b_sem), user_id)
                    st.success(f"Successfully linked {count} subject(s) to {b_prog.code} (Sem {b_sem}).")
                    _trigger_refresh()

    if subjects:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Code": item.code,
                        "Name": item.name,
                        "Programme": item.program.code if item.program else "—",
                        "Semester": item.semester,
                        "Department": item.department.code if item.department else "—",
                    }
                    for item in subjects
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    if not departments:
        st.info("Create a department before adding subjects.")
        return
    if not programs:
        st.info("Create a programme before adding subjects.")
        return

    st.caption("Add subject")
    add_prog_id = st.selectbox("Programme", list(programs), format_func=programs.get, key="add_subject_program")
    chosen_program = db.get(Program, add_prog_id)
    max_semester = chosen_program.total_semesters if chosen_program else 10

    with st.form("add_subject", clear_on_submit=True):
        code = st.text_input("Subject code")
        name = st.text_input("Subject name")
        semester = st.number_input("Semester", min_value=1, max_value=max_semester, value=1)
        default_dept_idx = 0
        if chosen_program and chosen_program.department_id and chosen_program.department_id in departments:
            default_dept_idx = list(departments).index(chosen_program.department_id)
        department_id = st.selectbox("Department", list(departments), index=default_dept_idx, format_func=departments.get, key="add_subject_department")
        if st.form_submit_button("Add subject"):
            if not code.strip() or not name.strip():
                st.error("Enter a subject code and name.")
            else:
                db.add(
                    Subject(
                        code=code.strip().upper(),
                        name=name.strip(),
                        semester=int(semester),
                        department_id=department_id,
                        program_id=add_prog_id,
                    )
                )
                try:
                    _commit_with_audit(db, user_id, "CREATE_SUBJECT", "Subject")
                    st.success(f"Subject {code.strip().upper()} added to {chosen_program.code} (Sem {semester}).")
                    _trigger_refresh()
                except IntegrityError:
                    db.rollback()
                    st.error("That subject code already exists.")

    if subjects:
        update_column, delete_column = st.columns(2)
        with update_column:
            labels = {item.id: f"{item.code} · {item.name}" for item in subjects}
            selected = st.selectbox("Update subject", list(labels), format_func=labels.get, key="update_subject_select")
            subject = db.get(Subject, selected)
            with st.form("update_subject", clear_on_submit=True):
                edited_code = st.text_input("Code", value=subject.code)
                edited_name = st.text_input("Name", value=subject.name)
                
                default_prog_idx = list(programs).index(subject.program_id) if subject.program_id in programs else 0
                edited_program_id = st.selectbox("Programme", list(programs), index=default_prog_idx, format_func=programs.get, key="update_subject_prog")
                upd_prog = db.get(Program, edited_program_id)
                upd_max_sem = upd_prog.total_semesters if upd_prog else 10
                
                edited_semester = st.number_input("Semester", min_value=1, max_value=upd_max_sem, value=min(subject.semester, upd_max_sem))
                default_dept_idx = list(departments).index(subject.department_id) if subject.department_id in departments else 0
                edited_department = st.selectbox("Department", list(departments), format_func=departments.get, index=default_dept_idx, key="update_subject_department")
                if st.form_submit_button("Save changes"):
                    subject.code = edited_code.strip().upper() or subject.code
                    subject.name = edited_name.strip() or subject.name
                    subject.semester = int(edited_semester)
                    subject.program_id = edited_program_id
                    subject.department_id = edited_department
                    try:
                        _commit_with_audit(db, user_id, "UPDATE_SUBJECT", "Subject", subject.id)
                        st.success("Subject updated.")
                        _trigger_refresh()
                    except IntegrityError:
                        db.rollback()
                        st.error("That subject code already exists.")
        with delete_column:
            st.caption("Delete subject")
            st.caption("A subject assigned to faculty or with practicals cannot be deleted.")
            if st.button("Delete selected subject", type="secondary"):
                try:
                    delete_subject(db, selected, user_id)
                    st.success("Subject deleted.")
                    _trigger_refresh()
                except ValueError as error:
                    st.error(str(error))


def _faculty_crud(db, user_id: int) -> None:
    st.subheader("Faculty accounts and subject-wise assignment")
    faculty = _faculty_users(db)
    all_subjects = _subject_labels(db)
    if faculty:
        rows = []
        for member in faculty:
            assigned = sorted(link.subject.code for link in member.faculty_subjects)
            rows.append({"Name": member.full_name, "Username": member.username, "Email": member.email, "Assigned subjects": ", ".join(assigned) or "—", "Active": "Yes" if member.is_active else "No"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.info("No faculty accounts yet. Create one below.")

    if not all_subjects:
        st.info("Create subjects first, then assign them to faculty.")
    else:
        st.caption("Add faculty profile")
        non_faculty = db.scalars(
            select(User).where(User.role_id != _role_id(db, "Faculty")).order_by(User.full_name)
        ).all()
        non_faculty_labels = {item.id: f"{item.full_name} ({item.username})" for item in non_faculty}
        with st.form("add_faculty", clear_on_submit=True):
            mode = st.radio("How do you want to create the faculty account?", ["New account", "Promote an existing user"], horizontal=True)
            username = st.text_input("Username") if mode == "New account" else None
            full_name = st.text_input("Full name") if mode == "New account" else None
            email = st.text_input("Email") if mode == "New account" else None
            password = st.text_input("Temporary password", type="password") if mode == "New account" else None
            existing_user_id = st.selectbox("Choose user", list(non_faculty_labels), format_func=non_faculty_labels.get, key="faculty_existing_user") if mode == "Promote an existing user" else None
            assigned_subjects = st.multiselect("Subjects assigned to this faculty member", list(all_subjects), format_func=lambda value: all_subjects[value], key="add_faculty_subjects")
            if st.form_submit_button("Create faculty profile"):
                if mode == "New account":
                    if not all([username, full_name, email, password]):
                        st.error("Complete username, full name, email, and password.")
                    else:
                        try:
                            target = User(username=username.strip(), full_name=full_name.strip(), email=email.strip(), password_hash=hash_password(password), role_id=_role_id(db, "Faculty"))
                            db.add(target); db.flush()
                        except IntegrityError:
                            db.rollback(); st.error("That username or email already exists.")
                        else:
                            assign_faculty_subjects(db, target.id, assigned_subjects, user_id)
                            _commit_with_audit(db, user_id, "CREATE_FACULTY", "User", target.id)
                            st.success("Faculty account created.")
                            _trigger_refresh()
                else:
                    if not existing_user_id:
                        st.error("Select a user to promote.")
                    else:
                        target = db.get(User, existing_user_id)
                        target.role_id = _role_id(db, "Faculty")
                        assign_faculty_subjects(db, target.id, assigned_subjects, user_id)
                        _commit_with_audit(db, user_id, "PROMOTE_FACULTY", "User", target.id)
                        st.success("User promoted to faculty.")
                        _trigger_refresh()

    if faculty:
        st.caption("Edit faculty profile and subject assignments")
        faculty_labels = {item.id: f"{item.full_name} ({item.username})" for item in faculty}
        selected = st.selectbox("Faculty member", list(faculty_labels), format_func=faculty_labels.get, key="edit_faculty_select")
        member = db.get(User, selected)
        current_ids = [sid for sid in faculty_subject_ids(db, member.id) if sid in all_subjects]
        with st.form("update_faculty", clear_on_submit=False):
            edited_name = st.text_input("Full name", value=member.full_name, key=f"ef_name_{member.id}")
            edited_email = st.text_input("Email", value=member.email, key=f"ef_email_{member.id}")
            edited_active = st.checkbox("Active account", value=member.is_active, key=f"ef_active_{member.id}")
            edited_subjects = st.multiselect(
                "Assigned subjects",
                list(all_subjects),
                default=current_ids,
                format_func=lambda value: all_subjects[value],
                key=f"edit_faculty_subjects_{member.id}",
            )
            new_password = st.text_input("Reset password (leave blank to keep)", type="password", key=f"ef_pass_{member.id}")
            if st.form_submit_button("Save changes"):
                member.full_name = edited_name.strip() or member.full_name
                member.email = edited_email.strip() or member.email
                member.is_active = edited_active
                if new_password:
                    member.password_hash = hash_password(new_password)
                try:
                    assign_faculty_subjects(db, member.id, edited_subjects, user_id)
                    _commit_with_audit(db, user_id, "UPDATE_FACULTY", "User", member.id)
                    st.success(f"Updated {member.full_name} ({len(edited_subjects)} subject(s) assigned).")
                    _trigger_refresh()
                except IntegrityError:
                    db.rollback(); st.error("That email is already in use by another account.")
        if st.button(f"Delete {member.full_name}", type="secondary", key=f"del_faculty_{member.id}"):
            try:
                delete_user(db, member.id, user_id)
                st.success("Faculty account deleted.")
                _trigger_refresh()
            except ValueError as error:
                st.error(str(error))


def _user_crud(db, user_id: int) -> None:
    st.subheader("User accounts")
    users = list(db.scalars(select(User).order_by(User.username)))
    roles = _roles(db)

    # ---- Student roster: grouped by Programme → Semester ----
    from models.schema import Student
    all_students = list(db.scalars(select(Student).order_by(Student.program, Student.semester, Student.enrollment_no)))
    programs_list = list(db.scalars(select(Program).order_by(Program.code)))

    if all_students:
        view_tab, all_tab = st.tabs(["Students by Programme & Semester", "All users"])
        with view_tab:
            if not programs_list:
                st.info("No programmes configured yet.")
            else:
                prog_filter_labels = {p.id: f"{p.code} · {p.name}" for p in programs_list}
                prog_filter_id = st.selectbox(
                    "Filter by Programme",
                    [None] + list(prog_filter_labels),
                    format_func=lambda v: "All Programmes" if v is None else prog_filter_labels[v],
                    key="student_prog_filter",
                )
                filtered_students = [
                    s for s in all_students
                    if prog_filter_id is None or s.program_id == prog_filter_id
                ]
                if not filtered_students:
                    st.info("No students enrolled in this programme yet.")
                else:
                    # Group by semester
                    from itertools import groupby
                    semester_sorted = sorted(filtered_students, key=lambda s: (s.semester or 0))
                    for sem, group in groupby(semester_sorted, key=lambda s: s.semester):
                        group_list = list(group)
                        with st.expander(f"Semester {sem}  ·  {len(group_list)} student(s)", expanded=True):
                            rows_data = []
                            for s in group_list:
                                prog_label = s.program_ref.code if s.program_ref else s.program or "—"
                                rows_data.append({
                                    "Enrollment No.": s.enrollment_no,
                                    "Name": s.user.full_name if s.user else "—",
                                    "Email": s.user.email if s.user else "—",
                                    "Programme": prog_label,
                                    "Semester": s.semester,
                                    "Active": "Yes" if (s.user and s.user.is_active) else "No",
                                })
                            st.dataframe(pd.DataFrame(rows_data), hide_index=True, width="stretch")
        with all_tab:
            st.dataframe(
                pd.DataFrame(
                    [{"Username": item.username, "Name": item.full_name, "Email": item.email, "Role": roles[item.role_id], "Active": "Yes" if item.is_active else "No"} for item in users]
                ),
                hide_index=True, width="stretch",
            )
    elif users:
        st.dataframe(
            pd.DataFrame(
                [{"Username": item.username, "Name": item.full_name, "Email": item.email, "Role": roles[item.role_id], "Active": "Yes" if item.is_active else "No"} for item in users]
            ),
            hide_index=True, width="stretch",
        )

    st.caption("Create user account")
    student_role_id = next((r_id for r_id, r_name in roles.items() if r_name == "Student"), list(roles)[0] if roles else None)
    default_role_idx = list(roles).index(student_role_id) if student_role_id in roles else 0
    selected_role_id = st.selectbox("Role", list(roles), index=default_role_idx, format_func=roles.get, key="create_user_role_select")
    is_student = roles.get(selected_role_id) == "Student"
    programs = _program_labels(db)
    
    if is_student and not programs:
        st.warning("Please create a Programme in Master Data before creating student accounts.")

    selected_prog_id = None
    if is_student and programs:
        selected_prog_id = st.selectbox("Programme", list(programs), format_func=programs.get, key="cu_program_select")
    
    sel_prog = db.get(Program, selected_prog_id) if selected_prog_id else None
    max_semester = sel_prog.total_semesters if sel_prog else 10

    with st.form("create_user", clear_on_submit=False):
        username = st.text_input("Username / Enrollment No" if is_student else "Username", key="cu_username")
        full_name = st.text_input("Full name", key="cu_full_name")
        email = st.text_input("Email", key="cu_email")
        password = st.text_input("Temporary password", type="password", key="cu_password")
        
        if is_student:
            st.markdown("---")
            st.caption(f"Student profile details ({sel_prog.code if sel_prog else 'No Programme'})")
            semester = st.number_input("Semester", min_value=1, max_value=max_semester, value=1, key="cu_semester")
            
        if st.form_submit_button("Create user"):
            if not all([username.strip(), full_name.strip(), email.strip(), password]):
                st.error("Complete all fields.")
            elif is_student and not selected_prog_id:
                st.error("Select a Programme for the student profile.")
            elif is_student and not re.match(r"^(\d{9}|\d{12})$", username.strip()):
                st.error(f"Student Enrollment No. '{username.strip()}' must be 9 or 12 digits (e.g. 210160450 or 250160450310).")
            elif is_student and not re.match(r"^(\d{9}|\d{12})\.gvp@gujaratvidyapith\.org$", email.strip(), re.IGNORECASE):
                st.error(f"Student email '{email.strip()}' must follow the format '<9-or-12-digit-enrollment>.gvp@gujaratvidyapith.org'.")
            elif is_student and email.strip().lower().split(".gvp@")[0] != username.strip().lower():
                st.error(f"Student email enrollment number does not match Enrollment No. '{username.strip()}'.")
            else:
                user = User(username=username.strip(), full_name=full_name.strip(), email=email.strip(), password_hash=hash_password(password), role_id=selected_role_id)
                db.add(user)
                try:
                    db.flush()
                    if is_student:
                        from models.schema import Student
                        db.add(Student(user_id=user.id, enrollment_no=username.strip(), semester=int(semester), program=sel_prog.code if sel_prog else "MCA", program_id=selected_prog_id))
                    _commit_with_audit(db, user_id, "CREATE_USER", "User", user.id)
                    st.success("User account created.")
                    for key in ["cu_username", "cu_full_name", "cu_email", "cu_password", "cu_semester"]:
                        if key in st.session_state:
                            del st.session_state[key]
                    _trigger_refresh()
                except IntegrityError:
                    db.rollback()
                    st.error("That username, enrollment number, or email already exists.")
    if users:
        update_column, delete_column = st.columns(2)
        with update_column:
            user_labels = {item.id: f"{item.username} · {roles[item.role_id]}" for item in users}
            selected = st.selectbox("Update user", list(user_labels), format_func=user_labels.get, key="update_user_select")
            target = db.get(User, selected)
            with st.form("update_user", clear_on_submit=False):
                edited_name = st.text_input("Full name", value=target.full_name, key=f"uu_name_{target.id}")
                edited_email = st.text_input("Email", value=target.email, key=f"uu_email_{target.id}")
                edited_active = st.checkbox("Active account", value=target.is_active, key=f"uu_active_{target.id}")
                default_role_index = list(roles).index(target.role_id) if target.role_id in roles else 0
                edited_role = st.selectbox("Role", list(roles), index=default_role_index, format_func=roles.get, key=f"uu_role_{target.id}")
                new_password = st.text_input("Reset password (leave blank to keep)", type="password", key=f"uu_pass_{target.id}")
                
                is_student_update = target.student is not None
                edited_prog_id = None
                upd_prog = None
                if is_student_update:
                    st.markdown("---")
                    st.caption("Student profile details")
                    edited_enrollment = st.text_input("Enrollment No", value=target.student.enrollment_no, key=f"uu_enroll_{target.id}")
                    
                    default_prog_idx = 0
                    if target.student.program_id in programs:
                        default_prog_idx = list(programs).index(target.student.program_id)
                    elif programs and target.student.program:
                        for idx, pid in enumerate(programs):
                            if target.student.program in programs[pid]:
                                default_prog_idx = idx
                                break
                    
                    edited_prog_id = st.selectbox("Programme", list(programs), index=default_prog_idx, format_func=programs.get, key=f"uu_prog_{target.id}") if programs else None
                    upd_prog = db.get(Program, edited_prog_id) if edited_prog_id else None
                    upd_max_sem = upd_prog.total_semesters if upd_prog else 10
                    edited_semester = st.number_input("Semester", min_value=1, max_value=upd_max_sem, value=min(target.student.semester, upd_max_sem), key=f"uu_sem_{target.id}")

                if st.form_submit_button("Save changes"):
                    if is_student_update and not edited_enrollment.strip():
                        st.error("Enrollment number cannot be empty.")
                    else:
                        target.full_name = edited_name.strip() or target.full_name
                        target.email = edited_email.strip() or target.email
                        target.is_active = edited_active
                        target.role_id = edited_role
                        if is_student_update:
                            target.student.enrollment_no = edited_enrollment.strip()
                            if edited_prog_id:
                                target.student.program_id = edited_prog_id
                                target.student.program = upd_prog.code if upd_prog else target.student.program
                            target.student.semester = int(edited_semester)
                        if new_password:
                            target.password_hash = hash_password(new_password)
                        try:
                            _commit_with_audit(db, user_id, "UPDATE_USER", "User", target.id)
                            st.success("User updated.")
                            for key in [f"uu_name_{target.id}", f"uu_email_{target.id}", f"uu_active_{target.id}", f"uu_role_{target.id}", f"uu_pass_{target.id}", f"uu_enroll_{target.id}", f"uu_prog_{target.id}", f"uu_sem_{target.id}"]:
                                if key in st.session_state:
                                    del st.session_state[key]
                            _trigger_refresh()
                        except IntegrityError:
                            db.rollback()
                            st.error("That email or enrollment number is already in use.")
        with delete_column:
            st.caption("Delete user")
            st.caption("Users with practicals, evaluations, or a student profile must be deactivated instead.")
            if st.button("Delete selected user", type="secondary"):
                try:
                    delete_user(db, selected, user_id)
                    st.success("User deleted.")
                    _trigger_refresh()
                except ValueError as error:
                    st.error(str(error))


def _reports_ui(db) -> None:
    report_data = marks_dataframe(db)
    st.subheader("Evaluation report")
    st.dataframe(report_data, hide_index=True, width="stretch")
    csv_data = report_data.to_csv(index=False).encode("utf-8")
    downloads = st.container()
    with downloads:
        cols = st.columns(3)
        with cols[0]:
            st.download_button("Download CSV", csv_data, "evaluation-report.csv", "text/csv")
        with cols[1]:
            st.download_button("Download XLSX", excel_report(db), "evaluation-report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        with cols[2]:
            st.download_button("Download PDF", pdf_report(db), "evaluation-report.pdf", "application/pdf")


def _bulk_import_ui(db, user_id: int) -> None:
    st.subheader("Bulk user import")
    st.caption("Upload an Excel file to validate and import students, faculty, or administrators.")
    import_type = st.selectbox("Import type", ["student", "faculty", "admin"], format_func=lambda value: value.title())

    # Use a key-counter so we can reset the uploader after a successful import
    if "bulk_import_reset" not in st.session_state:
        st.session_state["bulk_import_reset"] = 0

    uploader_key = f"bulk_import_file_{st.session_state['bulk_import_reset']}"
    uploaded = st.file_uploader("Choose Excel file", type=["xlsx", "xls"], key=uploader_key)

    if uploaded is not None:
        try:
            data = pd.read_excel(uploaded)
            preview = validate_bulk_user_import(data, import_type, db)

            ready_count = sum(1 for r in preview if r.get("ready"))
            error_count = len(preview) - ready_count

            # Summary metrics
            mcol1, mcol2, mcol3 = st.columns(3)
            with mcol1:
                st.metric("Total rows", len(preview))
            with mcol2:
                st.metric("Ready to import", ready_count)
            with mcol3:
                st.metric("Rows with errors", error_count)

            # Only show the preview table when there are problems to fix
            if error_count:
                st.warning(f"{error_count} row(s) have issues and will be skipped. Review them below before importing.")
                error_rows = [r for r in preview if not r.get("ready")]
                st.dataframe(pd.DataFrame(error_rows), hide_index=True, width="stretch")

            if ready_count == 0:
                st.error("No rows are ready to import. Fix the errors above and re-upload.")
            else:
                btn_label = f"Import {ready_count} valid row(s)" if error_count == 0 else f"Import {ready_count} valid row(s), skip {error_count}"
                if st.button(btn_label, type="primary"):
                    summary = import_bulk_users_from_dataframe(data, import_type, db, user_id)
                    if summary["imported"] > 0:
                        st.success(
                            f"✅ Successfully imported **{summary['imported']}** {import_type}(s). "
                            + (f"Skipped {summary['skipped']} duplicate(s)." if summary["skipped"] else "")
                            + (f" {summary['failed']} failed." if summary["failed"] else "")
                        )
                        # Reset the file uploader so the old file disappears
                        st.session_state["bulk_import_reset"] += 1
                        _trigger_refresh()
                    else:
                        st.warning(
                            f"No new records were imported. "
                            f"All {summary['skipped']} row(s) were already present in the system."
                        )
        except Exception as error:
            st.error(f"Import failed: {error}")

    cols = st.columns(2)
    with cols[0]:
        template_bytes = build_bulk_import_template(import_type)
        st.download_button(
            "⬇ Download sample template",
            template_bytes,
            file_name=f"{import_type}_import_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with cols[1]:
        if import_type == "student":
            st.info("Required: **Enrollment No.**, **Student Name**, **Email**, **Programme**, **Semester**. Programme must be added in Master Data first.")
        elif import_type == "faculty":
            st.info("Required: **Faculty ID**, **Name**, **Email**, **Mobile**, **Department**, **Designation**.")
        else:
            st.info("Required: **Employee ID**, **Name**, **Email**, **Mobile**, **Role**.")


def administrator_page(db, user: User) -> None:
    st.title("Administrator workspace")
    # enforce admin-level permission
    if not has_permission(db, user, "admin.access"):
        st.error("You do not have permission to access the Administrator page.")
        return
    st.caption("Manage master data, faculty, user accounts, and evaluation reports.")
    master_tab, faculty_tab, users_tab, import_tab, reports_tab, perm_tab = st.tabs(["Master data", "Faculty", "Users", "Import", "Reports", "Permissions"])
    with perm_tab:
        _permissions_ui(db, user.id)
    with master_tab:
        program_tab, department_tab, subject_tab = st.tabs(["Programmes", "Departments", "Subjects"])
        with program_tab:
            _program_crud(db, user.id)
        with department_tab:
            _department_crud(db, user.id)
        with subject_tab:
            _subject_crud(db, user.id)
    with faculty_tab:
        _faculty_crud(db, user.id)
    with users_tab:
        _user_crud(db, user.id)
    with import_tab:
        _bulk_import_ui(db, user.id)
    with reports_tab:
        _reports_ui(db)


def _permissions_ui(db, user_id: int) -> None:
    st.subheader("Permissions management")
    st.caption("Create permissions and assign them to roles.")
    perms = list(db.scalars(select(Permission).order_by(Permission.code)))
    roles = _roles(db)
    # create permission
    with st.form("create_permission", clear_on_submit=True):
        code = st.text_input("Permission code (e.g. faculty.access)")
        description = st.text_input("Description")
        if st.form_submit_button("Create permission"):
            if not code.strip():
                st.error("Enter a permission code.")
            else:
                try:
                    p = ensure_permission(db, code.strip(), description.strip())
                    _commit_with_audit(db, user_id, "CREATE_PERMISSION", "Permission", p.id, p.code)
                    st.success("Permission created.")
                    _trigger_refresh()
                except Exception as e:
                    db.rollback(); st.error(str(e))
    # assign permission
    if perms and roles:
        st.markdown("---")
        perm_map = {p.id: p.code for p in perms}
        selected_perm = st.selectbox("Permission to assign", list(perm_map), format_func=lambda v: perm_map[v])
        selected_roles = st.multiselect("Assign to roles", list(roles), format_func=roles.get)
        if st.button("Grant permission"):
            for rid in selected_roles:
                grant_role_permission(db, db.get(Role, rid), db.get(Permission, selected_perm))
            _commit_with_audit(db, user_id, "GRANT_PERMISSION", "Permission", selected_perm, f"roles={selected_roles}")
            st.success("Permission granted to selected roles.")