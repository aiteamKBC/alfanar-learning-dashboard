"""Idempotent upsert of a normalized Dataset into the database.

Rows are matched on natural (source) keys: unchanged rows are untouched,
changed rows are updated in place (primary keys stay stable), new rows are
inserted and rows no longer present in the source are removed. Must be called
inside a transaction.
"""

from __future__ import annotations

from ..models import (
    AcademicEvidence, Activity, Course, Enrollment, Learner, LearnerIdentity, LessonRecord, QuizAttempt, QuizResult, Submission,
)
from .normalize import Dataset


def _upsert(model, key_fields: tuple[str, ...], desired: dict[tuple, dict]) -> tuple[dict, dict]:
    existing = {tuple(getattr(o, f) for f in key_fields): o for o in model.objects.all()}
    to_create, to_update, update_fields = [], [], set()
    for key, values in desired.items():
        obj = existing.get(key)
        if obj is None:
            to_create.append(model(**dict(zip(key_fields, key)), **values))
            continue
        changed = [f for f, v in values.items() if getattr(obj, f) != v]
        if changed:
            for f in changed:
                setattr(obj, f, values[f])
            update_fields.update(changed)
            to_update.append(obj)
    stale_ids = [o.pk for k, o in existing.items() if k not in desired]
    for i in range(0, len(stale_ids), 500):
        model.objects.filter(pk__in=stale_ids[i:i + 500]).delete()
    if to_create:
        model.objects.bulk_create(to_create, batch_size=500)
    if to_update:
        model.objects.bulk_update(to_update, sorted(update_fields), batch_size=500)
    pk_map = {tuple(row[:-1]): row[-1] for row in model.objects.values_list(*key_fields, "pk")}
    stats = {"created": len(to_create), "updated": len(to_update), "deleted": len(stale_ids)}
    return pk_map, stats


def load_dataset(ds: Dataset) -> dict:
    stats = {}
    _, stats["identities"] = _upsert(LearnerIdentity, ("source_id",), {(k,):v for k,v in ds.identities.items()})
    _, stats["academic_evidence"] = _upsert(AcademicEvidence, ("sheet", "row_number"), ds.evidence)
    course_pk, stats["courses"] = _upsert(Course, ("source_id",), {(k,): v for k, v in ds.courses.items()})
    learner_pk, stats["learners"] = _upsert(Learner, ("source_id",), {(k,): v for k, v in ds.learners.items()})

    enrol_pk, stats["enrollments"] = _upsert(
        Enrollment, ("learner_id", "course_id"),
        {(learner_pk[(u,)], course_pk[(c,)]): v for (u, c), v in ds.enrollments.items()},
    )

    def e(uid, cid):
        return enrol_pk[(learner_pk[(uid,)], course_pk[(cid,)])]

    act_pk, stats["activities"] = _upsert(
        Activity, ("course_id", "kind", "source_id"),
        {(course_pk[(c,)], kind, sid): v for (c, kind, sid), v in ds.activities.items()},
    )

    def a(cid, kind, sid):
        return act_pk[(course_pk[(cid,)], kind, sid)]

    _, stats["lesson_records"] = _upsert(
        LessonRecord, ("enrollment_id", "activity_id"),
        {(e(u, c), a(c, kind, sid)): v for (u, c, kind, sid), v in ds.lesson_records.items()},
    )
    _, stats["quiz_results"] = _upsert(
        QuizResult, ("enrollment_id", "activity_id"),
        {(e(u, c), a(c, "quiz", q)): v for (u, c, q), v in ds.quiz_results.items()},
    )
    _, stats["quiz_attempts"] = _upsert(
        QuizAttempt, ("enrollment_id", "activity_id", "attempt_number"),
        {(e(u, c), a(c, "quiz", q), n): v for (u, c, q, n), v in ds.quiz_attempts.items()},
    )
    _, stats["submissions"] = _upsert(
        Submission, ("source_id",),
        {
            (sid,): {
                "enrollment_id": e(s["uid"], s["cid"]),
                "activity_id": a(s["cid"], "assignment", s["aid"]),
                "submission_number": s["submission_number"],
                "status": s["status"],
                "grade": s["grade"],
                "updated_at": s["updated_at"],
            }
            for sid, s in ds.submissions.items()
        },
    )
    return stats
