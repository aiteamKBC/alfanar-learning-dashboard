"""Import collected evidence without touching LMS, KPI or historical source tables.

Directory identities require verified emails. Transcript labels can attribute
estimated speech to a uniquely named participant in that same meeting, but do
not create learner identities or claim an identity-verified speaker binding.
"""
import datetime as dt
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django.db import transaction

from ..models import Course, LectureCapture, LectureLearner, LectureResource
from .library import registry
from .daily import attendance_timing, combine_days

SUBJECTS = {
    "Level 7 Diploma in Project Management": 83334,
    "Andrew Operations and Information Management for Project Managers": 135945,
    "Advanced Project and Logistics Management": 154268,
}
CAIRO = ZoneInfo("Africa/Cairo")


def timestamp(value):
    if not value:
        return None
    result = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Evidence timestamps must include a timezone")
    return result.astimezone(dt.timezone.utc)


def union(intervals):
    result = []
    for start, end in sorted(set(intervals)):
        if end < start:
            raise ValueError("Invalid attendance interval")
        if start == end:
            continue
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def seconds(intervals):
    return sum((b - a).total_seconds() for a, b in union(intervals))


def speaker_label(value):
    """Exact text comparison, ignoring only case and whitespace; never fuzzy."""
    return " ".join(str(value or "").split()).casefold()


def within_presence(start, end, intervals):
    # Source timestamps can differ slightly at a join/leave boundary.
    tolerance = dt.timedelta(seconds=2)
    return end > start and any(a - tolerance <= start and end <= b + tolerance for a, b in union(intervals))


def source_tables(source):
    result = {}
    for table in source.get("previous", {}).get("historical_sources", []):
        name = table["range"].split("!")[0].strip("'")
        rows = table.get("values", [])
        result[name] = [dict(zip(rows[0], row)) for row in rows[1:] if row] if rows else []
    return result


def folder_url(value):
    if not value:
        return ""
    parsed = urlsplit(value)
    # Never publish signed download URLs, tokens, or arbitrary source URLs.
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".sharepoint.com") or parsed.query or parsed.fragment:
        return ""
    return value


def media_window_target(asset, course, prepared):
    """Locate a file only in one already-established lecture with timing anchors.

    Files do not need a complete matching attendance report. This never creates
    a lecture, infers a participant, or matches by a title/date alone.
    """
    start, end = timestamp(asset.get("createdDateTime")), timestamp(asset.get("endDateTime"))
    if not start or not end or end <= start:
        return None
    duration = (end - start).total_seconds()
    matches = []
    for key, lecture in prepared.items():
        if lecture["course"] is None or lecture["course"].pk != course.pk or lecture["label"] == "Recording session":
            continue
        anchors = []
        for observation in lecture["observations"].values():
            if observation["needs_review"]:
                continue
            spans = observation["intervals"]
            if not spans and observation["first_join"] and observation["last_leave"] and observation["duration_seconds"] is not None:
                spans = [(observation["first_join"], observation["last_leave"])]
            anchors.extend(spans)
        # At least half the file, including 60 seconds of verified cohort
        # presence, must overlap. Multiple possible lectures stay unresolved.
        overlap = seconds([(max(start, a), min(end, b)) for a, b in anchors if min(end, b) > max(start, a)])
        if overlap >= 60 and overlap / duration >= 0.5:
            matches.append(key)
    return matches[0] if len(matches) == 1 else None


def import_source(path, speaker_bindings=None):
    source = json.loads(Path(path).read_text(encoding="utf-8"))
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    learners, emails, enrolled = registry()
    by_source = {x.source_id: x for x in learners.values()}
    courses = {x.source_id: x for x in Course.objects.all()}
    tables = source_tables(source)
    historical_learners = {str(x["LearnerID"]): x for x in tables.get("AT_Learners", [])}
    prepared, archived_dates = {}, defaultdict(list)

    for row in tables.get("AT_Lectures", []):
        course = courses.get(int(row["CourseID"]))
        if not course:
            continue
        day = dt.date.fromisoformat(row["LectureDate"]) if row.get("LectureDate") else None
        key = str(row["LectureID"])
        prepared[key] = {
            "course": course, "day": day, "starts": timestamp(row.get("ScheduledStartUTC")),
            "ends": timestamp(row.get("ScheduledEndUTC")), "label": "Historical attendance",
            "reports": set(), "observations": {}, "resources": {}, "folder": "",
        }
        if day:
            archived_dates[(course.pk, day)].append(key)

    for row in tables.get("AT_Attendance", []):
        lecture = prepared.get(row.get("LectureID"))
        identity = historical_learners.get(str(row.get("LearnerID")), {})
        email = str(identity.get("RegisteredEmail") or "").strip().casefold()
        learner = by_source.get(int(row["LearnerID"]))
        if not lecture or not learner or emails.get(email) != learner.pk or learner.pk not in enrolled[lecture["course"].pk]:
            continue
        duration = row.get("DurationSeconds")
        lecture["observations"][learner.pk] = {
            "source_emails": {email}, "identity_ids": set(), "intervals": [],
            "attendance": {"Present": "present", "Absent": "absent", "Review": "review"}.get(row.get("EffectiveStatus"), "not_available"),
            "first_join": timestamp(row.get("FirstJoinUTC")), "last_leave": timestamp(row.get("LastLeaveUTC")),
            "duration_seconds": float(duration) if isinstance(duration, (int, float)) else None,
            "speaking_seconds": None, "speaker_verified": False,
            "speaking_match": "", "speaking_emails": set(), "speaking_partial": False,
            "camera_on": None, "microphone_on": None,
            "timing_source": str(row.get("PreferredTimingSource") or "Historical attendance"),
            "needs_review": bool(row.get("NeedsReview")),
        }

    groups, report_groups = {}, {}
    reports = source.get("previous", {}).get("attendance", {}).get("reports", []) + source.get("extra_reports", [])
    incomplete = 0
    for report in reports:
        course = courses.get(SUBJECTS.get(report.get("subject")))
        if not course:
            continue
        start, end = timestamp(report.get("start")), timestamp(report.get("end"))
        if not start or not end or end < start:
            incomplete += 1
            continue
        mid, rid = report["meeting_id"], report["report_id"]
        records = source.get("attendance_records", {}).get(mid + "|" + rid)
        expected = report.get("expected")
        if records is None or expected is not None and len(records) != expected:
            incomplete += 1
            continue
        group_key = (course.pk, mid, start, end)
        group = groups.setdefault(group_key, {"course": course, "start": start, "end": end, "mid": mid, "reports": set(), "observations": {}, "speaker_labels": defaultdict(set)})
        group["reports"].add(rid)
        report_groups[(mid, rid)] = group_key
        for record in records:
            email = str(record.get("emailAddress") or "").strip().casefold()
            lid = emails.get(email)
            label = speaker_label(record.get("identity", {}).get("displayName"))
            if label:
                # Include outsiders in ambiguity detection, even though they can
                # never become learner rows or receive displayed speaking time.
                identity = record.get("identity", {}).get("id") or email or record["id"]
                candidate = ("learner", lid) if lid in enrolled[course.pk] else ("external", identity)
                group["speaker_labels"][label].add(candidate)
            if lid not in enrolled[course.pk]:
                continue
            observation = group["observations"].setdefault(lid, {
                "source_emails": set(), "identity_ids": set(), "intervals": [],
                "camera_on": None, "microphone_on": None, "needs_review": False,
            })
            observation["source_emails"].add(email)
            identity_id = record.get("identity", {}).get("id")
            if identity_id:
                observation["identity_ids"].add(identity_id)
            intervals = record.get("attendanceIntervals") or []
            for interval in intervals:
                a, b = timestamp(interval.get("joinDateTime")), timestamp(interval.get("leaveDateTime"))
                if not a or not b or b < a:
                    raise ValueError("Invalid learner attendance interval")
                observation["intervals"].append((a, b))
            if not intervals and record.get("totalAttendanceInSeconds", 0) > 0:
                observation["needs_review"] = True
            for event in record.get("engagements") or []:
                if event.get("engagementSubType") == "cameraOn":
                    observation["camera_on"] = True
                if event.get("engagementSubType") == "unmute":
                    observation["microphone_on"] = True

    # A historical lecture can receive report fragments only when actual timing
    # for the same canonical learner overlaps its previously verified timing.
    group_targets, held = {}, 0
    for gkey, group in sorted(groups.items(), key=lambda item: (item[1]["start"], item[1]["mid"])):
        day = group["start"].astimezone(CAIRO).date()
        candidates = []
        for key in archived_dates[(group["course"].pk, day)]:
            old = prepared[key]["observations"]
            if any(
                lid in old and old[lid]["first_join"] and old[lid]["last_leave"]
                and any((min(b, old[lid]["last_leave"]) - max(a, old[lid]["first_join"])).total_seconds() >= 60 for a, b in o["intervals"])
                for lid, o in group["observations"].items()
            ):
                candidates.append(key)
        if len(candidates) == 1:
            key = candidates[0]
        elif sum(seconds(o["intervals"]) >= 1800 for o in group["observations"].values()) >= 5:
            key = "graph:" + hashlib.sha256(json.dumps([group["course"].source_id, group["mid"], group["start"].isoformat(), group["end"].isoformat()]).encode()).hexdigest()
            prepared[key] = {"course": group["course"], "day": day, "starts": None, "ends": None,
                             "label": "Teams attendance", "reports": set(), "observations": {}, "resources": {}, "folder": ""}
        else:
            held += 1
            continue
        group_targets[gkey] = key
        lecture = prepared[key]
        lecture["reports"].update(group["reports"])
        lecture["starts"] = min(lecture["starts"], group["start"]) if lecture["starts"] else group["start"]
        lecture["ends"] = max(lecture["ends"], group["end"]) if lecture["ends"] else group["end"]
        if lecture["label"] == "Historical attendance":
            lecture["label"] = "Historical attendance + Teams"
        for lid, item in group["observations"].items():
            observation = lecture["observations"].setdefault(lid, {
                "source_emails": set(), "identity_ids": set(), "intervals": [], "attendance": "not_available",
                "first_join": None, "last_leave": None, "duration_seconds": None,
                "speaking_seconds": None, "speaker_verified": False,
                "speaking_match": "", "speaking_emails": set(), "speaking_partial": False, "camera_on": None,
                "microphone_on": None, "timing_source": "", "needs_review": False,
            })
            for field in ("source_emails", "identity_ids"):
                observation[field].update(item[field])
            observation["intervals"].extend(item["intervals"])
            for field in ("camera_on", "microphone_on"):
                if item[field] is True:
                    observation[field] = True
            observation["needs_review"] |= item["needs_review"]

    asset_targets = {}
    media_window_links = 0
    contents = source.get("transcript_contents", {})
    for series in source.get("series", []):
        course = courses.get(SUBJECTS.get(series.get("subject")))
        # Explicit project archive title; its module is deliberately unresolved.
        project_archive = series.get("subject") == "PCP Alfanar Group"
        if not course and not project_archive:
            continue
        for source_kind, kind in (("recordings", "recording"), ("transcripts", "transcript")):
            for asset in series.get(source_kind, []):
                provider = asset.get("provider", "teams")
                if provider not in {"teams", "onedrive"}:
                    raise ValueError("Unsupported media provider")
                if provider == "onedrive" and (kind != "recording" or not asset.get("drive_id")):
                    raise ValueError("OneDrive recording requires an exact drive item reference")
                if project_archive and provider != "onedrive":
                    raise ValueError("Project archive requires an original OneDrive item")
                call = asset.get("callId")
                pair = (series["meeting_id"], call)
                target = asset_targets.get(pair) if call else None
                target = target or group_targets.get(report_groups.get(pair))
                if not target and course:
                    target = media_window_target(asset, course, prepared)
                    if target:
                        media_window_links += 1
                if not target:
                    # Media visibility must not depend on learner quorum. A
                    # source recording is retained in its own labelled session,
                    # never promoted to an approved delivered lecture/KPI.
                    target = "media:" + hashlib.sha256(json.dumps([course.source_id if course else "unassigned", series["meeting_id"], call or asset["id"]]).encode()).hexdigest()
                    start, end = timestamp(asset.get("createdDateTime")), timestamp(asset.get("endDateTime"))
                    if target not in prepared:
                        prepared[target] = {"course": course, "day": start.astimezone(CAIRO).date() if start else None,
                                            "starts": start, "ends": end, "label": "Recording session",
                                            "reports": set(), "observations": {}, "resources": {}, "folder": ""}
                        raw = groups.get(report_groups.get(pair))
                        if raw:
                            prepared[target]["reports"].update(raw["reports"])
                            for lid, observation in raw["observations"].items():
                                prepared[target]["observations"][lid] = {
                                    "source_emails": set(observation["source_emails"]), "identity_ids": set(observation["identity_ids"]),
                                    "intervals": list(observation["intervals"]), "attendance": "not_available",
                                    "first_join": None, "last_leave": None, "duration_seconds": None,
                                    "speaking_seconds": None, "speaker_verified": False, "speaking_match": "",
                                    "speaking_emails": set(), "speaking_partial": False,
                                    "camera_on": observation["camera_on"], "microphone_on": observation["microphone_on"],
                                    "timing_source": "", "needs_review": observation["needs_review"],
                                }
                lecture = prepared[target]
                if lecture["label"] == "Recording session":
                    start, end = timestamp(asset.get("createdDateTime")), timestamp(asset.get("endDateTime"))
                    if start and (lecture["starts"] is None or start < lecture["starts"]):
                        lecture["starts"], lecture["day"] = start, start.astimezone(CAIRO).date()
                    if end and (lecture["ends"] is None or end > lecture["ends"]):
                        lecture["ends"] = end
                # The original folder is a navigational reference, never a file-name match.
                folder = next((x.get("folder_url", "") for x in source.get("recording_folders", []) if x["organizer_id"] == series["organizer_id"]), "")
                lecture["folder"] = folder_url(folder)
                content = contents.get(asset["id"], {}) if kind == "transcript" else {}
                lecture["resources"][(kind, provider, asset["id"])] = {
                    "drive_id": asset.get("drive_id", ""),
                    "organizer_id": series["organizer_id"], "meeting_id": series["meeting_id"],
                    "source_created_at": timestamp(asset.get("createdDateTime")),
                    "readable": bool(asset.get("content_available", True)) if kind == "recording" else bool(content.get("text")),
                    "text_content": content.get("text", ""), "vtt_content": content.get("vtt", ""),
                }
                asset_targets[(series["meeting_id"], call)] = target

    source_windows = len(prepared)
    prepared, daily_targets = combine_days(prepared)
    asset_targets = {pair: daily_targets[target] for pair, target in asset_targets.items()}

    # Match transcript labels only within the actual report/window, to a unique
    # participant already identified by an unambiguous registered email. The
    # label-based attribution is explicitly distinct from a verified binding.
    speech_intervals = defaultdict(list)
    verified_intervals = defaultdict(list)
    speech_emails = defaultdict(set)
    partial = set()
    for transcript in source.get("timed_transcripts", []):
        pair = (transcript["meeting_id"], transcript.get("call_id"))
        target = asset_targets.get(pair)
        group = groups.get(report_groups.get(pair))
        if not target or not group or not transcript.get("readable"):
            continue
        for segment in transcript.get("segments", []):
            candidates = group["speaker_labels"].get(speaker_label(segment.get("name")), set())
            if len(candidates) != 1:
                continue
            kind, lid = next(iter(candidates))
            if kind != "learner" or lid not in group["observations"]:
                continue
            observation = group["observations"][lid]
            start, end = timestamp(segment["start"]), timestamp(segment["end"])
            if not start or not end:
                continue
            key = (target, lid)
            if not within_presence(start, end, observation["intervals"]):
                partial.add(key)
                continue
            speech_intervals[key].append((start, end))
            speech_emails[key].update(observation["source_emails"])

    # Optional owner-verified bindings still require an exact Graph identity in
    # the same report. A manual verified binding can supersede label attribution.
    bindings = json.loads(Path(speaker_bindings).read_text(encoding="utf-8")) if speaker_bindings else []
    for binding in bindings:
        if binding.get("verified") is not True or not binding.get("evidence"):
            raise ValueError("Speaker bindings require explicit verification evidence")
        pair = (binding["meeting_id"], binding["call_id"])
        target = asset_targets.get(pair)
        if not target:
            continue
        lecture = prepared[target]
        group = groups.get(report_groups.get(pair))
        matched = [lid for lid, o in (group["observations"] if group else {}).items()
                   if binding["identity_id"] in o["identity_ids"] and lid in lecture["observations"]]
        if len(matched) != 1:
            raise ValueError("Speaker identity does not uniquely match the lecture roster")
        for transcript in source.get("timed_transcripts", []):
            if (transcript["meeting_id"], transcript.get("call_id")) != pair:
                continue
            for segment in transcript.get("segments", []):
                if segment["name"] == binding["speaker_name"]:
                    key = (target, matched[0])
                    interval = (timestamp(segment["start"]), timestamp(segment["end"]))
                    speech_intervals[key].append(interval)
                    verified_intervals[key].append(interval)
                    speech_emails[key].update(lecture["observations"][matched[0]]["source_emails"])

    with transaction.atomic():
        saved, observation_count, resource_count = [], 0, 0
        for key, item in prepared.items():
            lecture, _ = LectureCapture.objects.update_or_create(source_key=key, defaults={
                "course": item["course"], "session_date": item["day"], "starts_at": item["starts"],
                "ends_at": item["ends"], "source_label": item["label"], "report_ids": sorted(item["reports"]),
                "recording_folder_url": item["folder"], "source_fingerprint": digest, "active": True,
            })
            saved.append(lecture.pk)
            kept_learners = []
            for lid, row in item["observations"].items():
                intervals = row.pop("intervals")
                first, last, duration = attendance_timing(intervals, row.pop("timing_claims"), union, seconds)
                if duration is not None:
                    row.update(first_join=first or row["first_join"], last_leave=last or row["last_leave"], duration_seconds=duration)
                    if intervals:
                        row["timing_source"] = "Teams attendance intervals"
                    if duration >= 1800:
                        row["attendance"] = "present"
                    elif intervals and row["attendance"] not in {"present", "absent"}:
                        row["attendance"] = "review"
                speech = speech_intervals.get((key, lid))
                if speech:
                    duration = seconds(speech)
                    verified = bool(verified_intervals.get((key, lid))) and abs(seconds(verified_intervals[(key, lid)]) - duration) < 0.000001
                    row.update(speaking_seconds=duration, speaker_verified=verified,
                               speaking_match="verified_identity" if verified else "meeting_name",
                               speaking_emails=speech_emails[(key, lid)],
                               speaking_partial=(key, lid) in partial and not verified)
                for field in ("source_emails", "identity_ids", "speaking_emails"):
                    row[field] = sorted(row[field])
                LectureLearner.objects.update_or_create(lecture=lecture, learner_id=lid, defaults=row)
                kept_learners.append(lid)
                observation_count += 1
            lecture.observations.exclude(learner_id__in=kept_learners).delete()
            kept_resources = []
            for (kind, provider, source_id), values in item["resources"].items():
                asset, _ = LectureResource.objects.update_or_create(lecture=lecture, provider=provider, kind=kind, source_id=source_id, defaults=values)
                kept_resources.append(asset.pk)
                resource_count += 1
            lecture.resources.exclude(pk__in=kept_resources).delete()
        LectureCapture.objects.exclude(pk__in=saved).update(active=False)
    return {"lectures": len(saved), "learner_observations": observation_count, "resources": resource_count,
            "held_report_windows": held, "incomplete_reports": incomplete,
            "media_window_links": media_window_links,
            "recording_sessions": sum(item["label"] == "Recording session" for item in prepared.values()),
            "merged_same_day_windows": source_windows - len(prepared),
            "speaking_rows": len(speech_intervals),
            "verified_speaker_rows": sum(bool(verified_intervals.get(k)) and abs(seconds(verified_intervals[k]) - seconds(v)) < 0.000001 for k, v in speech_intervals.items())}
