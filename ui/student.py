from collections import defaultdict
from datetime import datetime, timezone

import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from models.schema import Assignment, Practical, Student, Subject, Submission
from services.core_services import _github_url_type, save_submission


# ── helpers ──────────────────────────────────────────────────────────────────

def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _status_badge(assignment: Assignment) -> str:
    """Return a coloured emoji badge for the assignment status."""
    status = assignment.status
    if status == "Submitted":
        return "🟡 Submitted"
    if status == "Late":
        return "🔴 Late"
    if assignment.submission and assignment.submission.evaluation and assignment.submission.evaluation.published:
        return "✅ Graded"
    if _utc_now() > assignment.deadline and not assignment.submission:
        return "⛔ Overdue"
    return "🔵 Pending"


def _difficulty_badge(difficulty: str) -> str:
    icons = {"Easy": "🟢", "Medium": "🟠", "Hard": "🔴"}
    return f"{icons.get(difficulty, '⚪')} {difficulty}"


# ── submission form ───────────────────────────────────────────────────────────

def _render_submission_form(db, assignment: Assignment, student: Student, default_url: str = "", default_branch: str = "main", default_notes: str = "") -> None:
    with st.form(f"submit-{assignment.id}"):
        url = st.text_input(
            "GitHub URL",
            value=default_url,
            placeholder=(
                "https://github.com/username/repo  OR  "
                "https://github.com/username/repo/blob/main/Solution.java"
            ),
            help=(
                "Paste either:\n"
                "- **Repository URL** – `https://github.com/username/repo`\n"
                "- **Direct file URL** – `https://github.com/username/repo/blob/main/Solution.java`\n\n"
                "Supported file types: `.java`, `.py`, `.js`, `.html`, `.css`, `.cpp`, `.c`, `.ts`, and more."
            ),
        )
        branch = st.text_input("Branch", value=default_branch)
        notes = st.text_area("Documentation / remarks", value=default_notes)

        if st.form_submit_button("Submit", type="primary"):
            try:
                submission = save_submission(
                    db,
                    assignment.id,
                    url,
                    student.user_id,
                    branch=branch,
                    documentation=notes,
                )
                if submission.is_late:
                    st.warning("Late submission recorded ⚠️ Your submission was accepted but marked as late.")
                else:
                    st.success("Submission recorded ✅")
                st.rerun()
            except ValueError as error:
                st.error(str(error))


# ── one practical card ────────────────────────────────────────────────────────

def _render_practical(db, assignment: Assignment, student: Student) -> None:
    practical = assignment.practical
    badge = _status_badge(assignment)
    is_overdue = _utc_now() > assignment.deadline and not assignment.submission

    expander_label = (
        f"P{practical.practical_number} · {practical.title}  —  {badge}"
    )

    with st.expander(expander_label, expanded=False):
        # Meta row
        cols = st.columns([2, 2, 2])
        with cols[0]:
            st.caption(f"📅 Deadline: {assignment.deadline:%d %b %Y, %I:%M %p}")
        with cols[1]:
            st.caption(_difficulty_badge(practical.difficulty))
            
        if practical.description:
            st.write(practical.description)
        if practical.learning_outcome:
            st.info(f"**Learning outcome:** {practical.learning_outcome}")

        st.divider()

        if assignment.submission:
            sub = assignment.submission
            url_type = _github_url_type(sub.github_url)
            btn_label = "📄 Open submitted file" if url_type == "file" else "📂 Open GitHub repository"
            st.link_button(btn_label, sub.github_url)

            detail_cols = st.columns(2)
            with detail_cols[0]:
                if sub.branch and sub.branch not in ("", "main"):
                    st.caption(f"Branch: `{sub.branch}`")
                if sub.is_late:
                    st.warning("Submitted late")
            with detail_cols[1]:
                if sub.documentation:
                    st.caption(f"📝 {sub.documentation}")

            if sub.evaluation and sub.evaluation.published:
                ev = sub.evaluation
                st.success(f"**Grade: {ev.grade}** · Marks: {ev.total_marks} / {practical.max_marks}")
                if ev.remarks:
                    st.write(f"*Remarks:* {ev.remarks}")
                if ev.suggestions:
                    st.write(f"*Suggestions:* {ev.suggestions}")
            elif sub.evaluation:
                st.info("Evaluation in progress — not published yet.")
            else:
                st.info("Submitted — awaiting evaluation.")
                
                # Allow edit if before deadline
                if _utc_now() <= assignment.deadline:
                    with st.expander("✏️ Edit Submission", expanded=False):
                        st.caption("You can update your submission link before the deadline.")
                        _render_submission_form(
                            db, 
                            assignment, 
                            student, 
                            default_url=sub.github_url, 
                            default_branch=sub.branch, 
                            default_notes=sub.documentation
                        )
                else:
                    st.caption("🔒 The deadline has passed. You can no longer edit this submission.")

        elif is_overdue:
            st.warning(
                "⏰ **Deadline has passed.** You can still submit, but it will be "
                "recorded as a **late submission** and may affect your evaluation."
            )
            _render_submission_form(db, assignment, student)

        else:
            _render_submission_form(db, assignment, student)


# ── main page ─────────────────────────────────────────────────────────────────

def student_page(db, student: Student) -> None:
    st.title("My practicals")

    # Load all assignments with their nested relationships eagerly to avoid N+1 queries
    assignments: list[Assignment] = db.scalars(
        select(Assignment)
        .where(Assignment.student_id == student.id)
        .options(
            joinedload(Assignment.practical).joinedload(Practical.subject),
            joinedload(Assignment.submission).joinedload(Submission.evaluation),
        )
        .order_by(Assignment.deadline)
    ).unique().all()

    if not assignments:
        st.info("No practicals assigned yet. Check back later.")
        return

    # ── top-level summary strip ───────────────────────────────────────────────
    total   = len(assignments)
    submitted = sum(1 for a in assignments if a.submission)
    graded  = sum(
        1 for a in assignments
        if a.submission and a.submission.evaluation and a.submission.evaluation.published
    )
    pending = total - submitted

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total", total)
    m2.metric("Pending", pending, delta=None)
    m3.metric("Submitted", submitted)
    m4.metric("Graded", graded)

    st.divider()

    # ── group by subject ──────────────────────────────────────────────────────
    # subject_id → list[Assignment]
    by_subject: dict[int, list[Assignment]] = defaultdict(list)
    subject_meta: dict[int, Subject] = {}

    for a in assignments:
        subj = a.practical.subject
        by_subject[subj.id].append(a)
        subject_meta[subj.id] = subj

    # Sort subjects by code for a stable, predictable order
    sorted_subject_ids = sorted(subject_meta, key=lambda sid: subject_meta[sid].code)

    for sid in sorted_subject_ids:
        subj = subject_meta[sid]
        subj_assignments = by_subject[sid]

        # Per-subject stats
        s_total     = len(subj_assignments)
        s_submitted = sum(1 for a in subj_assignments if a.submission)
        s_graded    = sum(
            1 for a in subj_assignments
            if a.submission and a.submission.evaluation and a.submission.evaluation.published
        )
        s_pending   = s_total - s_submitted

        # Section header
        sem_label = f"Sem {subj.semester}" if subj.semester else ""
        prog_label = subj.program.code if subj.program else ""
        meta_parts = [p for p in [prog_label, sem_label] if p]
        meta_str   = f"  ·  {' · '.join(meta_parts)}" if meta_parts else ""

        st.subheader(f"📘 {subj.code} — {subj.name}{meta_str}")

        # Subject-level progress bar
        progress = s_submitted / s_total if s_total else 0
        st.progress(progress, text=f"{s_submitted}/{s_total} submitted · {s_graded} graded · {s_pending} pending")

        # Sort: pending/overdue first, then submitted, then graded
        def sort_key(a: Assignment):
            if a.submission and a.submission.evaluation and a.submission.evaluation.published:
                return 3
            if a.submission:
                return 2
            return 1

        for assignment in sorted(subj_assignments, key=sort_key):
            _render_practical(db, assignment, student)

        st.write("")  # visual breathing room between subjects
