import pandas as pd
import streamlit as st
from datetime import date, timedelta
from sqlalchemy import select
from models.schema import Assignment, FacultySubject, Practical, Program, Student, Submission, Subject
from services.core_services import (
    _github_url_type,
    assign_practical,
    build_practical_import_template,
    create_practical,
    delete_practical,
    faculty_practicals,
    get_or_create_subject_header_config,
    get_student_subject_progress,
    grade_submission,
    import_practicals_from_dataframe,
    subjects_for_faculty,
    submissions_for_faculty,
    update_practical,
    update_subject_header_config,
    validate_practical_import,
)
from services.pdf_service import generate_blank_master_pdf, generate_practical_list_pdf
from core.rbac import has_permission

VALID_GRADES = ["A", "B", "C", "D", "E", "F"]


def _subject_labels(subjects: list[Subject]) -> dict[int, str]:
    return {subject.id: f"{subject.code} · {subject.name}" for subject in subjects}


def faculty_page(db, user) -> None:
    st.title("Faculty workspace")
    # require faculty access permission (faculty and administrators allowed)
    if not (user and (user.role and user.role.name in ["Faculty", "Administrator"] or has_permission(db, user, "faculty.access"))):
        st.error("You do not have permission to access the Faculty workspace.")
        return

    subjects = subjects_for_faculty(db, user.id)
    if not subjects:
        st.info("No subjects have been assigned to you yet. Contact the administrator to assign subjects.")
        return
    subject_labels = _subject_labels(subjects)
    st.caption(f"Teaching {len(subjects)} subject(s): {', '.join(subject_labels.values())}")

    practicals = faculty_practicals(db, user.id)
    submissions = submissions_for_faculty(db, user.id)
    pending_count = len([s for s in submissions if not s.evaluation])
    columns = st.columns(4)
    for column, label, value in zip(
        columns,
        ["Assigned subjects", "Practicals", "Submissions to evaluate", "Pending evaluation"],
        [len(subjects), len(practicals), len(submissions), pending_count],
    ):
        with column:
            st.metric(label, value)

    tab_create, tab_assign, tab_grade, tab_pdf, tab_reports = st.tabs([
        "Create / manage practicals",
        "Assign to students",
        "Evaluate",
        "📄 PDF Header Settings",
        "📈 Student Progress & Reports",
    ])
    with tab_create:
        _practical_management(db, user.id, subject_labels)
    with tab_assign:
        _assignment_ui(db, user.id, subject_labels)
    with tab_grade:
        _evaluation_ui(db, user.id, subject_labels)
    with tab_pdf:
        _pdf_header_settings_ui(db, user, subject_labels)
    with tab_reports:
        _student_reports_ui(db, user.id, subject_labels)


def _bulk_practical_import(db, user_id: int) -> None:
    st.subheader("Bulk import practicals")
    st.caption("Upload an Excel file to create multiple practicals across your assigned subjects.")
    with st.expander("Download sample template", expanded=False):
        st.download_button(
            "Download practical import template",
            build_practical_import_template(),
            file_name="practical_import_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.info("Required columns: **Subject Code**, **Practical Title**. Optional: Description, Learning Outcome, Difficulty (Easy/Medium/Hard), Submission Date (YYYY-MM-DD). Grade and Submission Days are no longer needed.")

    if "bulk_practical_reset" not in st.session_state:
        st.session_state["bulk_practical_reset"] = 0

    uploader_key = f"practical_import_file_{st.session_state['bulk_practical_reset']}"
    uploaded = st.file_uploader("Choose Excel file", type=["xlsx", "xls"], key=uploader_key)
    if uploaded is not None:
        try:
            rows = pd.read_excel(uploaded)
            preview = validate_practical_import(rows, db, user_id)
            valid_count = sum(1 for item in preview if item["ready"])
            bad_count = len(preview) - valid_count

            # Summary metrics
            mcol1, mcol2, mcol3 = st.columns(3)
            with mcol1:
                st.metric("Total rows", len(preview))
            with mcol2:
                st.metric("Ready to import", valid_count)
            with mcol3:
                st.metric("Rows with errors/duplicates", bad_count)

            if bad_count:
                st.warning(f"{bad_count} row(s) have issues (missing fields or already in database) and will be skipped.")
                st.dataframe(pd.DataFrame([p for p in preview if not p["ready"]]), hide_index=True, width="stretch")

            if valid_count == 0:
                st.error("No new practicals to import. All items either have errors or are already present in the database.")
            else:
                btn_label = f"Import {valid_count} valid practical(s)" if bad_count == 0 else f"Import {valid_count} valid practical(s), skip {bad_count}"
                if st.button(btn_label, type="primary"):
                    summary = import_practicals_from_dataframe(rows, db, user_id, user_id)
                    if summary["imported"] > 0:
                        st.success(f"✅ Successfully imported **{summary['imported']}** practical(s). " + (f"Skipped {summary['skipped']} duplicate(s)." if summary['skipped'] else ""))
                        st.session_state["bulk_practical_reset"] += 1
                        st.rerun()
                    else:
                        st.warning(f"No new practicals were imported. All {summary['skipped']} practical(s) already exist in database.")
        except Exception as error:
            st.error(f"Import failed: {error}")


def _practical_management(db, user_id: int, subject_labels: dict[int, str]) -> None:
    subjects = list(db.scalars(select(Subject).where(Subject.id.in_(subject_labels.keys())).order_by(Subject.code)))
    if not subjects:
        st.info("Create subjects first via the administrator.")
        return

    with st.expander("Bulk import practicals", expanded=False):
        _bulk_practical_import(db, user_id)

    with st.form("create_practical", clear_on_submit=True):
        st.caption("Create a new practical for one of your subjects")
        subject = st.selectbox("Subject", subjects, format_func=lambda item: f"{item.code} · {item.name}", key="create_practical_subject")
        title = st.text_input("Practical title")
        description = st.text_area("Description")
        learning_outcome = st.text_area("Learning outcome")
        difficulty = st.selectbox("Difficulty", ["Easy", "Medium", "Hard"], index=1)
        submission_date = st.date_input("Submission date (optional)", value=None, min_value=date.today())
        # PDF-specific optional fields
        with st.expander("🗒️ PDF display fields (optional)", expanded=False):
            st.caption("These fields control how the practical appears in generated PDFs.")
            unit_name = st.text_input("Unit name", placeholder="e.g. Unit-1 & Unit-2", help="Unit grouping banner shown in the PDF.")
            practical_code = st.text_input("Practical code", placeholder="e.g. U1-P1", help="Shown in 'Practical No.' column. Defaults to P1, P2, ...")
            assigned_date = st.date_input("Assigned date (for PDF)", value=None, help="The assigned / start date to show in the PDF.")
        if st.form_submit_button("Create practical"):
            if not title.strip():
                st.error("Enter a practical title.")
            else:
                p = create_practical(
                    db,
                    subject_id=subject.id,
                    title=title,
                    description=description,
                    learning_outcome=learning_outcome,
                    difficulty=difficulty,
                    grade="A",  # default; set per-student during evaluation
                    submission_date=submission_date,
                    creator_id=user_id,
                )
                # Apply optional PDF fields
                if unit_name.strip() or practical_code.strip() or assigned_date:
                    update_practical(
                        db, p.id, user_id,
                        unit_name=unit_name.strip() or None,
                        practical_code=practical_code.strip() or None,
                        assigned_date=assigned_date or None,
                    )
                st.success(f"Practical created for {subject.code}.")
                st.rerun()

    practicals = faculty_practicals(db, user_id)
    if not practicals:
        st.info("No practicals exist yet for your assigned subjects.")
        return
    selected = st.selectbox(
        "Practical to manage",
        practicals,
        format_func=lambda item: f"{item.subject.code} · P{item.practical_number} · {item.title}",
        key="manage_practical_select",
    )
    manage_tab, delete_tab = st.tabs(["Edit practical", "Delete practical"])
    with manage_tab:
        with st.form("update_practical_form"):
            edited_title = st.text_input("Title", value=selected.title)
            edited_description = st.text_area("Description", value=selected.description)
            edited_outcome = st.text_area("Learning outcome", value=selected.learning_outcome)
            difficulty_index = ["Easy", "Medium", "Hard"].index(selected.difficulty) if selected.difficulty in ["Easy", "Medium", "Hard"] else 1
            edited_difficulty = st.selectbox("Difficulty", ["Easy", "Medium", "Hard"], index=difficulty_index)

            if selected.submission_date:
                st.caption(f"Submission date is fixed at {selected.submission_date:%d %b %Y} to keep assigned deadlines immutable.")
            else:
                st.caption("Submission date is not set; no deadline will be enforced.")

            with st.expander("🗒️ PDF display fields (optional)", expanded=False):
                st.caption("These fields only affect how the practical appears in generated PDFs.")
                edited_unit_name = st.text_input("Unit name", value=selected.unit_name or "", placeholder="e.g. Unit-1 & Unit-2")
                edited_pract_code = st.text_input("Practical code", value=selected.practical_code or "", placeholder="e.g. U1-P1")
                edited_assigned_date = st.date_input("Assigned date (for PDF)", value=selected.assigned_date or None)

            if st.form_submit_button("Save practical changes"):
                update_practical(
                    db,
                    selected.id,
                    user_id,
                    title=edited_title,
                    description=edited_description,
                    learning_outcome=edited_outcome,
                    difficulty=edited_difficulty,
                    unit_name=edited_unit_name.strip() or None,
                    practical_code=edited_pract_code.strip() or None,
                    assigned_date=edited_assigned_date or None,
                )
                st.success("Practical updated.")
                st.rerun()
    with delete_tab:
        st.warning("Deleting a practical also removes its assignments. Practicals with student submissions cannot be deleted.")
        if st.button("Delete this practical"):
            try:
                delete_practical(db, selected.id, user_id)
                st.success("Practical deleted.")
                st.rerun()
            except ValueError as error:
                st.error(str(error))


def _assignment_ui(db, user_id: int, subject_labels: dict[int, str]) -> None:
    st.subheader("Assign practical to students")
    st.caption("Select one of your assigned subjects, choose a practical, and assign it to students.")

    # 1. Fetch faculty's assigned subjects
    fac_subjects = subjects_for_faculty(db, user_id)
    if not fac_subjects:
        st.info("No subjects have been assigned to you yet. Contact the administrator.")
        return

    subj_choices = {
        s.id: f"{s.code} · {s.name} ({s.program.code if s.program else 'No Prog'} · Sem {s.semester})"
        for s in fac_subjects
    }

    col1, col2 = st.columns(2)
    with col1:
        selected_subj_id = st.selectbox(
            "1. Select Subject",
            list(subj_choices),
            format_func=subj_choices.get,
            key="assign_subj_select",
        )
        chosen_subject = db.get(Subject, selected_subj_id)

    # 2. Fetch practicals for selected subject
    practicals = list(
        db.scalars(
            select(Practical)
            .where(Practical.subject_id == selected_subj_id)
            .order_by(Practical.practical_number)
        )
    )
    if not practicals:
        with col2:
            st.warning("No practicals created for this subject.")
        st.info(f"Create a practical for {chosen_subject.code} in the 'Create / manage practicals' tab first.")
        return

    pract_choices = {
        p.id: f"P{p.practical_number} · {p.title} (Due: {p.submission_date.strftime('%d %b %Y') if p.submission_date else 'No date'})"
        for p in practicals
    }
    with col2:
        selected_pract_id = st.selectbox(
            "2. Select Practical",
            list(pract_choices),
            format_func=pract_choices.get,
            key="assign_pract_select",
        )
        selected_practical = db.get(Practical, selected_pract_id)

    # Auto-detected Programme & Semester with override expander
    prog_code = chosen_subject.program.code if chosen_subject.program else "MCA"
    prog_id = chosen_subject.program_id
    sem_val = chosen_subject.semester or 1

    st.caption(f"Target Cohort: **{prog_code}** · **Semester {sem_val}** (derived from subject configuration)")

    st.markdown("---")

    # Fetch students enrolled in this program and semester
    student_query = select(Student).where(
        (Student.program_id == prog_id) | ((Student.program_id.is_(None)) & (Student.program == prog_code)),
        Student.semester == sem_val,
    ).order_by(Student.enrollment_no)
    students = list(db.scalars(student_query))

    if not students:
        st.info(f"No students found enrolled in {prog_code} - Semester {sem_val}.")
        return

    # Check unassigned vs assigned
    assigned_ids = set(db.scalars(select(Assignment.student_id).where(Assignment.practical_id == selected_practical.id)))
    unassigned = [s for s in students if s.id not in assigned_ids]
    already_assigned = [s for s in students if s.id in assigned_ids]

    # Metrics summary
    mcol1, mcol2, mcol3 = st.columns(3)
    with mcol1:
        st.metric(f"Total in {prog_code} Sem {sem_val}", len(students))
    with mcol2:
        st.metric("Eligible to Assign", len(unassigned))
    with mcol3:
        st.metric("Already Assigned", len(already_assigned))

    if not unassigned:
        st.success(f"This practical is already assigned to all {len(students)} enrolled student(s) in {prog_code} Semester {sem_val}.")
    else:
        choices = {s.id: f"{s.enrollment_no} · {s.user.full_name}" for s in unassigned}
        scope = st.radio(
            "Assignment Mode",
            [f"Assign to all eligible students ({len(unassigned)})", "Select specific students from list"],
            horizontal=True,
            key="assign_mode_radio",
        )
        if scope.startswith("Assign to all"):
            st.caption(f"Clicking the button below will immediately assign Practical P{selected_practical.practical_number} to all {len(unassigned)} students.")
            if st.button("Assign to all in one click", type="primary"):
                created = assign_practical(db, selected_practical, user_id, student_ids=[s.id for s in unassigned])
                st.success(f"Assigned practical to {created} student(s).")
                st.rerun()
        else:
            selected_students = st.multiselect(
                "Select students to receive this practical",
                list(choices),
                format_func=choices.get,
                key="assign_selected_students",
            )
            if st.button("Assign to selected students", type="primary"):
                if not selected_students:
                    st.error("Select at least one student from the list.")
                else:
                    created = assign_practical(db, selected_practical, user_id, student_ids=selected_students)
                    st.success(f"Assigned practical to {created} student(s).")
                    st.rerun()

    if already_assigned:
        with st.expander(f"View already assigned students ({len(already_assigned)})", expanded=False):
            assigned_rows = []
            for s in already_assigned:
                assign_rec = db.scalar(select(Assignment).where(Assignment.practical_id == selected_practical.id, Assignment.student_id == s.id))
                status_str = assign_rec.status if assign_rec else "Assigned"
                assigned_rows.append({"Enrollment": s.enrollment_no, "Name": s.user.full_name, "Status": status_str})
            st.dataframe(pd.DataFrame(assigned_rows), hide_index=True, width="stretch")


def _evaluation_ui(db, user_id: int, subject_labels: dict[int, str]) -> None:
    subjects = list(db.scalars(select(Subject).where(Subject.id.in_(subject_labels.keys())).order_by(Subject.code)))
    submissions = submissions_for_faculty(db, user_id)
    if not submissions:
        st.info("No submissions to evaluate for your subjects.")
        return
    subject_choices = {subject.id: f"{subject.code} · {subject.name}" for subject in subjects}
    subject_filter = st.selectbox(
        "Filter by subject",
        [None] + list(subject_choices),
        format_func=lambda value: "All subjects" if value is None else subject_choices[value],
        key="evaluate_subject_filter",
    )
    filtered = [item for item in submissions if subject_filter is None or item.assignment.practical.subject_id == subject_filter]
    if not filtered:
        st.info("No submissions for the selected subject.")
        return
    submission_choices = {
        item.id: f"{item.assignment.practical.subject.code} · P{item.assignment.practical.practical_number} · {item.assignment.student.enrollment_no}"
        for item in filtered
    }
    selected = st.selectbox("Submission to evaluate", list(submission_choices), format_func=lambda value: submission_choices[value], key="evaluate_submission_select")
    submission = db.get(Submission, selected)
    
    # Display Practical Context
    pract = submission.assignment.practical
    student = submission.assignment.student
    
    st.markdown("---")
    st.subheader(f"P{pract.practical_number}: {pract.title}")
    
    col_a, col_b = st.columns(2)
    col_a.write(f"**Student:** {student.user.full_name} ({student.enrollment_no})")
    col_b.write(f"**Deadline:** {submission.assignment.deadline.strftime('%d %b %Y, %I:%M %p')}")

    if pract.description:
        st.info(f"**Description:**\n\n{pract.description}")
        
    st.markdown("---")

    _url_type = _github_url_type(submission.github_url)
    _btn_label = "📄 Open submitted file" if _url_type == "file" else "📂 Open GitHub repository"
    st.link_button(_btn_label, submission.github_url)

    # Late submission notice — restrict grade options to B–F
    if submission.is_late:
        st.warning(
            "⏰ **Late submission** — this practical was submitted after the deadline. "
            "Grade **A** is not available for late submissions."
        )
        available_grades = [g for g in VALID_GRADES if g != "A"]
    else:
        available_grades = VALID_GRADES

    ai_key_remarks = f"ai_remarks_{submission.id}"
    ai_key_suggestions = f"ai_suggestions_{submission.id}"
    ai_key_questions = f"ai_questions_{submission.id}"

    if st.button("🤖 Analyze Code with AI", help="Uses Sarvam AI to review the code and suggest remarks."):
        with st.spinner("Fetching code and analyzing with Sarvam AI..."):
            try:
                from services.ai_service import fetch_github_code, analyze_code_with_sarvam
                code_content = fetch_github_code(submission.github_url)
                practical_obj = submission.assignment.practical
                ai_result = analyze_code_with_sarvam(
                    practical_title=practical_obj.title,
                    practical_desc=practical_obj.description or "No description provided.",
                    code_content=code_content
                )
                st.session_state[ai_key_remarks] = ai_result.get("remarks", "")
                st.session_state[ai_key_suggestions] = ai_result.get("suggestions", "")
                st.session_state[ai_key_questions] = ai_result.get("viva_questions", [])
                st.rerun()  # Rerun to populate the text areas in the form below
            except Exception as e:
                st.error(str(e))

    # Display Viva questions if generated
    viva_questions = st.session_state.get(ai_key_questions, [])
    if viva_questions:
        with st.expander("🎓 Suggested Viva Questions", expanded=True):
            st.caption("Ask these questions to gauge the student's understanding of their submitted code.")
            for idx, q in enumerate(viva_questions, 1):
                st.markdown(f"**Q{idx}:** {q}")

    with st.form("evaluation_form"):
        grade = st.selectbox("Grade", available_grades)
        
        # If there's an existing evaluation in the database, we use that as fallback if session state is empty
        default_remarks = submission.evaluation.remarks if submission.evaluation else ""
        default_suggestions = submission.evaluation.suggestions if submission.evaluation else ""
        
        remarks = st.text_area("Remarks", value=st.session_state.get(ai_key_remarks, default_remarks))
        suggestions = st.text_area("Suggestions", value=st.session_state.get(ai_key_suggestions, default_suggestions))
        
        if st.form_submit_button("Publish evaluation", type="primary"):
            grade_submission(db, submission.id, user_id, grade, remarks, suggestions)
            st.success("Evaluation published.")
            # Clear AI results from session state on successful publish
            st.session_state.pop(ai_key_remarks, None)
            st.session_state.pop(ai_key_suggestions, None)
            st.session_state.pop(ai_key_questions, None)
            st.rerun()


# ── PDF Header Settings ───────────────────────────────────────────────────────

def _pdf_header_settings_ui(db, user, subject_labels: dict[int, str]) -> None:
    st.subheader("PDF header configuration")
    st.caption(
        "Configure the institutional header printed at the top of every practical-list PDF for each subject. "
        "Changes take effect immediately the next time any PDF is generated."
    )

    subjects = list(db.scalars(select(Subject).where(Subject.id.in_(subject_labels.keys())).order_by(Subject.code)))
    if not subjects:
        st.info("No subjects assigned.")
        return

    selected_subj = st.selectbox(
        "Subject",
        subjects,
        format_func=lambda s: f"{s.code} · {s.name}",
        key="pdf_hdr_subj_select",
    )

    # Load (or auto-create) config
    cfg = get_or_create_subject_header_config(db, selected_subj.id, faculty_user=user)

    with st.form("pdf_header_form"):
        st.caption("All fields are pre-filled with sensible defaults; update as needed.")
        inst_name   = st.text_input("Institution name",  value=cfg.institution_name)
        dept_name   = st.text_input("Department name",   value=cfg.department_name)
        course_name = st.text_input("Course / programme name", value=cfg.course_name,
                                    placeholder="e.g. MCA Semester-I")
        acad_year   = st.text_input("Academic year",     value=cfg.academic_year,
                                    placeholder="e.g. 2026-27")
        teacher     = st.text_input("Subject teacher",   value=cfg.subject_teacher,
                                    placeholder="e.g. Shri Hasmukh Mer")
        subj_title  = st.text_area("Subject display title", value=cfg.subject_title,
                                   placeholder="e.g. Lab based on Python Ecosystem for AI Applications",
                                   height=80)

        save_col, preview_col = st.columns([1, 1])
        save_clicked = save_col.form_submit_button("💾 Save header configuration", type="primary")
        if save_clicked:
            update_subject_header_config(
                db,
                selected_subj.id,
                faculty_user=user,
                institution_name=inst_name.strip(),
                department_name=dept_name.strip(),
                course_name=course_name.strip(),
                academic_year=acad_year.strip(),
                subject_teacher=teacher.strip(),
                subject_title=subj_title.strip(),
            )
            st.success("Header configuration saved successfully.")
            st.rerun()

    # ── Faculty Digital Signature (Hybrid) ────────────────────────────────────
    st.markdown("---")
    st.subheader("Faculty digital signature (Optional)")
    st.caption(
        "Upload a clear image of your signature (PNG or JPG). When student practical-list PDFs are generated, "
        "this signature image and the exact evaluation date will appear in the 'Sign' column. "
        "If no image is uploaded, the system will print the faculty name/initials with the grading date. "
        "The sign area remains completely blank until evaluated."
    )

    import os
    from services.core_services import remove_signature_image, save_signature_image

    current_sig = cfg.signature_image_path or getattr(user, "signature_image_path", None)

    sig_col1, sig_col2 = st.columns([1, 1])
    if current_sig and os.path.isfile(current_sig):
        with sig_col1:
            st.markdown("**Current active signature:**")
            st.image(current_sig, caption="Uploaded Signature Preview", width=180)
            if st.button("🗑️ Remove signature", key=f"remove_sig_{selected_subj.id}"):
                remove_signature_image(current_sig)
                cfg.signature_image_path = None
                if hasattr(user, "signature_image_path"):
                    user.signature_image_path = None
                db.commit()
                st.success("Signature removed. Fallback digital text signature will be used.")
                st.rerun()

    with sig_col2:
        new_sig = st.file_uploader(
            "Upload new signature image (PNG / JPG)",
            type=["png", "jpg", "jpeg", "webp"],
            key=f"upload_sig_{selected_subj.id}",
            help="For best results, use a cropped PNG with a white or transparent background.",
        )
        if new_sig is not None:
            if st.button("💾 Save signature", key=f"save_sig_{selected_subj.id}", type="primary"):
                saved_path = save_signature_image(new_sig.getvalue(), new_sig.name, "subject", selected_subj.id)
                update_subject_header_config(db, selected_subj.id, faculty_user=user, signature_image_path=saved_path)
                if hasattr(user, "signature_image_path"):
                    user.signature_image_path = saved_path
                db.commit()
                st.success("Signature saved! It will now appear on all evaluated practical lists.")
                st.rerun()

    st.markdown("---")
    st.markdown("**Download blank master practical list for this subject:**")
    st.caption("Prints the header and full practical table with blank date/grade/sign/remarks columns for classroom distribution.")
    try:
        practicals_exist = db.scalar(
            select(Practical).where(Practical.subject_id == selected_subj.id)
        )
        if practicals_exist:
            master_buf = generate_blank_master_pdf(db, selected_subj.id)
            st.download_button(
                label="📄 Download Master Practical List (Blank)",
                data=master_buf,
                file_name=f"Master_Practical_List_{selected_subj.code}.pdf",
                mime="application/pdf",
                key=f"master_pdf_{selected_subj.id}",
            )
        else:
            st.info("No practicals exist for this subject yet. Create practicals first in the 'Create / manage practicals' tab.")
    except Exception as _err:
        st.error(f"Could not generate PDF: {_err}")


# ── Student Progress & Reports ────────────────────────────────────────────────

def _student_reports_ui(db, user_id: int, subject_labels: dict[int, str]) -> None:
    st.subheader("Student progress & reports")
    st.caption("Track student completion per subject and download personalized practical-list PDFs.")

    subjects = list(db.scalars(select(Subject).where(Subject.id.in_(subject_labels.keys())).order_by(Subject.code)))
    if not subjects:
        st.info("No subjects assigned.")
        return

    rpt_subj = st.selectbox(
        "Select subject",
        subjects,
        format_func=lambda s: f"{s.code} · {s.name}",
        key="rpt_subj_select",
    )

    progress_rows = get_student_subject_progress(db, rpt_subj.id)

    if not progress_rows:
        st.info("No students have been assigned practicals in this subject yet.")
        return

    # ── Overview metrics ──────────────────────────────────────────────────────
    total_students  = len(progress_rows)
    fully_submitted = sum(1 for r in progress_rows if r["submitted"] == r["total"])
    fully_graded    = sum(1 for r in progress_rows if r["graded"] == r["total"])
    total_prac      = progress_rows[0]["total"] if progress_rows else 0

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Students", total_students)
    mc2.metric("Total practicals", total_prac)
    mc3.metric("Fully submitted", fully_submitted)
    mc4.metric("Fully graded", fully_graded)

    st.markdown("---")

    # ── Student roster table with inline PDF download ─────────────────────────
    st.markdown("**Student roster**")

    for row in sorted(progress_rows, key=lambda r: r["enrollment_no"]):
        sid       = row["student_id"]
        enr       = row["enrollment_no"]
        name      = row["full_name"]
        submitted = row["submitted"]
        graded    = row["graded"]
        total     = row["total"]
        pct       = row["completion_pct"]

        col_info, col_bar, col_btn = st.columns([3, 4, 2])

        with col_info:
            st.markdown(f"**{enr}** — {name}")
            st.caption(f"Submitted: {submitted}/{total} · Graded: {graded}/{total}")

        with col_bar:
            st.progress(pct / 100, text=f"{pct}% submitted")

        with col_btn:
            pdf_key = f"fac_pdf_{sid}_{rpt_subj.id}"
            try:
                pdf_buf = generate_practical_list_pdf(db, subject_id=rpt_subj.id, student_id=sid)
                st.download_button(
                    label="📥 PDF",
                    data=pdf_buf,
                    file_name=f"Practical_List_{rpt_subj.code}_{enr}.pdf",
                    mime="application/pdf",
                    key=pdf_key,
                    help=f"Download practical list PDF for {name}",
                )
            except Exception as _e:
                st.caption(f"Error: {_e}")

        st.divider()

    # ── Individual student detail drill-down ─────────────────────────────────
    st.markdown("---")
    st.markdown("**Inspect an individual student's progress**")

    student_choices = {r["student_id"]: f"{r['enrollment_no']} · {r['full_name']}" for r in progress_rows}
    drill_sid = st.selectbox(
        "Select student",
        list(student_choices),
        format_func=student_choices.get,
        key="rpt_drill_student",
    )

    if drill_sid:
        from models.schema import Assignment, Practical, Submission
        from sqlalchemy.orm import joinedload

        prac_ids_subj = [
            p.id for p in db.scalars(select(Practical).where(Practical.subject_id == rpt_subj.id))
        ]
        student_assignments = list(db.scalars(
            select(Assignment)
            .where(Assignment.student_id == drill_sid, Assignment.practical_id.in_(prac_ids_subj))
            .options(
                joinedload(Assignment.practical),
                joinedload(Assignment.submission).joinedload(Submission.evaluation),
            )
            .order_by(Assignment.id)
        ))

        if not student_assignments:
            st.info("This student has no assignments for this subject yet.")
        else:
            for a in student_assignments:
                prac = a.practical
                sub  = a.submission
                ev   = sub.evaluation if sub else None

                status_icon = (
                    "✅" if (ev and ev.published) else
                    "🟡" if sub else
                    "🔵"
                )
                grade_str   = ev.grade if (ev and ev.published) else "—"
                remarks_str = ev.remarks if (ev and ev.published) else "—"

                pcode = prac.practical_code or f"P{prac.practical_number}"
                with st.container(border=True):
                    c1, c2, c3, c4 = st.columns([2, 4, 2, 2])
                    c1.markdown(f"**{pcode}**")
                    c2.markdown(prac.title)
                    c3.markdown(f"{status_icon} Grade: **{grade_str}**")
                    c4.markdown(f"Remarks: {remarks_str[:60] + '…' if len(remarks_str) > 60 else remarks_str}")

