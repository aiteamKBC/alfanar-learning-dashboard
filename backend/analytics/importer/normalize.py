"""Convert raw worksheet tables into a validated, de-duplicated dataset.

Design rules (see docs/data-mapping.md):
* Students sheet is the enrollment master: one row per (User ID, Course ID).
* Lesson Details is the master for lesson completions (it is the union of the
  Videos / PDFs / Audio / Readings sheets, which are only used for cross-checks).
* Quizzes = one row per learner/quiz; Quiz Attempts = one row per attempt.
* Assignments + Submissions supply assignment activities and submissions.
* "All Activities" is the union of quizzes + lessons + submissions and is only
  used for validation, never loaded, to avoid double counting.
* Blank cells become None ("not available"), never 0.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..models import ActivityKind
from .exclusions import NON_LEARNER_SOURCE_IDS


class ValidationFailed(Exception):
    def __init__(self, errors: list[str], warnings: list[str] | None = None):
        super().__init__("; ".join(errors[:5]))
        self.errors = errors
        self.warnings = warnings or []


# Canonical sheet keys -> required headers
REQUIRED_SHEETS = {
    "students": ["User ID", "Name", "Course", "Course ID", "Progress (%)"],
    "quizzes": ["User ID", "Course ID", "Quiz ID", "Quiz Title", "Attempts Count", "Best Progress (%)"],
    "quiz attempts": ["User ID", "Course ID", "Quiz ID", "Attempt #", "Progress (%)", "Status"],
    "lesson details": ["User ID", "Course ID", "Category", "Lesson ID", "Lesson Title"],
    "submissions": ["User ID", "Course ID", "Assignment ID", "Submission ID", "Status"],
}
OPTIONAL_SHEETS = [
    "assignments", "all activities", "videos", "pdfs", "audio", "readings",
    "reading sessions", "other lessons", "coverage", "diagnostics",
]

LESSON_CATEGORY = {
    "video": ActivityKind.VIDEO,
    "pdf": ActivityKind.PDF,
    "audio": ActivityKind.AUDIO,
    "reading": ActivityKind.READING,
    "text": ActivityKind.READING,
}

# Fraction of fact rows allowed to fail referential checks before the import is rejected.
MAX_ORPHAN_RATIO = 0.05


def canonical_sheet_name(title: str) -> str:
    t = re.sub(r"\s+", " ", str(title or "")).strip().lower()
    t = re.sub(r"^al[\s-]?fanar\s*", "", t)
    return t


def clean_text(v) -> str:
    if v is None:
        return ""
    s = html.unescape(str(v))
    return re.sub(r"\s+", " ", s).strip()


def is_blank(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def to_float(v):
    if is_blank(v):
        return None
    if isinstance(v, bool):
        raise ValueError("boolean is not numeric")
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").rstrip("%")
    return float(s)


def to_int(v):
    f = to_float(v)
    if f is None:
        return None
    if abs(f - round(f)) > 1e-9:
        raise ValueError(f"expected integer, got {v!r}")
    return int(round(f))


_DT_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S")


def to_datetime(v):
    """Datetime cells, ISO-like strings, or Unix epoch seconds -> aware UTC datetime.

    The export does not state a timezone; values are interpreted as UTC.
    """
    if is_blank(v):
        return None
    if isinstance(v, dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day, tzinfo=dt.timezone.utc)
    if isinstance(v, (int, float)) or re.fullmatch(r"\d{9,11}(\.0+)?", str(v).strip()):
        ts = float(v)
        if ts <= 0:
            return None
        return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc)
    s = str(v).strip()
    for fmt in _DT_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {s!r}")


@dataclass
class Dataset:
    courses: dict = field(default_factory=dict)        # cid -> fields
    learners: dict = field(default_factory=dict)       # uid -> fields
    enrollments: dict = field(default_factory=dict)    # (uid, cid) -> fields
    activities: dict = field(default_factory=dict)     # (cid, kind, sid) -> fields
    lesson_records: dict = field(default_factory=dict) # (uid, cid, kind, sid) -> fields
    quiz_results: dict = field(default_factory=dict)   # (uid, cid, qid) -> fields
    quiz_attempts: dict = field(default_factory=dict)  # (uid, cid, qid, n) -> fields
    submissions: dict = field(default_factory=dict)    # sub_id -> fields (incl. uid, cid, aid)
    sheets_seen: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    latest_activity_at: dt.datetime | None = None
    possible_duplicate_groups: list = field(default_factory=list)
    excluded_non_learners: dict = field(default_factory=dict)  # aggregate counts of skipped non-learner rows

    def counts(self) -> dict:
        return {
            "courses": len(self.courses),
            "learners": len(self.learners),
            "enrollments": len(self.enrollments),
            "activities": len(self.activities),
            "lesson_records": len(self.lesson_records),
            "quiz_results": len(self.quiz_results),
            "quiz_attempts": len(self.quiz_attempts),
            "submissions": len(self.submissions),
        }

    def fingerprint(self) -> str:
        def enc(o):
            if isinstance(o, dt.datetime):
                return o.isoformat()
            raise TypeError(type(o))

        blob = {
            name: sorted(([list(k) if isinstance(k, tuple) else k, v] for k, v in getattr(self, name).items()),
                         key=lambda kv: json.dumps(kv[0]))
            for name in ("courses", "learners", "enrollments", "activities", "lesson_records",
                         "quiz_results", "quiz_attempts", "submissions")
        }
        return hashlib.sha256(json.dumps(blob, default=enc, sort_keys=True).encode()).hexdigest()


class _Sheet:
    """Header-indexed access to a raw table, skipping fully blank rows."""

    def __init__(self, title: str, rows: list[list]):
        self.title = title
        rows = [r for r in rows if r and any(not is_blank(c) for c in r)]
        self.header = [clean_text(h) for h in rows[0]] if rows else []
        self.index = {h: i for i, h in enumerate(self.header) if h}
        self.rows = rows[1:] if rows else []

    def missing(self, required):
        return [h for h in required if h not in self.index]

    def records(self):
        for n, r in enumerate(self.rows, start=2):
            yield n, {h: (r[i] if i < len(r) else None) for h, i in self.index.items()}


def normalize(tables: dict[str, list[list]]) -> Dataset:
    ds = Dataset()
    sheets: dict[str, _Sheet] = {}
    for title, rows in tables.items():
        key = canonical_sheet_name(title)
        sheets[key] = _Sheet(title, rows)
        ds.sheets_seen.append({"title": title, "key": key, "rows": len(sheets[key].rows)})

    for key, required in REQUIRED_SHEETS.items():
        sh = sheets.get(key)
        if sh is None:
            ds.errors.append(f"Required worksheet missing: '{key}'")
        elif sh.missing(required):
            ds.errors.append(f"Worksheet '{sh.title}' missing headers: {', '.join(sh.missing(required))}")
    if ds.errors:
        raise ValidationFailed(ds.errors)

    row_errors: Counter = Counter()

    def bad(sheet: str, rownum: int, msg: str):
        row_errors[sheet] += 1
        if row_errors[sheet] <= 20:
            ds.errors.append(f"{sheet} row {rownum}: {msg}")

    def bump_latest(ts):
        if ts and (ds.latest_activity_at is None or ts > ds.latest_activity_at):
            ds.latest_activity_at = ts

    # ---- Students -> learners, courses, enrollments ---------------------------------
    dup_enrol = 0
    excluded: Counter = Counter()  # rows of configured non-learner accounts, per sheet / lesson kind
    excluded_ids: set = set()
    sh = sheets["students"]
    for n, r in sh.records():
        try:
            uid, cid = to_int(r.get("User ID")), to_int(r.get("Course ID"))
            if uid is None or cid is None:
                raise ValueError("missing User ID or Course ID")
            if uid in NON_LEARNER_SOURCE_IDS:
                excluded["students"] += 1
                excluded_ids.add(uid)
                continue
            name = clean_text(r.get("Name"))
            learner = {"name": name, "email": clean_text(r.get("Email"))}
            if uid in ds.learners and ds.learners[uid] != learner:
                ds.warnings.append(f"Learner {uid}: identity differs between rows; first value kept")
            ds.learners.setdefault(uid, learner)

            course = {
                "title": clean_text(r.get("Course")),
                "category": clean_text(r.get("Course Category")),
                "tutor_source_id": to_int(r.get("Tutor ID")),
                "tutor_name": clean_text(r.get("Tutor Name")),
            }
            if cid in ds.courses and ds.courses[cid]["title"] != course["title"]:
                ds.warnings.append(f"Course {cid}: title differs between rows; first value kept")
            ds.courses.setdefault(cid, course)

            enrol = {
                "progress_pct": to_float(r.get("Progress (%)")),
                "reported_quizzes": to_int(r.get("Quizzes Completed")),
                "reported_videos": to_int(r.get("Videos Completed")),
                "reported_text_lessons": to_int(r.get("Text Lessons Completed")),
                "reported_pdfs": to_int(r.get("PDFs Completed")),
                "reported_materials": to_int(r.get("Materials Completed")),
                "reported_submissions": to_int(r.get("Submissions Count")),
                "reported_total_seconds": to_int(r.get("Total Time Seconds")),
                "evidence_count": to_int(r.get("Evidence Count")),
                "last_login": to_datetime(r.get("Last Login")),
                "registered_at": to_datetime(r.get("Registered At")),
            }
            if (uid, cid) in ds.enrollments:
                dup_enrol += 1
                continue
            ds.enrollments[(uid, cid)] = enrol
        except (ValueError, TypeError) as exc:
            bad(sh.title, n, str(exc))
    if dup_enrol:
        ds.warnings.append(f"Students: {dup_enrol} duplicate learner/course rows ignored")
    if not ds.enrollments:
        ds.errors.append("No enrollments found in Students worksheet")
        raise ValidationFailed(ds.errors, ds.warnings)

    orphans: Counter = Counter()
    fact_rows: Counter = Counter()

    def enrolled(sheet_title, uid, cid, kind=None):
        if uid in NON_LEARNER_SOURCE_IDS:  # configured non-learner: skipped, not an orphan
            excluded[sheet_title] += 1
            if kind is not None:
                excluded[f"lesson:{kind}"] += 1
            excluded_ids.add(uid)
            return False
        fact_rows[sheet_title] += 1
        if (uid, cid) not in ds.enrollments:
            orphans[sheet_title] += 1
            return False
        return True

    def add_activity(cid, kind, sid, title, questions=None):
        key = (cid, str(kind), sid)
        cur = ds.activities.get(key)
        if cur is None:
            ds.activities[key] = {"title": title or f"{kind} {sid}", "questions_count": questions}
        elif questions is not None and cur["questions_count"] is None:
            cur["questions_count"] = questions
        return key

    # ---- Lesson Details -> lesson activities + lesson records -------------------------
    sh = sheets["lesson details"]
    dup = 0
    for n, r in sh.records():
        try:
            uid, cid, sid = to_int(r.get("User ID")), to_int(r.get("Course ID")), to_int(r.get("Lesson ID"))
            if None in (uid, cid, sid):
                raise ValueError("missing User ID, Course ID or Lesson ID")
            cat = clean_text(r.get("Category")).lower() or clean_text(r.get("Raw LMS Type")).lower()
            kind = LESSON_CATEGORY.get(cat)
            if kind is None:
                raise ValueError(f"unknown lesson category {cat!r}")
            if not enrolled(sh.title, uid, cid, kind):
                continue
            add_activity(cid, kind, sid, clean_text(r.get("Lesson Title")))
            time_source = clean_text(r.get("Time Source"))
            seconds = to_int(r.get("Session Seconds"))
            if seconds is None and time_source == "start_end_difference":
                seconds = to_int(r.get("Recorded Seconds"))
            if time_source and time_source != "start_end_difference":
                seconds = None  # duration unavailable -> not zero
            started = to_datetime(r.get("Start Time"))
            ended = to_datetime(r.get("End Time"))
            key = (uid, cid, str(kind), sid)
            if key in ds.lesson_records:
                dup += 1
                continue
            ds.lesson_records[key] = {
                "recorded_seconds": seconds,
                "started_at": started,
                "ended_at": ended,
                "time_source": time_source,
            }
            bump_latest(started)
        except (ValueError, TypeError) as exc:
            bad(sh.title, n, str(exc))
    if dup:
        ds.warnings.append(f"Lesson Details: {dup} duplicate learner/lesson rows ignored")

    # ---- Quizzes -> quiz activities + quiz results --------------------------------------
    sh = sheets["quizzes"]
    dup = 0
    for n, r in sh.records():
        try:
            uid, cid, qid = to_int(r.get("User ID")), to_int(r.get("Course ID")), to_int(r.get("Quiz ID"))
            if None in (uid, cid, qid):
                raise ValueError("missing User ID, Course ID or Quiz ID")
            if not enrolled(sh.title, uid, cid):
                continue
            add_activity(cid, ActivityKind.QUIZ, qid, clean_text(r.get("Quiz Title")), to_int(r.get("Questions Count")))
            key = (uid, cid, qid)
            if key in ds.quiz_results:
                dup += 1
                continue
            ds.quiz_results[key] = {
                "attempts_count": to_int(r.get("Attempts Count")),
                "best_score": to_float(r.get("Best Progress (%)")),
            }
        except (ValueError, TypeError) as exc:
            bad(sh.title, n, str(exc))
    if dup:
        ds.warnings.append(f"Quizzes: {dup} duplicate learner/quiz rows ignored")

    # ---- Quiz Attempts ------------------------------------------------------------------
    sh = sheets["quiz attempts"]
    dup = 0
    for n, r in sh.records():
        try:
            uid, cid, qid = to_int(r.get("User ID")), to_int(r.get("Course ID")), to_int(r.get("Quiz ID"))
            num = to_int(r.get("Attempt #"))
            if None in (uid, cid, qid, num):
                raise ValueError("missing User ID, Course ID, Quiz ID or Attempt #")
            if not enrolled(sh.title, uid, cid):
                continue
            add_activity(cid, ActivityKind.QUIZ, qid, clean_text(r.get("Quiz Title")))
            key = (uid, cid, qid, num)
            if key in ds.quiz_attempts:
                dup += 1
                continue
            created = to_datetime(r.get("Created At"))
            ds.quiz_attempts[key] = {
                "score": to_float(r.get("Progress (%)")),
                "status": clean_text(r.get("Status")).lower(),
                "created_at": created,
            }
            bump_latest(created)
        except (ValueError, TypeError) as exc:
            bad(sh.title, n, str(exc))
    if dup:
        ds.warnings.append(f"Quiz Attempts: {dup} duplicate attempt rows ignored")

    # Derive latest-attempt fields. "Latest" = highest source Attempt # (the LMS sequence).
    by_quiz = defaultdict(list)
    for (uid, cid, qid, num), a in ds.quiz_attempts.items():
        by_quiz[(uid, cid, qid)].append((num, a))
    for key, attempts in by_quiz.items():
        attempts.sort(key=lambda t: t[0])
        graded = [(num, a) for num, a in attempts if a["score"] is not None]
        latest_num, latest = graded[-1] if graded else (None, None)
        res = ds.quiz_results.get(key)
        if res is None:
            ds.warnings.append("Quiz attempts exist without a matching Quizzes row; summary derived from attempts")
            res = ds.quiz_results[key] = {
                "attempts_count": len(attempts),
                "best_score": max((a["score"] for _, a in graded), default=None),
            }
        res["latest_attempt_number"] = latest_num
        res["latest_score"] = latest["score"] if latest else None
        res["latest_status"] = latest["status"] if latest else ""
        res["ever_passed"] = any(a["status"] == "passed" for _, a in attempts)
    for key, res in ds.quiz_results.items():
        res.setdefault("latest_attempt_number", None)
        res.setdefault("latest_score", None)
        res.setdefault("latest_status", "")
        res.setdefault("ever_passed", None)
        if res["attempts_count"] is not None and key in by_quiz and res["attempts_count"] != len(by_quiz[key]):
            ds.warnings.append("Quizzes 'Attempts Count' differs from Quiz Attempts rows for some learner/quiz pairs")

    # ---- Assignments (catalog) ----------------------------------------------------------
    if "assignments" in sheets:
        sh = sheets["assignments"]
        for n, r in sh.records():
            try:
                cid, aid = to_int(r.get("Course ID")), to_int(r.get("Assignment ID"))
                if None in (cid, aid):
                    raise ValueError("missing Course ID or Assignment ID")
                if cid in ds.courses:
                    add_activity(cid, ActivityKind.ASSIGNMENT, aid, clean_text(r.get("Assignment Title")))
            except (ValueError, TypeError) as exc:
                bad(sh.title, n, str(exc))

    # ---- Submissions --------------------------------------------------------------------
    sh = sheets["submissions"]
    dup = 0
    for n, r in sh.records():
        try:
            uid, cid = to_int(r.get("User ID")), to_int(r.get("Course ID"))
            aid, sub_id = to_int(r.get("Assignment ID")), to_int(r.get("Submission ID"))
            if None in (uid, cid, aid, sub_id):
                raise ValueError("missing User ID, Course ID, Assignment ID or Submission ID")
            if not enrolled(sh.title, uid, cid):
                continue
            add_activity(cid, ActivityKind.ASSIGNMENT, aid, clean_text(r.get("Assignment Title")))
            if sub_id in ds.submissions:
                dup += 1
                continue
            updated = to_datetime(r.get("Updated At"))
            ds.submissions[sub_id] = {
                "uid": uid, "cid": cid, "aid": aid,
                "submission_number": to_int(r.get("Submission #")),
                "status": clean_text(r.get("Status")).lower(),
                "grade": to_float(r.get("Grade")),
                "updated_at": updated,
            }
        except (ValueError, TypeError) as exc:
            bad(sh.title, n, str(exc))
    if dup:
        ds.warnings.append(f"Submissions: {dup} duplicate submission IDs ignored")

    # ---- Validation ---------------------------------------------------------------------
    ds.excluded_non_learners = {"accounts": len(excluded_ids), "rows": dict(excluded)}
    if excluded_ids:
        ds.warnings.append(f"Excluded {len(excluded_ids)} configured non-learner account(s) "
                           f"({sum(v for k, v in excluded.items() if not k.startswith('lesson:'))} source rows) from learner data")
    for title, cnt in orphans.items():
        ds.warnings.append(f"{title}: {cnt} rows reference a learner/course not in Students and were skipped")
        if fact_rows[title] and cnt / fact_rows[title] > MAX_ORPHAN_RATIO:
            ds.errors.append(f"{title}: {cnt}/{fact_rows[title]} rows have no matching enrollment")
    for title, cnt in row_errors.items():
        total = len(next(s for s in sheets.values() if s.title == title).rows) or 1
        if cnt / total > MAX_ORPHAN_RATIO:
            ds.errors.append(f"{title}: {cnt} unreadable rows (> {MAX_ORPHAN_RATIO:.0%})")

    _cross_check(ds, sheets)
    _possible_duplicate_identities(ds)

    fatal = [e for e in ds.errors if "unreadable rows" in e or "no matching enrollment" in e]
    if fatal:
        raise ValidationFailed(ds.errors, ds.warnings)
    ds.warnings = list(dict.fromkeys(ds.warnings))
    return ds


def _identity_key(value: str) -> str:
    """Normalize a name or username-style email ("first-lastalfanar-com", "first.last@alfanar.com")."""
    v = re.sub(r"[^a-z0-9]", "", value.lower())
    v = re.sub(r"(alfanar)?(com)?$", "", v)
    return v


def _possible_duplicate_identities(ds: Dataset):
    """Flag (never merge) learner IDs whose names/emails normalize to the same identity."""
    groups = defaultdict(set)
    for uid, l in ds.learners.items():
        for raw in (l["name"], l["email"]):
            key = _identity_key(raw or "")
            if len(key) >= 6:
                groups[key].add(uid)
    clusters = {frozenset(v) for v in groups.values() if len(v) > 1}
    ds.possible_duplicate_groups = sorted(sorted(c) for c in clusters)
    if clusters:
        ids = "; ".join(", ".join(map(str, g)) for g in ds.possible_duplicate_groups)
        ds.warnings.append(
            f"Possible duplicate learner accounts (User IDs {ids}) share a normalized name or email. "
            "They are kept separate because the source treats them as distinct IDs; review in the LMS."
        )


def _cross_check(ds: Dataset, sheets: dict[str, _Sheet]):
    """Compare loaded totals with the export's own union / per-type sheets (warnings only)."""
    lesson_counts = Counter(k[2] for k in ds.lesson_records)
    per_type = {"videos": "video", "pdfs": "pdf", "audio": "audio", "readings": "reading"}
    for sheet_key, kind in per_type.items():
        if sheet_key in sheets:
            n = len(sheets[sheet_key].rows)
            n -= ds.excluded_non_learners.get("rows", {}).get(f"lesson:{kind}", 0)
            if n != lesson_counts.get(kind, 0):
                ds.warnings.append(
                    f"Cross-check: '{sheets[sheet_key].title}' has {n} rows but Lesson Details has "
                    f"{lesson_counts.get(kind, 0)} {kind} records"
                )
    if "all activities" in sheets:
        n = len(sheets["all activities"].rows)
        expected = len(ds.lesson_records) + len(ds.quiz_results) + len(ds.submissions)
        rows = ds.excluded_non_learners.get("rows", {})
        n -= sum(rows.get(sheets[k].title, 0) for k in ("lesson details", "quizzes", "submissions") if k in sheets)
        if n != expected:
            ds.warnings.append(f"Cross-check: All Activities has {n} rows; lessons+quizzes+submissions = {expected}")

    # LMS summary 'Submissions Count' vs actual submission rows
    subs = Counter((s["uid"], s["cid"]) for s in ds.submissions.values())
    mism = sum(1 for k, e in ds.enrollments.items()
               if e["reported_submissions"] is not None and e["reported_submissions"] != subs.get(k, 0))
    if mism:
        ds.warnings.append(
            f"Students 'Submissions Count' disagrees with Submissions rows for {mism} enrollments; "
            "the Submissions worksheet is used as the source of truth"
        )
