"""Only current, unambiguous registry identities may appear in the lecture library."""
from collections import defaultdict
from django.db.models import Prefetch

from django.conf import settings
from rest_framework.exceptions import APIException
from ..models import Course, Enrollment, Learner, LearnerIdentity
from lecture_library.models import LectureCapture, LectureResource
from .recordings import duration_index, stored_duration, select_recordings
from .attendance import attendance_status
from .transcripts import select_transcript, transcript_duration


def require_database():
    if settings.DATABASES['lecture_library']['ENGINE'] == 'django.db.backends.dummy':
        error = APIException('Lecture Library storage is not configured.')
        error.status_code = 503
        raise error


def courses():
    return {course.source_id: course for course in Course.objects.using('default').all()}


def registry():
    learners = {x.source_id: x for x in Learner.objects.using('default').all()}
    by_source = {x.source_id: x for x in learners.values()}
    emails = defaultdict(set)
    for learner in learners.values():
        if learner.email.strip():
            emails[learner.email.strip().casefold()].add(learner.source_id)
    for identity in LearnerIdentity.objects.using('default').all():
        learner = by_source.get(identity.canonical_source_id)
        if learner and identity.email.strip():
            emails[identity.email.strip().casefold()].add(learner.source_id)
    unique = {email: next(iter(ids)) for email, ids in emails.items() if len(ids) == 1}
    enrolled = defaultdict(set)
    for cid, lid in Enrollment.objects.using('default').values_list('course__source_id', 'learner__source_id'):
        enrolled[cid].add(lid)
    return learners, unique, enrolled


def canonical_ids():
    """Original source learner ID -> its current displayed learner, per the active identity projection.

    Owner merges are applied here at read time; stored lecture evidence keeps its original IDs.
    IDs absent from the projection (e.g. archived originals) map to nothing and stay hidden.
    """
    return dict(LearnerIdentity.objects.using('default').values_list('source_id', 'canonical_source_id'))


def _representative_rank(row, learner_id):
    # One stored row represents a learner when an owner merge gives them several: Present
    # (existing rule) before Absent, then the longest stored duration, then the learner's own
    # row, then the lowest original ID. Values are shown as stored; nothing is summed.
    status = attendance_status(row.attendance, row.duration_seconds)
    return (status == "present", status == "absent", row.duration_seconds if row.duration_seconds is not None else -1,
            row.learner_source_id == learner_id, -row.learner_source_id)


def scoped_rows(lecture, emails, enrolled, canonical=None):
    """Current learner ID -> every stored observation that counts for that learner.

    Enrolled learners with a matching identity email only. A row imported under an original ID
    that an owner has since merged into another learner counts for that learner.
    """
    canonical = canonical_ids() if canonical is None else canonical
    rows = defaultdict(list)
    for row in lecture.observations.all():
        learner_id = canonical.get(row.learner_source_id, row.learner_source_id)
        if learner_id not in enrolled[lecture.course_source_id]:
            continue
        if not any(emails.get(email.strip().casefold()) == learner_id for email in row.source_emails):
            continue
        rows[learner_id].append(row)
    return rows


def representative(rows, learner_id):
    return max(rows, key=lambda row: _representative_rank(row, learner_id))


def scoped_observations(lecture, emails, enrolled, canonical=None):
    """Current learner ID -> one stored observation (see `_representative_rank`), never counted twice."""
    return {lid: representative(rows, lid) for lid, rows in scoped_rows(lecture, emails, enrolled, canonical).items()}


def merged_speaking(rows, emails, learner_id):
    """(row, seconds, method) of a learner's strongest valid speaking evidence, or (None, None, None).

    Each row is validated as before (`speaking_result`). Across an owner-merged learner's rows,
    verified identity wins over a meeting-name match, then the longer duration. Speech is never
    summed across rows: the same utterances may be attributed to more than one account.
    """
    best = None
    for row in rows:
        seconds, method = speaking_result(row, emails, learner_id)
        if seconds is not None:
            key = (method == "verified_identity", seconds, -row.learner_source_id)
            if best is None or key > best[0]:
                best = (key, row, seconds, method)
    return best[1:] if best else (None, None, None)


def speaking_result(row, emails, learner_id=None):
    if not row or row.speaking_seconds is None:
        return None, None
    method = "verified_identity" if row.speaker_verified else row.speaking_match
    evidence_emails = row.speaking_emails or (row.source_emails if row.speaker_verified else [])
    if method not in {"verified_identity", "meeting_name"} or not evidence_emails:
        return None, None
    # A later registry update must not leave stale speech attributed to a learner.
    learner_id = row.learner_source_id if learner_id is None else learner_id
    if not all(emails.get(email.strip().casefold()) == learner_id for email in evidence_emails):
        return None, None
    return row.speaking_seconds, method


def visible_resources(lecture):
    resources = list(lecture.resources.all())
    index = duration_index()
    selected = {r.pk for r in select_recordings([r for r in resources if r.kind == 'recording'],
                lambda r: stored_duration(r, index))}
    transcripts = [r for r in resources if r.kind == 'transcript']
    if len(transcripts) > 1:
        transcript_index = duration_index('transcript')
        lengths = {r.pk: stored_duration(r, transcript_index) for r in transcripts}
        # Old installations need no reimport: use their saved VTT timings.
        missing = [r.pk for r in transcripts if lengths[r.pk] is None]
        content = {pk: (vtt, text) for pk, vtt, text in LectureResource.objects.filter(pk__in=missing)
                   .values_list('pk', 'vtt_content', 'text_content')} if missing else {}
        for pk, (vtt, _) in content.items():
            lengths[pk] = transcript_duration(vtt)
        transcripts = select_transcript(transcripts, lambda r: lengths[r.pk], available=lambda r: r.readable,
                                       content_size=lambda r: len(content.get(r.pk, ('', ''))[1]))
    selected.update(r.pk for r in transcripts)
    return [r for r in resources if r.pk in selected]


def recording_counts(queryset):
    grouped = defaultdict(list)
    for asset in LectureResource.objects.filter(lecture__in=queryset, kind='recording').only(
            'lecture_id', 'provider', 'drive_id', 'organizer_id', 'meeting_id', 'source_id', 'readable'):
        grouped[asset.lecture_id].append(asset)
    index = duration_index()
    visible = [r for rows in grouped.values() for r in select_recordings(rows,
               lambda r: stored_duration(r, index))]
    return len(visible), sum(r.readable for r in visible)


def lecture_summary(lecture, emails, enrolled, course_registry, canonical=None):
    course = course_registry.get(lecture.course_source_id)
    grouped = scoped_rows(lecture, emails, enrolled, canonical)
    observations = {lid: representative(rows, lid) for lid, rows in grouped.items()}
    speaking = [merged_speaking(rows, emails, lid) for lid, rows in grouped.items()]
    resources = visible_resources(lecture)
    return {
        "id": str(lecture.pk), "course_id": course.source_id if course else None,
        "course": course.display_title if course else "Alfanar — module unconfirmed", "date": lecture.session_date,
        "starts_at": lecture.starts_at, "ends_at": lecture.ends_at,
        "source": lecture.source_label, "learners": len(enrolled[lecture.course_source_id]),
        "recording_session": lecture.source_label == "Recording session",
        "present": sum(attendance_status(x.attendance, x.duration_seconds) == "present" for x in observations.values()),
        "recorded": sum(x.duration_seconds is not None or x.attendance in {"present", "absent", "review"} for x in observations.values()),
        "recordings": sum(x.kind == "recording" for x in resources),
        "playable_recordings": sum(x.kind == "recording" and x.readable for x in resources),
        "transcripts": sum(x.kind == "transcript" for x in resources),
        "readable_transcripts": sum(x.kind == "transcript" and x.readable for x in resources),
        "verified_speakers": sum(method == "verified_identity" for _, _, method in speaking),
        "speaking_covered": sum(seconds is not None for _, seconds, _ in speaking),
        "updated_at": lecture.updated_at,
    }


def lecture_queryset():
    require_database()
    return LectureCapture.objects.filter(active=True).prefetch_related(
        "observations", Prefetch("resources", queryset=LectureResource.objects.defer("text_content", "vtt_content"))
    )


def catalogue_queryset():
    """Hide cohort lectures without a Present learner; retain all source evidence."""
    queryset = lecture_queryset()
    _, emails, enrolled = registry()
    canonical = canonical_ids()
    hidden = [
        lecture.pk
        for lecture in queryset.prefetch_related(None).prefetch_related("observations")
        if enrolled[lecture.course_source_id]
        and not any(attendance_status(row.attendance, row.duration_seconds) == "present"
                    for row in scoped_observations(lecture, emails, enrolled, canonical).values())
    ]
    return queryset.exclude(pk__in=hidden)


def resolve_lecture(lecture_id):
    """Keep bookmarked fragment URLs usable after a same-day consolidation."""
    require_database()
    original = LectureCapture.objects.filter(pk=lecture_id).values("active", "course_source_id", "session_date").first()
    if not original:
        return None
    if original["active"]:
        return catalogue_queryset().filter(pk=lecture_id).first()
    if original["session_date"] is None:
        return None
    matches = list(catalogue_queryset().filter(course_source_id=original["course_source_id"], session_date=original["session_date"])[:2])
    return matches[0] if len(matches) == 1 else None


def detail(lecture):
    learners, emails, enrolled = registry()
    canonical = canonical_ids()
    grouped = scoped_rows(lecture, emails, enrolled, canonical)
    rows = []
    for lid in sorted(enrolled[lecture.course_source_id], key=lambda pk: (learners[pk].name.casefold(), pk)):
        learner = learners[lid]
        row = representative(grouped[lid], lid) if lid in grouped else None
        speech_row, speaking_seconds, speaking_match = merged_speaking(grouped.get(lid, []), emails, lid)
        rows.append({
            "learner_id": learner.source_id, "name": learner.name, "email": learner.email,
            "attendance": attendance_status(row.attendance, row.duration_seconds) if row else "not_available",
            "first_join": row.first_join if row else None,
            "last_leave": row.last_leave if row else None,
            "duration_seconds": row.duration_seconds if row else None,
            "speaking_seconds": speaking_seconds,
            "speaking_match": speaking_match,
            "speaking_partial": bool(speech_row is not None and speech_row.speaking_partial),
            "camera_on": row.camera_on if row else None,
            "microphone_on": row.microphone_on if row else None,
            "needs_review": bool(row and row.needs_review),
        })
    assets = []
    for asset in sorted(visible_resources(lecture), key=lambda a: (a.kind, a.source_created_at.isoformat() if a.source_created_at else "", a.pk)):
        assets.append({
            "id": asset.pk, "kind": asset.kind, "available": asset.readable,
            "created_at": asset.source_created_at,
            "url": f"/api/lectures/{lecture.pk}/resources/{asset.pk}" if asset.readable else None,
        })
    return {
        **lecture_summary(lecture, emails, enrolled, courses(), canonical), "participants": rows, "resources": assets,
        "recording_folder_url": lecture.recording_folder_url or None,
        "timezone": "Africa/Cairo",
    }
