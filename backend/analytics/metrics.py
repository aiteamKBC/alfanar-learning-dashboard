"""Metric calculations. Every number is derived from imported records.

Denominator conventions (also exposed to the UI as `definitions`):
* Enrollment = one learner in one module (Students worksheet row).
* "Known" activities of a module = distinct quizzes / lessons / assignments that
  appear anywhere in the export for that module. The export only contains items
  with at least one recorded learner interaction, so this is a lower bound of
  the true curriculum, not an official catalogue.
* Latest quiz score = score of the highest source "Attempt #" for a learner/quiz.
* Pass = source attempt Status == "passed" (pass marks vary per quiz in the LMS).
* A missing record means "no recorded activity", never a failure or a zero score.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean, median

from django.db.models import Count, Max, Q, Sum

from .attendance import confirmed
from .attendance import metrics as att

from .models import (
    LESSON_KINDS, Activity, ActivityKind, Course, Enrollment, LessonRecord, QuizAttempt,
    QuizResult, Submission, SyncRun,
)

KIND_LABELS = dict(ActivityKind.choices)
LESSON_KIND_VALUES = [str(k) for k in LESSON_KINDS]
SUBMITTED_STATUSES = {"passed", "pending", "failed", "graded", "submitted", "completed"}

DEFINITIONS = {
    "unique_learners": "Distinct learner IDs enrolled in the selected modules. A learner in several modules is counted once.",
    "modules": "Distinct modules (LMS courses) in the selection.",
    "enrollments": "Learner-module pairs from the Students worksheet.",
    "active_learners": "Active in LMS: learners with at least one recorded lesson, quiz attempt or assignment record (including drafts) in the selected modules. This is recorded LMS activity, not a measure of overall engagement.",
    "mean_progress": "Mean of the LMS-reported course progress (%) across enrollments. This is the LMS figure, not a qualification outcome.",
    "quiz_completion": "Learner-quiz pairs with at least one attempt ÷ (enrollments × quizzes known for that module). Known quizzes are those appearing in the export.",
    "mean_latest_score": "Mean of each learner's latest graded attempt (highest Attempt #) per quiz. Quizzes never attempted are excluded, not counted as zero.",
    "pass_rate": "Share of graded learner-quiz results whose latest graded attempt has source status 'passed'. Ungraded results are excluded. Pass marks are set per quiz by the LMS.",
    "submissions": "Assignment submission records. 'Submitted' excludes drafts.",
    "assignment_coverage": "Learner-assignment pairs with a non-draft submission ÷ (enrollments × assignments known for that module).",
    "materials_completed": "Recorded completions of videos, PDFs, audio and reading lessons (Lesson Details worksheet).",
    "lesson_coverage": "Recorded lesson completions ÷ (enrollments × lessons known for that module).",
    "activity_coverage": "Enrollments with any recorded activity ÷ all enrollments.",
    "recorded_time": "Unverified LMS timer total from lesson start/end records. Long open sessions may include idle time; this is not verified study hours. Missing durations are excluded.",
    "lecture_recordings": "Recorded lecture videos (lecture parts) in this module that at least one learner has completed (LMS).",
}


def defs() -> dict:
    """LMS definitions plus attendance definitions (which depend on configured thresholds)."""
    return {**DEFINITIONS, **att.definitions()}


def _pct(num, den):
    return round(100.0 * num / den, 1) if den else None


def _avg(values):
    vals = [v for v in values if v is not None]
    return round(mean(vals), 1) if vals else None


# ----------------------------------------------------------------------------------------
# Core per-enrollment aggregation (fixed number of grouped queries, no N+1)
# ----------------------------------------------------------------------------------------

def catalogue(course_ids=None) -> dict[int, dict[str, int]]:
    qs = Activity.objects.all()
    if course_ids is not None:
        qs = qs.filter(course_id__in=course_ids)
    out: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in qs.values("course_id", "kind").annotate(n=Count("id")):
        out[row["course_id"]][row["kind"]] = row["n"]
    return out


def enrollment_stats(enrollment_ids) -> dict[int, dict]:
    ids = list(enrollment_ids)
    stats = {
        eid: {
            "lessons": defaultdict(int), "lesson_seconds": 0, "lesson_timed": 0, "last_lesson": None,
            "quizzes_attempted": 0, "quiz_scores": [], "quizzes_passed_latest": 0, "quizzes_graded": 0,
            "attempts": 0, "last_attempt": None,
            "submissions": 0, "submitted": 0, "submission_status": defaultdict(int),
            "assignments_submitted": 0, "grades": [], "last_submission": None,
        }
        for eid in ids
    }
    if not ids:
        return stats

    for r in (LessonRecord.objects.filter(enrollment_id__in=ids)
              .values("enrollment_id", "activity__kind")
              .annotate(n=Count("id"), secs=Sum("recorded_seconds"), timed=Count("recorded_seconds"),
                        last=Max("started_at"))):
        s = stats[r["enrollment_id"]]
        s["lessons"][r["activity__kind"]] += r["n"]
        s["lesson_seconds"] += r["secs"] or 0
        s["lesson_timed"] += r["timed"]
        if r["last"] and (s["last_lesson"] is None or r["last"] > s["last_lesson"]):
            s["last_lesson"] = r["last"]

    for r in QuizResult.objects.filter(enrollment_id__in=ids).values(
            "enrollment_id", "latest_score", "latest_status"):
        s = stats[r["enrollment_id"]]
        s["quizzes_attempted"] += 1
        if r["latest_score"] is not None:
            s["quizzes_graded"] += 1
            s["quiz_scores"].append(r["latest_score"])
            if r["latest_status"] == "passed":
                s["quizzes_passed_latest"] += 1

    for r in (QuizAttempt.objects.filter(enrollment_id__in=ids).values("enrollment_id")
              .annotate(n=Count("id"), last=Max("created_at"))):
        stats[r["enrollment_id"]]["attempts"] = r["n"]
        stats[r["enrollment_id"]]["last_attempt"] = r["last"]

    submitted_pairs = defaultdict(set)
    for r in Submission.objects.filter(enrollment_id__in=ids).values(
            "enrollment_id", "activity_id", "status", "grade", "updated_at"):
        s = stats[r["enrollment_id"]]
        if r['updated_at'] and (s['last_submission'] is None or r['updated_at'] > s['last_submission']):
            s['last_submission'] = r['updated_at']
        s["submissions"] += 1
        s["submission_status"][r["status"] or "unknown"] += 1
        if r["status"] != "draft":
            s["submitted"] += 1
            submitted_pairs[r["enrollment_id"]].add(r["activity_id"])
        if r["grade"] is not None:
            s["grades"].append(r["grade"])
    for eid, acts in submitted_pairs.items():
        stats[eid]["assignments_submitted"] = len(acts)

    for s in stats.values():
        s["lesson_total"] = sum(s["lessons"].values())
        s["has_activity"] = bool(s["lesson_total"] or s["attempts"] or s["submissions"])
        last = [d for d in (s["last_lesson"], s["last_attempt"], s["last_submission"]) if d]
        s["last_activity"] = max(last) if last else None
    return stats


def filtered_enrollments(course_id=None, engagement=None):
    qs = Enrollment.objects.select_related("course", "learner")
    if course_id:
        qs = qs.filter(course__source_id=course_id)
    enrollments = list(qs)
    stats = enrollment_stats(e.id for e in enrollments)
    active_ids = {e.learner_id for e in enrollments if stats[e.id]["has_activity"]}
    if engagement == "active":
        enrollments = [e for e in enrollments if e.learner_id in active_ids]
    elif engagement == "inactive":
        enrollments = [e for e in enrollments if e.learner_id not in active_ids]
    return enrollments, stats


# ----------------------------------------------------------------------------------------
# Executive overview
# ----------------------------------------------------------------------------------------

def _known(cat, course_id, kinds):
    return sum(cat[course_id].get(str(k), 0) for k in kinds)


def summary(course_id=None, engagement=None) -> dict:
    enrollments, stats = filtered_enrollments(course_id, engagement)
    cat = catalogue()
    learners = {e.learner_id for e in enrollments}
    active_learners = {e.learner_id for e in enrollments if stats[e.id]["has_activity"]}
    progress = [e.progress_pct for e in enrollments if e.progress_pct is not None]

    quiz_den = sum(_known(cat, e.course_id, [ActivityKind.QUIZ]) for e in enrollments)
    lesson_den = sum(_known(cat, e.course_id, LESSON_KINDS) for e in enrollments)
    assign_den = sum(_known(cat, e.course_id, [ActivityKind.ASSIGNMENT]) for e in enrollments)

    agg = lambda key: sum(stats[e.id][key] for e in enrollments)  # noqa: E731
    scores = [v for e in enrollments for v in stats[e.id]["quiz_scores"]]
    graded = agg("quizzes_graded")
    sub_status = defaultdict(int)
    for e in enrollments:
        for k, v in stats[e.id]["submission_status"].items():
            sub_status[k] += v

    return {
        "filters": {"course": course_id, "engagement": engagement},
        "kpis": {
            "unique_learners": {"value": len(learners)},
            "modules": {"value": len({e.course_id for e in enrollments})},
            "enrollments": {"value": len(enrollments)},
            "active_learners": {"value": len(active_learners), "denominator": len(learners),
                                "pct": _pct(len(active_learners), len(learners))},
            "mean_progress": {"value": _avg(progress), "median": round(median(progress), 1) if progress else None,
                              "n": len(progress), "unit": "%"},
            "quiz_completion": {"value": agg("quizzes_attempted"), "denominator": quiz_den,
                                "pct": _pct(agg("quizzes_attempted"), quiz_den)},
            "mean_latest_score": {"value": _avg(scores), "n": len(scores), "unit": "%"},
            "pass_rate": {"value": agg("quizzes_passed_latest"), "denominator": graded,
                          "pct": _pct(agg("quizzes_passed_latest"), graded)},
            "submissions": {"value": agg("submitted"), "total_records": agg("submissions"),
                            "by_status": dict(sub_status)},
            "assignment_coverage": {"value": agg("assignments_submitted"), "denominator": assign_den,
                                    "pct": _pct(agg("assignments_submitted"), assign_den)},
            "materials_completed": {"value": agg("lesson_total"), "denominator": lesson_den,
                                    "pct": _pct(agg("lesson_total"), lesson_den)},
            "activity_coverage": {"value": sum(1 for e in enrollments if stats[e.id]["has_activity"]),
                                  "denominator": len(enrollments),
                                  "pct": _pct(sum(1 for e in enrollments if stats[e.id]["has_activity"]),
                                              len(enrollments))},
            "recorded_time": {"value": agg("lesson_seconds"), "timed_records": agg("lesson_timed"),
                              "untimed_records": agg("lesson_total") - agg("lesson_timed"), "unit": "seconds",
                              "verified": False, "warning": "LMS timers may include idle/open sessions; not verified study hours"},
            "attendance": att.aggregate(att.AttendanceIndex(),
                                        [(e.learner.source_id, e.course.source_id) for e in enrollments]),
        },
        "definitions": defs(),
    }


def lecture_recordings() -> dict[int, int]:
    """Per course: distinct recorded lecture videos with at least one learner completion (LMS)."""
    rows = (Activity.objects.filter(kind=ActivityKind.VIDEO, lesson_records__isnull=False)
            .values("course_id").annotate(n=Count("id", distinct=True)))
    return {r["course_id"]: r["n"] for r in rows}


def charts(course_id=None, engagement=None, activity_type=None) -> dict:
    enrollments, stats = filtered_enrollments(course_id, engagement)
    cat = catalogue()
    courses = {c.id: c for c in Course.objects.all()}
    by_course = defaultdict(list)
    for e in enrollments:
        by_course[e.course_id].append(e)
    recordings = lecture_recordings()
    index = att.AttendanceIndex()
    kinds = [activity_type] if activity_type else [k.value for k in ActivityKind]

    module_progress, quiz_by_module, completion = [], [], []
    for cid, ens in sorted(by_course.items(), key=lambda kv: courses[kv[0]].title):
        c = courses[cid]
        prog = [e.progress_pct for e in ens if e.progress_pct is not None]
        module_progress.append({
            "course_id": c.source_id, "course": c.title, "enrollments": len(ens),
            "active": sum(1 for e in ens if stats[e.id]["has_activity"]),
            "mean_progress": _avg(prog), "median_progress": round(median(prog), 1) if prog else None,
            # Module-level facts (independent of learner filters):
            "delivered_sessions": (teams_n := len(index.sessions_by_course[c.source_id]) if index.has_data(c.source_id) else None),
            "delivered_lectures": confirmed.delivered_lectures(c.source_id, teams_n,
                "attendance_matrix" if any(s["meeting__evidence_source"] == "attendance_matrix" for s in index.sessions_by_course[c.source_id]) else "teams"),
            "lecture_recordings": recordings.get(cid, 0),
            "attendance": att.aggregate(index, [(e.learner.source_id, c.source_id) for e in ens]),
        })
        n_quiz = cat[cid].get("quiz", 0)
        attempted = sum(stats[e.id]["quizzes_attempted"] for e in ens)
        graded = sum(stats[e.id]["quizzes_graded"] for e in ens)
        quiz_by_module.append({
            "course_id": c.source_id, "course": c.title, "known_quizzes": n_quiz,
            "attempted_pairs": attempted, "possible_pairs": n_quiz * len(ens),
            "completion_pct": _pct(attempted, n_quiz * len(ens)),
            "mean_latest_score": _avg([v for e in ens for v in stats[e.id]["quiz_scores"]]),
            "pass_rate_pct": _pct(sum(stats[e.id]["quizzes_passed_latest"] for e in ens), graded),
        })
        row = {"course_id": c.source_id, "course": c.title, "by_type": {}}
        for k in kinds:
            known = cat[cid].get(k, 0)
            if k == "quiz":
                done = attempted
            elif k == "assignment":
                done = sum(stats[e.id]["assignments_submitted"] for e in ens)
            else:
                done = sum(stats[e.id]["lessons"].get(k, 0) for e in ens)
            row["by_type"][k] = {"completed": done, "possible": known * len(ens),
                                 "known_items": known, "pct": _pct(done, known * len(ens))}
        completion.append(row)

    by_type = []
    for k in kinds:
        if k == "quiz":
            n = sum(stats[e.id]["quizzes_attempted"] for e in enrollments)
            label = "Quizzes attempted"
        elif k == "assignment":
            n = sum(stats[e.id]["submitted"] for e in enrollments)
            label = "Assignments submitted"
        else:
            n = sum(stats[e.id]["lessons"].get(k, 0) for e in enrollments)
            label = f"{KIND_LABELS[k]} completions"
        by_type.append({"type": k, "label": label, "count": n})

    buckets = [("0%", 0, 0), ("1–24%", 0.0001, 24.9999), ("25–49%", 25, 49.9999),
               ("50–74%", 50, 74.9999), ("75–99%", 75, 99.9999), ("100%", 100, 100)]
    dist = [{"bucket": b, "count": sum(1 for e in enrollments if e.progress_pct is not None and lo <= e.progress_pct <= hi)}
            for b, lo, hi in buckets]
    missing_progress = sum(1 for e in enrollments if e.progress_pct is None)

    # Learner x module progress matrix
    module_cols = [{"course_id": courses[cid].source_id, "course": courses[cid].title}
                   for cid in sorted(by_course, key=lambda c: courses[c].title)]
    rows = defaultdict(dict)
    names = {}
    for e in enrollments:
        rows[e.learner.source_id][e.course.source_id] = e.progress_pct
        names[e.learner.source_id] = e.learner.name
    matrix = [{"learner_id": lid, "name": names[lid], "progress": rows[lid]}
              for lid in sorted(rows, key=lambda l: names[l].lower())]

    return {
        "filters": {"course": course_id, "engagement": engagement, "activity_type": activity_type},
        "module_progress": module_progress,
        "quiz_by_module": quiz_by_module,
        "activity_completion": completion,
        "activity_by_type": by_type,
        "progress_distribution": {"buckets": dist, "missing": missing_progress, "total": len(enrollments)},
        "learner_module_matrix": {"modules": module_cols, "rows": matrix},
        "definitions": defs(),
    }


# ----------------------------------------------------------------------------------------
# Learners
# ----------------------------------------------------------------------------------------

def enrollment_payload(e: Enrollment, s: dict, cat, index: att.AttendanceIndex | None = None) -> dict:
    index = index or att.AttendanceIndex()
    known_q = cat[e.course_id].get("quiz", 0)
    known_l = _known(cat, e.course_id, LESSON_KINDS)
    known_a = cat[e.course_id].get("assignment", 0)
    return {
        "course_id": e.course.source_id,
        "course": e.course.title,
        "progress_pct": e.progress_pct,
        "has_activity": s["has_activity"],
        "lessons_completed": s["lesson_total"],
        "lessons_known": known_l,
        "lessons_by_type": {k: {"completed": s["lessons"].get(k, 0), "known": cat[e.course_id].get(k, 0)}
                            for k in LESSON_KIND_VALUES},
        "quizzes_attempted": s["quizzes_attempted"],
        "quizzes_known": known_q,
        "quiz_attempts": s["attempts"],
        "mean_latest_score": _avg(s["quiz_scores"]),
        "latest_score_sum": sum(s["quiz_scores"]),
        "quizzes_passed_latest": s["quizzes_passed_latest"],
        "quizzes_graded": s["quizzes_graded"],
        "assignments_submitted": s["assignments_submitted"],
        "assignments_known": known_a,
        "submission_status": dict(s["submission_status"]),
        "recorded_seconds": s["lesson_seconds"],
        "last_activity": s["last_activity"],
        "last_login": e.last_login,
        "registered_at": e.registered_at,
        "attendance": index.enrollment(e.learner.source_id, e.course.source_id),
    }


def learner_rollup(enrollment_payloads: list[dict]) -> dict:
    prog = [p["progress_pct"] for p in enrollment_payloads if p["progress_pct"] is not None]
    graded = sum(p["quizzes_graded"] for p in enrollment_payloads)
    # pair-level mean of latest scores across all modules
    total_score = sum(p["latest_score_sum"] for p in enrollment_payloads)
    last = [p["last_activity"] for p in enrollment_payloads if p["last_activity"]]
    covered = [p["attendance"] for p in enrollment_payloads if p["attendance"]["available"]]
    att_attended = sum(a["attended"] for a in covered)
    att_expected = sum(a.get("measured", a["delivered"]) for a in covered)
    return {
        "attendance": {
            "available": bool(covered), "attended": att_attended if covered else None,
            "expected": att_expected if covered else None, "pct": _pct(att_attended, att_expected) if covered else None,
            "live_seconds": sum(a["live_seconds"] or 0 for a in covered) if any(a['live_seconds'] is not None for a in covered) else None,
            "modules_covered": len(covered), "modules_total": len(enrollment_payloads),
            "modules_without_teams_identity": sum(1 for p in enrollment_payloads
                                                  if p["attendance"].get("teams_identity") is False),
        },
        "modules": len(enrollment_payloads),
        "mean_progress": _avg(prog),
        "lessons_completed": sum(p["lessons_completed"] for p in enrollment_payloads),
        "quizzes_attempted": sum(p["quizzes_attempted"] for p in enrollment_payloads),
        "quizzes_known": sum(p["quizzes_known"] for p in enrollment_payloads),
        "quiz_attempts": sum(p["quiz_attempts"] for p in enrollment_payloads),
        "mean_latest_score": round(total_score / graded, 1) if graded else None,
        "quizzes_passed_latest": sum(p["quizzes_passed_latest"] for p in enrollment_payloads),
        "quizzes_graded": graded,
        "assignments_submitted": sum(p["assignments_submitted"] for p in enrollment_payloads),
        "assignments_known": sum(p["assignments_known"] for p in enrollment_payloads),
        "recorded_seconds": sum(p["recorded_seconds"] for p in enrollment_payloads),
        "has_activity": any(p["has_activity"] for p in enrollment_payloads),
        "last_activity": max(last) if last else None,
    }


def learner_directory(search=None, course_id=None, engagement=None, ordering="name") -> list[dict]:
    qs = Enrollment.objects.select_related("course", "learner")
    if search:
        q = Q(learner__name__icontains=search)
        if search.isdigit():
            q |= Q(learner__source_id=int(search))
        qs = qs.filter(q)
    enrollments = list(qs)
    stats = enrollment_stats(e.id for e in enrollments)
    cat = catalogue()
    index = att.AttendanceIndex()

    grouped = defaultdict(list)
    learners = {}
    for e in enrollments:
        grouped[e.learner_id].append(enrollment_payload(e, stats[e.id], cat, index))
        learners[e.learner_id] = e.learner

    rows = []
    for lid, payloads in grouped.items():
        if course_id:
            # When a module is selected, the row's figures describe that module only.
            payloads = [p for p in payloads if p["course_id"] == course_id]
            if not payloads:
                continue
        roll = learner_rollup(payloads)
        if engagement == "active" and not roll["has_activity"]:
            continue
        if engagement == "inactive" and roll["has_activity"]:
            continue
        l = learners[lid]
        rows.append({
            "learner_id": l.source_id, "name": l.name,
            "enrollments": [{"course_id": p["course_id"], "course": p["course"], "progress_pct": p["progress_pct"]}
                            for p in sorted(payloads, key=lambda p: p["course"])],
            **roll,
        })

    desc = ordering.startswith("-")
    key = ordering.lstrip("-")
    sort_keys = {
        "name": lambda r: r["name"].lower(),
        "progress": lambda r: r["mean_progress"],
        "lessons": lambda r: r["lessons_completed"],
        "quiz_score": lambda r: r["mean_latest_score"],
        "quizzes": lambda r: r["quizzes_attempted"],
        "modules": lambda r: r["modules"],
        "attendance": lambda r: r["attendance"]["pct"],
    }
    kf = sort_keys.get(key, sort_keys["name"])
    present = [r for r in rows if kf(r) is not None]
    absent = [r for r in rows if kf(r) is None]  # unknown values always sort last
    present.sort(key=lambda r: (kf(r), r["name"].lower()), reverse=desc)
    return present + sorted(absent, key=lambda r: r["name"].lower())


def learner_detail(learner) -> dict:
    enrollments = list(Enrollment.objects.filter(learner=learner).select_related("course", "learner"))
    stats = enrollment_stats(e.id for e in enrollments)
    cat = catalogue()
    index = att.AttendanceIndex()
    payloads = sorted((enrollment_payload(e, stats[e.id], cat, index) for e in enrollments), key=lambda p: p["course"])
    return {
        "learner": {"learner_id": learner.source_id, "name": learner.name, "email": learner.email},
        "summary": learner_rollup(payloads),
        "courses": payloads,
        "definitions": defs(),
    }


def module_matrix(enrollment: Enrollment) -> dict:
    activities = list(Activity.objects.filter(course_id=enrollment.course_id).order_by("source_id"))
    lessons = {r.activity_id: r for r in LessonRecord.objects.filter(enrollment=enrollment)}
    quizzes = {r.activity_id: r for r in QuizResult.objects.filter(enrollment=enrollment)}
    attempts = defaultdict(list)
    for a in QuizAttempt.objects.filter(enrollment=enrollment).order_by("attempt_number"):
        attempts[a.activity_id].append({"attempt": a.attempt_number, "score": a.score,
                                        "status": a.status, "created_at": a.created_at})
    subs = defaultdict(list)
    for s in Submission.objects.filter(enrollment=enrollment).order_by("submission_number", "source_id"):
        subs[s.activity_id].append(s)

    rows = []
    for a in activities:
        row = {"activity_id": a.source_id, "kind": a.kind, "kind_label": KIND_LABELS[a.kind],
               "title": a.title, "recorded": False}
        if a.kind in LESSON_KIND_VALUES:
            rec = lessons.get(a.id)
            row["lesson"] = None if rec is None else {
                "recorded_seconds": rec.recorded_seconds, "started_at": rec.started_at,
                "ended_at": rec.ended_at, "time_source": rec.time_source}
            row["recorded"] = rec is not None
        elif a.kind == ActivityKind.QUIZ:
            qr = quizzes.get(a.id)
            row["quiz"] = None if qr is None else {
                "questions": a.questions_count, "attempts": qr.attempts_count,
                "latest_attempt": qr.latest_attempt_number, "latest_score": qr.latest_score,
                "latest_status": qr.latest_status, "best_score": qr.best_score,
                "ever_passed": qr.ever_passed, "history": attempts.get(a.id, [])}
            if qr is None:
                row["quiz_questions"] = a.questions_count
            row["recorded"] = qr is not None
        elif a.kind == ActivityKind.ASSIGNMENT:
            ss = subs.get(a.id, [])
            latest = ss[-1] if ss else None
            row["assignment"] = None if latest is None else {
                "status": latest.status, "grade": latest.grade, "updated_at": latest.updated_at,
                "has_submitted": any(s.status != 'draft' for s in ss),
                "history": [{"source_id": s.source_id, "number": s.submission_number, "status": s.status,
                             "grade": s.grade, "updated_at": s.updated_at} for s in ss],
                "submissions": len(ss)}
            row["recorded"] = latest is not None
        rows.append(row)

    def col(kind):
        items = [r for r in rows if r["kind"] == kind]
        return {"known": len(items), "recorded": sum(1 for r in items if r["recorded"])}

    quiz_rows = [r for r in rows if r["kind"] == "quiz" and r.get("quiz")]
    scores = [r["quiz"]["latest_score"] for r in quiz_rows if r["quiz"]["latest_score"] is not None]
    assignment_rows = [r for r in rows if r["kind"] == "assignment"]
    footer = {
        "activities": {"known": len(rows), "recorded": sum(1 for r in rows if r["recorded"])},
        "quiz": {**col("quiz"),
                 "passed_latest": sum(1 for r in quiz_rows if r["quiz"]["latest_status"] == "passed"),
                 "mean_latest_score": _avg(scores), "graded": len(scores),
                 "attempts": sum(len(r["quiz"]["history"]) for r in quiz_rows)},
        "video": col("video"), "audio": col("audio"), "pdf": col("pdf"), "reading": col("reading"),
        "assignment": {**col("assignment"),
                       "submitted": sum(1 for r in assignment_rows
                                        if r.get("assignment") and r["assignment"]["has_submitted"])},
        "recorded_seconds": sum((r["lesson"] or {}).get("recorded_seconds") or 0
                                for r in rows if r.get("lesson")),
    }
    present_kinds = [k for k in ActivityKind.values if any(r["kind"] == k for r in rows)]
    index = att.AttendanceIndex()
    learner_sid, course_sid = enrollment.learner.source_id, enrollment.course.source_id
    summary_att = index.enrollment(learner_sid, course_sid)
    attendance = {**summary_att, "sessions": index.sessions_for(learner_sid, course_sid) if summary_att["available"] else []}
    if not index.has_data(course_sid):
        attendance["reason"] = ("No Microsoft Teams meeting is mapped to this module, so delivered sessions and "
                                "attendance are not available.")
    return {
        "learner": {"learner_id": enrollment.learner.source_id, "name": enrollment.learner.name},
        "course": {"course_id": enrollment.course.source_id, "title": enrollment.course.title,
                   "progress_pct": enrollment.progress_pct},
        "matrix_basis": "activity",
        "matrix_note": ("Rows are the module's learning activities known from the export, in LMS item-ID order. "
                        "The source has no scheduled-session identifiers, so activities are not grouped into sessions."),
        "attendance": attendance,
        "kinds": present_kinds,
        "rows": rows,
        "footer": footer,
        "definitions": defs(),
    }


def sync_status() -> dict:
    last = SyncRun.objects.first()
    last_ok = SyncRun.objects.filter(status=SyncRun.Status.SUCCESS).first()
    from django.conf import settings

    def run_payload(r):
        if r is None:
            return None
        return {"id": r.pk, "source_type": r.source_type, "source_ref": r.source_ref if r.source_type == "excel" else "Google Spreadsheet",
                "status": r.status, "started_at": r.started_at, "finished_at": r.finished_at,
                "source_modified_at": r.source_modified_at, "latest_activity_at": r.latest_activity_at,
                "changed": r.changed, "counts": {k: v for k, v in r.counts.items() if k != "load"},
                "warnings": r.warnings, "errors": r.errors[:10]}

    return {
        "last_run": run_payload(last),
        "last_success": run_payload(last_ok),
        "google_sheets": {
            "enabled": settings.GOOGLE_SHEETS_ENABLED,
            "configured": bool(settings.GOOGLE_SHEETS_SPREADSHEET_ID and settings.GOOGLE_SERVICE_ACCOUNT_FILE),
            "interval_seconds": settings.SYNC_INTERVAL_SECONDS,
            "scope": "https://www.googleapis.com/auth/spreadsheets.readonly",
        },
        "teams_attendance": att.import_status(),
    }
