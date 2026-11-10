"""Only current, unambiguous registry identities may appear in the lecture library."""
from collections import defaultdict
from django.db.models import Prefetch

from ..models import Enrollment, Learner, LearnerIdentity, LectureCapture, LectureResource


def registry():
    learners = {x.pk: x for x in Learner.objects.all()}
    by_source = {x.source_id: x for x in learners.values()}
    emails = defaultdict(set)
    for learner in learners.values():
        if learner.email.strip():
            emails[learner.email.strip().casefold()].add(learner.pk)
    for identity in LearnerIdentity.objects.all():
        learner = by_source.get(identity.canonical_source_id)
        if learner and identity.email.strip():
            emails[identity.email.strip().casefold()].add(learner.pk)
    unique = {email: next(iter(ids)) for email, ids in emails.items() if len(ids) == 1}
    enrolled = defaultdict(set)
    for cid, lid in Enrollment.objects.values_list("course_id", "learner_id"):
        enrolled[cid].add(lid)
    return learners, unique, enrolled


def scoped_observations(lecture, emails, enrolled):
    return {
        row.learner_id: row for row in lecture.observations.all()
        if row.learner_id in enrolled[lecture.course_id]
        and any(emails.get(email.strip().casefold()) == row.learner_id for email in row.source_emails)
    }


def speaking_result(row, emails):
    if not row or row.speaking_seconds is None:
        return None, None
    method = "verified_identity" if row.speaker_verified else row.speaking_match
    evidence_emails = row.speaking_emails or (row.source_emails if row.speaker_verified else [])
    if method not in {"verified_identity", "meeting_name"} or not evidence_emails:
        return None, None
    # A later registry update must not leave stale speech attributed to a learner.
    if not all(emails.get(email.strip().casefold()) == row.learner_id for email in evidence_emails):
        return None, None
    return row.speaking_seconds, method


def lecture_summary(lecture, emails, enrolled):
    observations = scoped_observations(lecture, emails, enrolled)
    resources = list(lecture.resources.all())
    return {
        "id": str(lecture.pk), "course_id": lecture.course.source_id if lecture.course else None,
        "course": lecture.course.display_title if lecture.course else "Alfanar — module unconfirmed", "date": lecture.session_date,
        "starts_at": lecture.starts_at, "ends_at": lecture.ends_at,
        "source": lecture.source_label, "learners": len(enrolled[lecture.course_id]),
        "recording_session": lecture.source_label == "Recording session",
        "present": sum(x.attendance == "present" for x in observations.values()),
        "recorded": sum(x.duration_seconds is not None or x.attendance in {"present", "absent", "review"} for x in observations.values()),
        "recordings": sum(x.kind == "recording" for x in resources),
        "playable_recordings": sum(x.kind == "recording" and x.readable for x in resources),
        "transcripts": sum(x.kind == "transcript" for x in resources),
        "readable_transcripts": sum(x.kind == "transcript" and x.readable for x in resources),
        "verified_speakers": sum(speaking_result(x, emails)[1] == "verified_identity" for x in observations.values()),
        "speaking_covered": sum(speaking_result(x, emails)[0] is not None for x in observations.values()),
        "updated_at": lecture.updated_at,
    }


def lecture_queryset():
    return LectureCapture.objects.filter(active=True).select_related("course").prefetch_related(
        "observations", Prefetch("resources", queryset=LectureResource.objects.defer("text_content", "vtt_content"))
    )


def resolve_lecture(lecture_id):
    """Keep bookmarked fragment URLs usable after a same-day consolidation."""
    original = LectureCapture.objects.filter(pk=lecture_id).values("active", "course_id", "session_date").first()
    if not original:
        return None
    if original["active"]:
        return lecture_queryset().filter(pk=lecture_id).first()
    if original["session_date"] is None:
        return None
    matches = list(lecture_queryset().filter(course_id=original["course_id"], session_date=original["session_date"])[:2])
    return matches[0] if len(matches) == 1 else None


def detail(lecture):
    learners, emails, enrolled = registry()
    observed = scoped_observations(lecture, emails, enrolled)
    rows = []
    for lid in sorted(enrolled[lecture.course_id], key=lambda pk: (learners[pk].name.casefold(), pk)):
        learner, row = learners[lid], observed.get(lid)
        speaking_seconds, speaking_match = speaking_result(row, emails)
        rows.append({
            "learner_id": learner.source_id, "name": learner.name, "email": learner.email,
            "attendance": row.attendance if row else "not_available",
            "first_join": row.first_join if row else None,
            "last_leave": row.last_leave if row else None,
            "duration_seconds": row.duration_seconds if row else None,
            "speaking_seconds": speaking_seconds,
            "speaking_match": speaking_match,
            "speaking_partial": bool(speaking_seconds is not None and row.speaking_partial),
            "camera_on": row.camera_on if row else None,
            "microphone_on": row.microphone_on if row else None,
            "needs_review": bool(row and row.needs_review),
        })
    assets = []
    for asset in sorted(lecture.resources.all(), key=lambda a: (a.kind, a.source_created_at.isoformat() if a.source_created_at else "", a.pk)):
        assets.append({
            "id": asset.pk, "kind": asset.kind, "available": asset.readable,
            "created_at": asset.source_created_at,
            "url": f"/api/lectures/{lecture.pk}/resources/{asset.pk}" if asset.readable else None,
        })
    return {
        **lecture_summary(lecture, emails, enrolled), "participants": rows, "resources": assets,
        "recording_folder_url": lecture.recording_folder_url or None,
        "timezone": "Africa/Cairo",
    }
