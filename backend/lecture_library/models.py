"""PostgreSQL-only library. External registry references are stable LMS IDs, never cross-database FKs."""
import uuid
from django.db import models


class LectureCapture(models.Model):
    """Lecture library, separate from the approved historical attendance/KPI tables."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_key = models.CharField(max_length=100, unique=True)
    course_source_id = models.BigIntegerField(null=True, db_index=True)
    session_date = models.DateField(null=True)
    starts_at = models.DateTimeField(null=True)
    ends_at = models.DateTimeField(null=True)
    source_label = models.CharField(max_length=80)
    report_ids = models.JSONField(default=list)
    recording_folder_url = models.URLField(max_length=1500, blank=True)
    source_fingerprint = models.CharField(max_length=64)
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [models.F("session_date").desc(nulls_last=True), models.F("starts_at").desc(nulls_last=True), "id"]


class LectureLearner(models.Model):
    lecture = models.ForeignKey(LectureCapture, on_delete=models.CASCADE, related_name="observations")
    learner_source_id = models.BigIntegerField(db_index=True)
    source_emails = models.JSONField(default=list)
    identity_ids = models.JSONField(default=list)
    attendance = models.CharField(max_length=20, default="not_available")
    first_join = models.DateTimeField(null=True)
    last_leave = models.DateTimeField(null=True)
    duration_seconds = models.FloatField(null=True)
    speaking_seconds = models.FloatField(null=True)
    speaker_verified = models.BooleanField(default=False)
    speaking_match = models.CharField(max_length=30, blank=True)
    speaking_emails = models.JSONField(default=list)
    speaking_partial = models.BooleanField(default=False)
    camera_on = models.BooleanField(null=True)
    microphone_on = models.BooleanField(null=True)
    timing_source = models.CharField(max_length=60, blank=True)
    needs_review = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["lecture", "learner_source_id"], name="unique_lecture_library_learner")]


class LectureResource(models.Model):
    lecture = models.ForeignKey(LectureCapture, on_delete=models.CASCADE, related_name="resources")
    kind = models.CharField(max_length=15, choices=[("recording", "Recording"), ("transcript", "Transcript")])
    provider = models.CharField(max_length=15, choices=[("teams", "Teams"), ("onedrive", "OneDrive")], default="teams")
    drive_id = models.CharField(max_length=300, blank=True)
    source_id = models.CharField(max_length=1024)
    organizer_id = models.CharField(max_length=100)
    meeting_id = models.CharField(max_length=1024)
    source_created_at = models.DateTimeField(null=True)
    readable = models.BooleanField(default=False)
    text_content = models.TextField(blank=True)
    vtt_content = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["lecture", "provider", "kind", "source_id"], name="unique_lecture_library_resource")]
