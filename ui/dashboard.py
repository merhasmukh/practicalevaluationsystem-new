from collections import Counter
from datetime import date, timedelta
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import select
from models.schema import Assignment, Student, User


def card(label: str, value: object) -> None:
    st.markdown(f'<div class="metric-card"><small>{label}</small><h2>{value}</h2></div>', unsafe_allow_html=True)


def _get_chart_theme() -> dict[str, str]:
    try:
        base = st.get_option("theme.base")
    except Exception:
        base = "light"
    dark = str(base).lower() == "dark"
    return {
        "primary": "#1E3A8A",
        "secondary": "#0F766E",
        "accent": "#F59E0B",
        "success": "#16A34A",
        "warning": "#F97316",
        "danger": "#DC2626",
        "bg": "#111827" if dark else "#F8FAFC",
        "card": "#111827" if dark else "#FFFFFF",
        "text": "#F8FAFC" if dark else "#1F2937",
        "muted": "#CBD5E1" if dark else "#6B7280",
        "border": "#334155" if dark else "#E5E7EB",
    }


def _render_plot(fig, title: str | None = None) -> None:
    colors = _get_chart_theme()
    fig.update_layout(
        title=title,
        template="plotly_white",
        paper_bgcolor=colors["card"],
        plot_bgcolor=colors["card"],
        font=dict(color=colors["text"], family="Inter, Arial, sans-serif"),
        legend=dict(title="", bgcolor="rgba(0,0,0,0)", orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0.0),
        margin=dict(l=10, r=10, t=50, b=40),
        height=400,
        bargap=0.5,
    )
    st.plotly_chart(
        fig,
        width="stretch",
        config={"displayModeBar": True, "displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]},
    )


def _grade_score(grade: object) -> float:
    mapping = {"O": 10, "A+": 9.5, "A": 9, "B+": 8, "B": 7, "C": 6, "D": 5, "E": 4, "F": 0}
    value = str(grade or "").strip().upper()
    return mapping.get(value, 0.0)


def _build_filtered_assignment_frame(assignments, db) -> pd.DataFrame:
    rows = []
    for assignment in assignments:
        practical = assignment.practical
        student = assignment.student
        user = student.user if student else None
        evaluation = assignment.submission.evaluation if assignment.submission and assignment.submission.evaluation else None
        rows.append(
            {
                "Student": user.full_name if user else "-",
                "Enrollment": student.enrollment_no if student else "-",
                "Practical": f"P{practical.practical_number} · {practical.title}" if practical else "-",
                "Subject": practical.subject.code if practical and practical.subject else "-",
                "Department": practical.subject.department.name if practical and practical.subject and practical.subject.department else "-",
                "Status": assignment.status,
                "Assigned On": assignment.assigned_at.strftime("%Y-%m-%d") if assignment.assigned_at else "-",
                "Submitted On": assignment.submission.submitted_at.strftime("%Y-%m-%d") if assignment.submission and assignment.submission.submitted_at else "-",
                "Grade": evaluation.grade if evaluation and evaluation.grade else "-",
                "Evaluation Published": bool(evaluation),
            }
        )
    return pd.DataFrame(rows)


def _score_to_grade(score: float) -> str:
    if score >= 9.5:
        return "A+"
    if score >= 8.5:
        return "A"
    if score >= 7.5:
        return "B+"
    if score >= 6.5:
        return "B"
    if score >= 5.5:
        return "C"
    return "F"


def dashboard(db, user: User, active_role: str | None = None, *args, **kwargs) -> None:
    role_val = active_role or kwargs.get("active_role") or (user.role.name if getattr(user, "role", None) else "Administrator")
    effective_role = str(role_val)
    first_name = user.full_name.split()[0] if user.full_name else user.username
    st.title(f"Good day, {first_name}")

    if effective_role == "Administrator":
        st.caption(f"Viewing as **{effective_role}** · Choose a dashboard view to switch between practical tracking and interactive analytics.")
        dashboard_view = st.radio(
            "Dashboard view",
            ["Existing system", "Interactive analytics"],
            horizontal=True,
            index=0,
        )
    else:
        st.caption(f"Viewing as **{effective_role}** · Practical tracking and submission search.")
        dashboard_view = "Existing system"

    if dashboard_view != "Interactive analytics":
        if effective_role == "Faculty":
            st.subheader("Subject analytics overview")
            from models.schema import Practical
            from services.core_services import faculty_subject_ids
            
            subject_ids = faculty_subject_ids(db, user.id)
            if subject_ids:
                faculty_assignments = db.scalars(
                    select(Assignment).join(Assignment.practical).where(Practical.subject_id.in_(subject_ids))
                ).all()
                
                subject_stats = {}
                for a in faculty_assignments:
                    subj_label = f"{a.practical.subject.code} - {a.practical.subject.name}"
                    if subj_label not in subject_stats:
                        subject_stats[subj_label] = {"Graded": 0, "Pending Evaluation": 0, "Pending Submission": 0}
                    
                    if a.submission:
                        if a.submission.evaluation and a.submission.evaluation.published:
                            subject_stats[subj_label]["Graded"] += 1
                        else:
                            subject_stats[subj_label]["Pending Evaluation"] += 1
                    else:
                        subject_stats[subj_label]["Pending Submission"] += 1
                        
                if subject_stats:
                    subj_choices = ["All Subjects"] + list(subject_stats.keys())
                    selected_subj = st.selectbox("Filter Analytics by Subject", subj_choices)
                    
                    if selected_subj == "All Subjects":
                        agg_graded = sum(s["Graded"] for s in subject_stats.values())
                        agg_pending_eval = sum(s["Pending Evaluation"] for s in subject_stats.values())
                        agg_pending_sub = sum(s["Pending Submission"] for s in subject_stats.values())
                        
                        table_df = pd.DataFrame.from_dict(subject_stats, orient="index").reset_index()
                        table_df.rename(columns={"index": "Subject"}, inplace=True)
                    else:
                        agg_graded = subject_stats[selected_subj]["Graded"]
                        agg_pending_eval = subject_stats[selected_subj]["Pending Evaluation"]
                        agg_pending_sub = subject_stats[selected_subj]["Pending Submission"]
                        
                        table_df = pd.DataFrame([{
                            "Subject": selected_subj, 
                            "Graded": agg_graded, 
                            "Pending Evaluation": agg_pending_eval, 
                            "Pending Submission": agg_pending_sub
                        }])
                        
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        card("Graded", agg_graded)
                    with c2:
                        card("Pending Evaluation", agg_pending_eval)
                    with c3:
                        card("Pending Submission", agg_pending_sub)
                        
                    st.markdown("<br>", unsafe_allow_html=True)
                    st.dataframe(table_df, hide_index=True, use_container_width=True)
            st.divider()
            
        st.subheader("Search and filter practicals")
        search_term = st.text_input("Search by name, enrollment, subject, or status", placeholder="Type to filter instantly")
        status_filter = st.selectbox("Status", ["All", "Assigned", "Submitted", "Late", "Evaluated"], index=0)

        from models.schema import Practical
        from services.core_services import faculty_subject_ids
        
        query = select(Assignment).join(Assignment.practical).join(Assignment.student).join(Student.user)
        if effective_role == "Faculty":
            subject_ids = faculty_subject_ids(db, user.id)
            query = query.where(Practical.subject_id.in_(subject_ids))
        assignments = db.scalars(query.order_by(Assignment.deadline)).all()
        filtered = []
        for assignment in assignments:
            haystack = " ".join([
                assignment.practical.title or "",
                assignment.practical.subject.code or "",
                assignment.practical.subject.name or "",
                assignment.student.enrollment_no or "",
                assignment.student.user.full_name or "",
                assignment.status or "",
            ]).lower()
            if search_term and search_term.lower() not in haystack:
                continue
            if status_filter != "All" and assignment.status != status_filter:
                continue
            filtered.append(assignment)

        if filtered:
            frame = pd.DataFrame([
                {
                    "Student": assignment.student.user.full_name,
                    "Enrollment": assignment.student.enrollment_no,
                    "Practical": f"P{assignment.practical.practical_number} · {assignment.practical.title}",
                    "Subject": assignment.practical.subject.code,
                    "Status": assignment.status,
                }
                for assignment in filtered
            ])
            st.dataframe(frame, hide_index=True, width="stretch")
        else:
            st.info("No matching practicals found.")
        return

    from models.schema import Practical
    from services.core_services import faculty_subject_ids
    
    query = select(Assignment).order_by(Assignment.assigned_at)
    if effective_role == "Faculty":
        subject_ids = faculty_subject_ids(db, user.id)
        query = query.join(Assignment.practical).where(Practical.subject_id.in_(subject_ids))
    base_assignments = db.scalars(query).all()
    if not base_assignments:
        st.info("No practical assignments have been created yet. Create data to populate the dashboard.")
        return

    years = sorted({assignment.assigned_at.year for assignment in base_assignments if assignment.assigned_at}, reverse=True)
    departments = sorted({assignment.practical.subject.department.name for assignment in base_assignments if assignment.practical and assignment.practical.subject and assignment.practical.subject.department})
    semesters = sorted({assignment.student.semester for assignment in base_assignments if assignment.student})
    subjects = sorted({assignment.practical.subject.name for assignment in base_assignments if assignment.practical and assignment.practical.subject})
    faculties = sorted({db.get(User, assignment.practical.created_by).full_name for assignment in base_assignments if assignment.practical and assignment.practical.created_by and db.get(User, assignment.practical.created_by)})
    practicals = sorted({f"P{assignment.practical.practical_number} · {assignment.practical.title}" for assignment in base_assignments if assignment.practical})
    students = sorted({assignment.student.user.full_name for assignment in base_assignments if assignment.student and assignment.student.user})

    with st.sidebar:
        st.subheader("Global filters")
        academic_year = st.selectbox("Academic year", ["All"] + years, index=0)
        department = st.selectbox("Department", ["All"] + departments, index=0)
        semester = st.selectbox("Semester", ["All"] + semesters, index=0)
        subject = st.selectbox("Subject", ["All"] + subjects, index=0)
        faculty = st.selectbox("Faculty", ["All"] + faculties, index=0)
        practical = st.selectbox("Practical", ["All"] + practicals, index=0)
        student = st.selectbox("Student", ["All"] + students, index=0)
        grade = st.selectbox("Grade", ["All", "O", "A+", "A", "B+", "B", "C", "D", "E", "F"], index=0)
        evaluation_status = st.selectbox("Evaluation status", ["All", "Assigned", "Submitted", "Late", "Evaluated"], index=0)
        date_range = st.date_input("Date range", value=(date.today() - timedelta(days=90), date.today()))

    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date = end_date = date_range

    filtered = []
    for assignment in base_assignments:
        practical_obj = assignment.practical
        subject_obj = practical_obj.subject if practical_obj else None
        student_obj = assignment.student
        user_obj = student_obj.user if student_obj else None
        evaluation = assignment.submission.evaluation if assignment.submission and assignment.submission.evaluation else None

        if academic_year != "All" and assignment.assigned_at.year != academic_year:
            continue
        if department != "All" and (not subject_obj or subject_obj.department.name != department):
            continue
        if semester != "All" and (not student_obj or student_obj.semester != semester):
            continue
        if subject != "All" and (not subject_obj or subject_obj.name != subject):
            continue
        if faculty != "All":
            creator = db.get(User, practical_obj.created_by) if practical_obj else None
            if not creator or creator.full_name != faculty:
                continue
        if practical != "All" and (not practical_obj or f"P{practical_obj.practical_number} · {practical_obj.title}" != practical):
            continue
        if student != "All" and (not user_obj or user_obj.full_name != student):
            continue
        if grade != "All" and (not evaluation or str(evaluation.grade).upper() != grade.upper()):
            continue
        if evaluation_status != "All" and assignment.status != evaluation_status:
            continue
        if start_date and assignment.assigned_at.date() < start_date:
            continue
        if end_date and assignment.assigned_at.date() > end_date:
            continue
        filtered.append(assignment)

    if not filtered:
        st.info("No matching records for the current filters. Adjust the filters to broaden the results.")
        return

    evaluations = [assignment.submission.evaluation for assignment in filtered if assignment.submission and assignment.submission.evaluation]
    scores = [_grade_score(evaluation.grade) for evaluation in evaluations]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
    pass_count = sum(1 for evaluation in evaluations if str(evaluation.grade).upper() != "F")
    fail_count = len(evaluations) - pass_count
    pass_percentage = round((pass_count / len(evaluations)) * 100, 1) if evaluations else 0.0
    fail_percentage = round(100 - pass_percentage, 1) if evaluations else 0.0

    colors = _get_chart_theme()
    filtered_frame = _build_filtered_assignment_frame(filtered, db)

    st.subheader("Executive overview")
    cols = st.columns(5)
    metric_items = [
        ("Average Grade", _score_to_grade(avg_score)),
        ("Highest Grade", max([str(evaluation.grade).upper() for evaluation in evaluations], key=lambda item: _grade_score(item), default="-")),
        ("Lowest Grade", min([str(evaluation.grade).upper() for evaluation in evaluations], key=lambda item: _grade_score(item), default="-")),
        ("Pass %", f"{pass_percentage:.1f}%"),
        ("Fail %", f"{fail_percentage:.1f}%"),
    ]
    for column, (label, value) in zip(cols, metric_items):
        with column:
            st.markdown(f'<div class="metric-card"><small>{label}</small><h3>{value}</h3></div>', unsafe_allow_html=True)

    st.subheader("Interactive analytics")
    grade_counts = Counter(str(evaluation.grade).upper() for evaluation in evaluations)
    grade_order = ["O", "A+", "A", "B+", "B", "C", "D", "E", "F"]
    grade_frame = pd.DataFrame({"Grade": grade_order, "Count": [grade_counts.get(item, 0) for item in grade_order]})
    grade_fig = px.bar(grade_frame, x="Grade", y="Count", color="Grade", color_discrete_sequence=[colors["primary"], colors["secondary"], colors["accent"], colors["warning"], colors["success"], colors["danger"], colors["primary"], colors["secondary"], colors["muted"]])
    _render_plot(grade_fig, "Grade distribution")

    subject_scores = {}
    for assignment in filtered:
        practical_obj = assignment.practical
        subject_obj = practical_obj.subject if practical_obj else None
        if not subject_obj:
            continue
        subject_key = f"{subject_obj.code} · {subject_obj.name}"
        subject_scores.setdefault(subject_key, []).append(_grade_score(assignment.submission.evaluation.grade) if assignment.submission and assignment.submission.evaluation else 0.0)

    subject_rows = []
    for subject_name, values in subject_scores.items():
        if values:
            subject_rows.append({"Subject": subject_name, "Average Grade": round(sum(values) / len(values), 1), "Highest Grade": round(max(values), 1), "Lowest Grade": round(min(values), 1), "Evaluation Count": len(values)})
    if subject_rows:
        subject_frame = pd.DataFrame(subject_rows).sort_values("Average Grade", ascending=False)
        subject_fig = go.Figure()
        subject_fig.add_trace(go.Bar(name="Average", x=subject_frame["Subject"], y=subject_frame["Average Grade"], marker_color=colors["primary"]))
        subject_fig.add_trace(go.Bar(name="Highest", x=subject_frame["Subject"], y=subject_frame["Highest Grade"], marker_color=colors["accent"]))
        subject_fig.add_trace(go.Bar(name="Lowest", x=subject_frame["Subject"], y=subject_frame["Lowest Grade"], marker_color=colors["secondary"]))
        _render_plot(subject_fig, "Subject-wise performance")

    semester_stats = {}
    for assignment in filtered:
        student_obj = assignment.student
        semester_value = student_obj.semester if student_obj else 0
        entry = semester_stats.setdefault(semester_value, {"Total Students": set(), "Completed Evaluations": 0, "Pending Evaluations": 0, "Average Grade": []})
        entry["Total Students"].add(student_obj.id if student_obj else 0)
        if assignment.status == "Evaluated" and assignment.submission and assignment.submission.evaluation:
            entry["Completed Evaluations"] += 1
            entry["Average Grade"].append(_grade_score(assignment.submission.evaluation.grade))
        else:
            entry["Pending Evaluations"] += 1

    semester_rows = []
    for semester_value, entry in sorted(semester_stats.items()):
        values = entry["Average Grade"]
        semester_rows.append({"Semester": f"Sem {semester_value}", "Total Students": len(entry["Total Students"]), "Completed Evaluation": entry["Completed Evaluations"], "Pending Evaluation": entry["Pending Evaluations"], "Average Grade": round(sum(values) / len(values), 1) if values else 0.0})
    if semester_rows:
        semester_frame = pd.DataFrame(semester_rows)
        semester_fig = go.Figure()
        semester_fig.add_trace(go.Bar(name="Completed", x=semester_frame["Semester"], y=semester_frame["Completed Evaluation"], marker_color=colors["success"]))
        semester_fig.add_trace(go.Bar(name="Pending", x=semester_frame["Semester"], y=semester_frame["Pending Evaluation"], marker_color=colors["warning"]))
        semester_fig.add_trace(go.Scatter(name="Average Grade", x=semester_frame["Semester"], y=semester_frame["Average Grade"], mode="lines+markers", yaxis="y2", line=dict(color=colors["primary"], width=3)))
        semester_fig.update_layout(yaxis2=dict(overlaying="y", side="right"))
        _render_plot(semester_fig, "Semester-wise analytics")

    status_counter = Counter()
    for assignment in filtered:
        if assignment.status == "Late" or (assignment.submission and assignment.submission.is_late):
            status_counter["Late Submission"] += 1
        elif assignment.submission:
            status_counter["Submitted"] += 1
        else:
            status_counter["Pending"] += 1
    status_frame = pd.DataFrame({"Status": list(status_counter.keys()), "Count": list(status_counter.values())})
    status_fig = px.pie(status_frame, values="Count", names="Status", color_discrete_sequence=[colors["success"], colors["primary"], colors["warning"], colors["danger"]])
    _render_plot(status_fig, "Practical submission status")

    faculty_stats = {}
    for assignment in filtered:
        practical_obj = assignment.practical
        creator = db.get(User, practical_obj.created_by) if practical_obj else None
        if not creator or creator.role.name != "Faculty":
            continue
        entry = faculty_stats.setdefault(creator.full_name, {"Assigned Practicals": 0, "Completed": 0, "Pending": 0, "Evaluation Time": []})
        entry["Assigned Practicals"] += 1
        if assignment.submission and assignment.submission.evaluation:
            entry["Completed"] += 1
            if assignment.submission.submitted_at and assignment.submission.evaluation.evaluated_at:
                delta = (assignment.submission.evaluation.evaluated_at - assignment.submission.submitted_at).days
                entry["Evaluation Time"].append(delta)
        else:
            entry["Pending"] += 1
    faculty_rows = []
    for name, entry in faculty_stats.items():
        faculty_rows.append({"Faculty Name": name, "Assigned Practicals": entry["Assigned Practicals"], "Completed": entry["Completed"], "Pending": entry["Pending"], "Average Evaluation Time": round(sum(entry["Evaluation Time"]) / len(entry["Evaluation Time"]), 1) if entry["Evaluation Time"] else 0.0})
    if faculty_rows:
        faculty_frame = pd.DataFrame(faculty_rows).sort_values("Completed", ascending=False)
        faculty_fig = px.bar(faculty_frame, x="Completed", y="Faculty Name", orientation="h", text="Completed", color_discrete_sequence=[colors["primary"]])
        _render_plot(faculty_fig, "Faculty evaluation performance")

    department_stats = {}
    for assignment in filtered:
        subject_obj = assignment.practical.subject if assignment.practical else None
        if not subject_obj or not subject_obj.department:
            continue
        department_name = subject_obj.department.name
        entry = department_stats.setdefault(department_name, {"Students": set(), "Faculty": set(), "Subjects": set(), "Practicals": set()})
        if assignment.student:
            entry["Students"].add(assignment.student.id)
        creator = db.get(User, assignment.practical.created_by) if assignment.practical else None
        if creator:
            entry["Faculty"].add(creator.id)
        entry["Subjects"].add(subject_obj.id)
        entry["Practicals"].add(assignment.practical.id)
    department_rows = []
    for name, entry in department_stats.items():
        department_rows.append({"Department": name, "Students": len(entry["Students"]), "Faculty": len(entry["Faculty"]), "Subjects": len(entry["Subjects"]), "Practicals": len(entry["Practicals"])})
    if department_rows:
        department_frame = pd.DataFrame(department_rows)
        department_fig = go.Figure()
        department_fig.add_trace(go.Bar(name="Students", x=department_frame["Department"], y=department_frame["Students"], marker_color=colors["primary"]))
        department_fig.add_trace(go.Bar(name="Faculty", x=department_frame["Department"], y=department_frame["Faculty"], marker_color=colors["secondary"]))
        department_fig.add_trace(go.Bar(name="Subjects", x=department_frame["Department"], y=department_frame["Subjects"], marker_color=colors["accent"]))
        department_fig.add_trace(go.Bar(name="Practicals", x=department_frame["Department"], y=department_frame["Practicals"], marker_color=colors["warning"]))
        _render_plot(department_fig, "Department-wise statistics")

    trend_rows = []
    for evaluation in evaluations:
        if evaluation.evaluated_at:
            month_label = evaluation.evaluated_at.strftime("%b %Y")
            trend_rows.append({"Month": month_label})
    if trend_rows:
        trend_frame = pd.DataFrame(trend_rows).value_counts().reset_index(name="Count")
        trend_frame.columns = ["Month", "Count"]
        trend_frame = trend_frame.sort_values("Month")
        trend_fig = px.line(trend_frame, x="Month", y="Count", markers=True, color_discrete_sequence=[colors["primary"]])
        _render_plot(trend_fig, "Monthly evaluation trend")

    timeline_rows = []
    for assignment in filtered:
        if assignment.assigned_at and assignment.submission and assignment.submission.submitted_at:
            finish_time = assignment.submission.evaluation.evaluated_at if assignment.submission and assignment.submission.evaluation and assignment.submission.evaluation.evaluated_at else assignment.submission.submitted_at
            timeline_rows.append({"Task": f"P{assignment.practical.practical_number}", "Start": assignment.assigned_at, "Finish": finish_time})
    if timeline_rows:
        timeline_frame = pd.DataFrame(timeline_rows)
        timeline_fig = px.timeline(timeline_frame, x_start="Start", x_end="Finish", y="Task", color="Task", color_discrete_sequence=[colors["primary"], colors["secondary"], colors["accent"], colors["warning"], colors["success"]])
        _render_plot(timeline_fig, "Practical completion timeline")

    course_rows = []
    course_map = {}
    for assignment in filtered:
        student_obj = assignment.student
        program = student_obj.program if student_obj else "Unknown"
        entry = course_map.setdefault(program, {"Average Grade": [], "Pass": 0, "Total": 0})
        if assignment.submission and assignment.submission.evaluation:
            entry["Average Grade"].append(_grade_score(assignment.submission.evaluation.grade))
            if str(assignment.submission.evaluation.grade).upper() != "F":
                entry["Pass"] += 1
        entry["Total"] += 1
    for course_name, entry in course_map.items():
        scores = entry["Average Grade"]
        course_rows.append({"Course": course_name, "Average Grade": round(sum(scores) / len(scores), 1) if scores else 0.0, "Pass %": round((entry["Pass"] / entry["Total"]) * 100, 1) if entry["Total"] else 0.0, "Completion %": round((entry["Pass"] / entry["Total"]) * 100, 1) if entry["Total"] else 0.0})
    if course_rows:
        course_frame = pd.DataFrame(course_rows)
        course_fig = go.Figure()
        course_fig.add_trace(go.Scatterpolar(r=[row["Average Grade"] for row in course_rows], theta=["Average Grade", "Pass %", "Completion %"], fill="toself", name="Course"))
        _render_plot(course_fig, "Course comparison")

    submission_rows = []
    for assignment in filtered:
        if assignment.submission and assignment.submission.submitted_at:
            submission_rows.append(assignment.submission)
    if submission_rows:
        heat_frame = pd.DataFrame({"Date": [item.submitted_at.date() for item in submission_rows], "Weekday": [item.submitted_at.strftime("%a") for item in submission_rows], "Month": [item.submitted_at.strftime("%b") for item in submission_rows]})
        heat_frame = heat_frame.groupby(["Month", "Weekday"]).size().reset_index(name="Count")
        heat_fig = px.density_heatmap(heat_frame, x="Month", y="Weekday", z="Count", color_continuous_scale=[colors["bg"], colors["primary"]])
        _render_plot(heat_fig, "Submission heat map")

    submission_counter = Counter()
    for assignment in filtered:
        if assignment.submission:
            submission_counter["Repository Submitted"] += 1
        else:
            submission_counter["Repository Missing"] += 1
        if assignment.submission and assignment.submission.is_late:
            submission_counter["Late Upload"] += 1
    github_frame = pd.DataFrame({"Category": list(submission_counter.keys()), "Count": list(submission_counter.values())})
    github_fig = px.pie(github_frame, values="Count", names="Category", color_discrete_sequence=[colors["primary"], colors["secondary"], colors["warning"], colors["danger"]])
    _render_plot(github_fig, "GitHub submission analytics")

    total_submissions = len([assignment for assignment in filtered if assignment.submission])
    progress_value = round((total_submissions / len(filtered)) * 100, 1) if filtered else 0.0
    gauge_fig = go.Figure(go.Indicator(mode="gauge+number", value=progress_value, gauge={"axis": {"range": [0, 100]}, "bar": {"color": colors["primary"]}, "steps": [{"range": [0, 50], "color": colors["warning"]}, {"range": [50, 100], "color": colors["success"]}] }))
    _render_plot(gauge_fig, "Evaluation progress")

    user_counts = Counter(user.role.name for user in db.scalars(select(User)).all())
    user_frame = pd.DataFrame({"Role": list(user_counts.keys()), "Count": list(user_counts.values())})
    user_fig = px.pie(user_frame, values="Count", names="Role", color_discrete_sequence=[colors["primary"], colors["secondary"], colors["accent"], colors["warning"], colors["danger"]])
    _render_plot(user_fig, "User statistics")

    practical_rows = []
    practical_groups = {}
    for assignment in filtered:
        practical_obj = assignment.practical
        if not practical_obj:
            continue
        label = f"P{practical_obj.practical_number} · {practical_obj.title}"
        entry = practical_groups.setdefault(label, {"Attempt Count": 0, "Average Grade": [], "Submission Count": 0})
        entry["Attempt Count"] += 1
        if assignment.submission and assignment.submission.evaluation:
            entry["Average Grade"].append(_grade_score(assignment.submission.evaluation.grade))
            entry["Submission Count"] += 1
    for label, entry in practical_groups.items():
        grades = entry["Average Grade"]
        practical_rows.append({"Practical Name": label, "Attempt Count": entry["Attempt Count"], "Average Grade": round(sum(grades) / len(grades), 1) if grades else 0.0, "Submission Percentage": round((entry["Submission Count"] / entry["Attempt Count"]) * 100, 1) if entry["Attempt Count"] else 0.0})
    if practical_rows:
        practical_frame = pd.DataFrame(practical_rows).sort_values("Submission Percentage", ascending=False)
        practical_fig = px.bar(practical_frame, x="Submission Percentage", y="Practical Name", orientation="h", color="Average Grade", color_continuous_scale=[colors["bg"], colors["primary"]])
        _render_plot(practical_fig, "Practical-wise analytics")

    st.subheader("Drill-down insights")
    drill_cols = st.columns([3, 1])
    with drill_cols[0]:
        st.caption("Inspect the active filter set with a detailed record view and export the current dataset for reporting.")
    with drill_cols[1]:
        csv_bytes = filtered_frame.to_csv(index=False).encode("utf-8")
        st.download_button("Export CSV", csv_bytes, file_name="dashboard_export.csv", mime="text/csv")

    with st.expander("Current filtered records", expanded=True):
        st.dataframe(filtered_frame, hide_index=True, width="stretch")

    summary_cols = st.columns(4)
    with summary_cols[0]:
        st.metric("Records", len(filtered_frame))
    with summary_cols[1]:
        st.metric("Submitted", int((filtered_frame["Status"] == "Submitted").sum()))
    with summary_cols[2]:
        st.metric("Evaluated", int((filtered_frame["Evaluation Published"] == True).sum()))
    with summary_cols[3]:
        st.metric("Pending", int((filtered_frame["Status"] != "Evaluated").sum()))


def student_dashboard(db, student: Student, *args, **kwargs) -> None:
    assignments = db.scalars(
        select(Assignment)
        .where(Assignment.student_id == student.id)
        .order_by(Assignment.deadline)
    ).all()
    submitted = [assignment for assignment in assignments if assignment.submission]
    pending = [assignment for assignment in assignments if not assignment.submission]
    graded = [assignment for assignment in submitted if assignment.submission.evaluation]

    st.title(f"Welcome, {student.user.full_name.split()[0]}")
    st.caption(f"{student.enrollment_no} · {student.program} · Semester {student.semester}")
    columns = st.columns(4)
    for column, label, value in zip(
        columns,
        ["Assigned practicals", "Submitted", "Pending", "Graded"],
        [len(assignments), len(submitted), len(pending), len(graded)],
    ):
        with column:
            card(label, value)

    def practical_rows(items: list[Assignment], include_grade: bool = False) -> pd.DataFrame:
        rows = []
        for assignment in items:
            evaluation = assignment.submission.evaluation if assignment.submission else None
            rows.append(
                {
                    "Practical": f"P{assignment.practical.practical_number}",
                    "Title": assignment.practical.title,
                    "Subject": assignment.practical.subject.code,
                    "Submission date": assignment.deadline.strftime("%d %b %Y"),
                    "Status": assignment.status,
                    **({"Grade": evaluation.grade} if include_grade and evaluation else {}),
                }
            )
        return pd.DataFrame(rows)

    st.subheader("Assigned practicals")
    if assignments:
        st.dataframe(practical_rows(assignments), hide_index=True, width="stretch")
    else:
        st.info("No practicals have been assigned yet.")

    left, right = st.columns(2)
    with left:
        st.subheader("Submitted practicals")
        if submitted:
            st.dataframe(practical_rows(submitted), hide_index=True, width="stretch")
        else:
            st.info("No practicals submitted yet.")
    with right:
        st.subheader("Pending practicals")
        if pending:
            st.dataframe(practical_rows(pending), hide_index=True, width="stretch")
        else:
            st.success("All assigned practicals have been submitted.")

    st.subheader("Grades")
    if graded:
        st.dataframe(practical_rows(graded, include_grade=True), hide_index=True, width="stretch")
        for assignment in graded:
            evaluation = assignment.submission.evaluation
            with st.expander(f"P{assignment.practical.practical_number} · {assignment.practical.title} · Grade {evaluation.grade}"):
                st.write(evaluation.remarks or "No remarks provided.")
                if evaluation.suggestions:
                    st.caption(f"Suggestions: {evaluation.suggestions}")
    else:
        st.info("Grades will appear here after faculty evaluation.")