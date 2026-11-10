"""Normalized application models for the Alfanar learning export.

Every record carries its source identifier so repeated imports (Excel or
Google Sheets) update rows in place instead of duplicating them. Nullable
fields mean "not present in the source" and are never coerced to zero.
"""

import uuid

from django.db import models


class ActivityKind(models.TextChoices):
    QUIZ = "quiz", "Quiz"
    VIDEO = "video", "Video"
    PDF = "pdf", "PDF"
    AUDIO = "audio", "Audio"
    READING = "reading", "Reading"
    ASSIGNMENT = "assignment", "Assignment"


LESSON_KINDS = (ActivityKind.VIDEO, ActivityKind.PDF, ActivityKind.AUDIO, ActivityKind.READING)


class SyncRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    source_type = models.CharField(max_length=20)  # excel | google
    source_ref = models.CharField(max_length=500, blank=True)  # file name / spreadsheet id (no secrets)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    source_modified_at = models.DateTimeField(null=True, blank=True)
    latest_activity_at = models.DateTimeField(null=True, blank=True)
    fingerprint = models.CharField(max_length=64, blank=True)
    changed = models.BooleanField(default=False)
    counts = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    errors = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-started_at"]


class Course(models.Model):
    source_id = models.BigIntegerField(unique=True)
    title = models.CharField(max_length=300)
    category = models.CharField(max_length=300, blank=True)
    tutor_source_id = models.BigIntegerField(null=True, blank=True)
    tutor_name = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["title"]

    @property
    def display_title(self):
        from .course_labels import course_display_name

        return course_display_name(self.source_id, self.title)

    @property
    def display_order_key(self):
        from .course_labels import course_order_key

        return course_order_key(self.source_id, self.display_title)

    def __str__(self):
        return self.display_title


class Learner(models.Model):
    source_id = models.BigIntegerField(unique=True)
    name = models.CharField(max_length=300)
    email = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"Learner {self.source_id}"


class Enrollment(models.Model):
    """One learner in one course, with the LMS summary values reported by the source."""

    learner = models.ForeignKey(Learner, on_delete=models.CASCADE, related_name="enrollments")
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="enrollments")
    progress_pct = models.FloatField(null=True, blank=True)  # LMS-reported course progress
    reported_quizzes = models.IntegerField(null=True, blank=True)
    reported_videos = models.IntegerField(null=True, blank=True)
    reported_text_lessons = models.IntegerField(null=True, blank=True)
    reported_pdfs = models.IntegerField(null=True, blank=True)
    reported_materials = models.IntegerField(null=True, blank=True)
    reported_submissions = models.IntegerField(null=True, blank=True)
    reported_total_seconds = models.IntegerField(null=True, blank=True)
    evidence_count = models.IntegerField(null=True, blank=True)
    last_login = models.DateTimeField(null=True, blank=True)
    registered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["learner", "course"], name="uniq_enrollment")]


class Activity(models.Model):
    """A learning item (quiz, lesson, assignment) known for a course from the export."""

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="activities")
    kind = models.CharField(max_length=12, choices=ActivityKind.choices)
    source_id = models.BigIntegerField()
    title = models.CharField(max_length=500)
    questions_count = models.IntegerField(null=True, blank=True)  # quizzes only

    class Meta:
        ordering = ["kind", "title"]
        constraints = [
            models.UniqueConstraint(fields=["course", "kind", "source_id"], name="uniq_activity")
        ]


class LessonRecord(models.Model):
    """A recorded lesson completion (video/pdf/audio/reading) for an enrollment."""

    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="lesson_records")
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="lesson_records")
    recorded_seconds = models.IntegerField(null=True, blank=True)  # null = duration unavailable
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    time_source = models.CharField(max_length=40, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["enrollment", "activity"], name="uniq_lesson_record")
        ]


class QuizResult(models.Model):
    """Per learner/quiz summary. Latest attempt fields are derived from QuizAttempt rows."""

    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="quiz_results")
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="quiz_results")
    attempts_count = models.IntegerField(null=True, blank=True)
    best_score = models.FloatField(null=True, blank=True)
    latest_attempt_number = models.IntegerField(null=True, blank=True)
    latest_score = models.FloatField(null=True, blank=True)
    latest_status = models.CharField(max_length=20, blank=True)
    ever_passed = models.BooleanField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["enrollment", "activity"], name="uniq_quiz_result")
        ]


class QuizAttempt(models.Model):
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="quiz_attempts")
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="quiz_attempts")
    attempt_number = models.IntegerField()
    score = models.FloatField(null=True, blank=True)
    status = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    source_learner_id = models.BigIntegerField(null=True, blank=True)
    source_attempt_number = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["attempt_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["enrollment", "activity", "attempt_number"], name="uniq_quiz_attempt"
            )
        ]


class Submission(models.Model):
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="submissions")
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="submissions")
    source_id = models.BigIntegerField(unique=True)
    submission_number = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, blank=True)
    grade = models.FloatField(null=True, blank=True)  # assignment grade, not a module grade
    updated_at = models.DateTimeField(null=True, blank=True)


class DashboardAccess(models.Model):
    can_manage_identities = models.BooleanField(default=False)
    """Grants a (non-staff) user access to the dashboard API. Superusers are always allowed.

    Temporary accounts carry an expiry; access is refused after `expires_at`.
    """

    user = models.OneToOneField("auth.User", on_delete=models.CASCADE, related_name="dashboard_access")
    is_temporary = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    def is_valid(self, now=None) -> bool:
        from django.utils import timezone

        now = now or timezone.now()
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > now)


# ----------------------------------------------------------------------------------------
# Microsoft Teams attendance (separate source; never touched by the LMS/Google sync).
# Linked to the LMS registry by source IDs, not foreign keys, so LMS reconciliation can
# never cascade-delete or overwrite attendance.
# ----------------------------------------------------------------------------------------

class AttendanceImportRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    source_ref = models.CharField(max_length=300, blank=True)  # file name only
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    source_modified_at = models.DateTimeField(null=True, blank=True)
    coverage_start = models.DateField(null=True, blank=True)
    coverage_end = models.DateField(null=True, blank=True)
    changed = models.BooleanField(default=False)
    diagnostics = models.JSONField(default=dict, blank=True)  # aggregate counts only, no personal data
    warnings = models.JSONField(default=list, blank=True)
    errors = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-started_at"]


class TeamsMeeting(models.Model):
    class Mapping(models.TextChoices):
        EXACT_TITLE = "exact_title", "Exact module title"
        APPROVED_RULE = "approved_rule", "Approved mapping rule"
        UNMAPPED = "unmapped", "Unmapped (needs review)"

    meeting_id = models.CharField(max_length=300, unique=True)  # Teams "Meeting ID"
    subject = models.CharField(max_length=500)
    course_source_id = models.BigIntegerField(null=True, blank=True)
    mapping_status = models.CharField(max_length=20, choices=Mapping.choices, default=Mapping.UNMAPPED)
    mapping_note = models.CharField(max_length=500, blank=True)
    evidence_source = models.CharField(max_length=30, default="teams_intervals")
    source_file = models.CharField(max_length=300, blank=True)


class LectureSession(models.Model):
    """One lecture occurrence = one Teams meeting series on one UTC date (reports merged)."""

    meeting = models.ForeignKey(TeamsMeeting, on_delete=models.CASCADE, related_name="sessions")
    session_date = models.DateField()
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    report_count = models.IntegerField()
    verified_learners = models.IntegerField()       # learners meeting the attendance threshold
    delivered = models.BooleanField()               # passes the delivered-session rule
    exclusion_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["session_date"]
        constraints = [models.UniqueConstraint(fields=["meeting", "session_date"], name="uniq_lecture_session")]


class SessionAttendance(models.Model):
    """Verified learner presence in one lecture occurrence (merged, de-duplicated intervals)."""

    session = models.ForeignKey(LectureSession, on_delete=models.CASCADE, related_name="attendance")
    learner_source_id = models.BigIntegerField(db_index=True)
    presence_seconds = models.IntegerField(null=True, blank=True)
    interval_count = models.IntegerField(null=True, blank=True)
    first_join = models.DateTimeField(null=True, blank=True)
    last_leave = models.DateTimeField(null=True, blank=True)
    attended = models.BooleanField()                # presence >= attendance threshold
    match_method = models.CharField(max_length=20)  # email | alias

    class Meta:
        constraints = [models.UniqueConstraint(fields=["session", "learner_source_id"], name="uniq_session_attendance")]


class TimingImport(models.Model):
    """Immutable timing snapshots; their evidence never updates binary attendance."""

    fingerprint = models.CharField(max_length=64, unique=True)
    source_name = models.CharField(max_length=300)
    source_sha256 = models.CharField(max_length=64)
    policy_sha256 = models.CharField(max_length=64)
    registry_sha256 = models.CharField(max_length=64)
    calendar_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=False)
    counts = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["active"], condition=models.Q(active=True),
                                               name="one_active_timing_import")]


class LectureTiming(models.Model):
    """Timing-only learner/date evidence, including records awaiting calendar review.

    Source IDs deliberately do not cascade from LMS, matrix or lecture imports.
    Only verified records attached to an existing lecture are exposed by the API.
    """

    class Status(models.TextChoices):
        VERIFIED = "verified", "Verified lecture"
        AMBIGUOUS_DATE = "ambiguous_date", "Ambiguous workbook date"
        OFF_CALENDAR = "off_calendar", "No current lecture date"
        DATE_REVIEW = "date_review", "Lecture date needs verification"
        BOUNDARY_REVIEW = "boundary_review", "UTC date boundary needs review"

    import_run = models.ForeignKey(TimingImport, on_delete=models.CASCADE, related_name="records")
    course_source_id = models.BigIntegerField()
    learner_source_id = models.BigIntegerField()
    session_date = models.DateField()
    status = models.CharField(max_length=30, choices=Status.choices)
    first_join = models.DateTimeField()
    last_leave = models.DateTimeField()
    duration_microseconds = models.PositiveBigIntegerField()
    visit_count = models.PositiveIntegerField()
    # Original timestamps (including sub-microsecond precision), report/record/meeting
    # IDs and match method. No names or email addresses are needed in this table.
    provenance = models.JSONField(default=list)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["import_run", "course_source_id", "learner_source_id", "session_date"],
            name="unique_timing_snapshot_pair")]
        indexes = [models.Index(fields=["learner_source_id", "course_source_id", "status"],
                                name="timing_learner_course")]


class LearnerIdentity(models.Model):
    """Every retained LMS account and email, including approved merged identities."""
    source_id = models.BigIntegerField(unique=True)
    canonical_source_id = models.BigIntegerField(db_index=True)
    email = models.CharField(max_length=300, blank=True)
    name = models.CharField(max_length=300)


class AcademicEvidence(models.Model):
    """Read-only source rows behind reconciled summaries; conflicting values remain reviewable."""
    sheet = models.CharField(max_length=100)
    row_number = models.IntegerField()
    source_learner_id = models.BigIntegerField(db_index=True)
    canonical_source_id = models.BigIntegerField(db_index=True)
    payload = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["sheet", "row_number"], name="uniq_academic_evidence")]


class TeamsDiscoveryState(models.Model):
    """Durable checkpoints; historical cutover is immutable after initialization."""
    key = models.CharField(max_length=40, primary_key=True)
    cutover_at = models.DateTimeField()
    checkpoints = models.JSONField(default=dict)
    pending_events = models.JSONField(default=dict)
    baseline_sha256 = models.CharField(max_length=64)


class TeamsDiscoveryRun(models.Model):
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=20, default="running")
    counts = models.JSONField(default=dict)
    diagnostics = models.JSONField(default=list)


class TeamsDiscoveredSeries(models.Model):
    """Private Graph identifiers, bound to an approved organizer/course relationship."""
    organizer_id = models.CharField(max_length=100)
    meeting_id = models.CharField(max_length=1024)
    course_source_id = models.BigIntegerField()
    subject = models.CharField(max_length=500)
    join_url = models.TextField(blank=True)
    provenance = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organizer_id", "meeting_id"], name="unique_discovery_series")]


class TeamsOccurrence(models.Model):
    """One canonical calendar occurrence, never one row per report or per date."""
    key = models.CharField(max_length=64, unique=True)
    series = models.ForeignKey(TeamsDiscoveredSeries, on_delete=models.PROTECT, related_name="occurrences")
    course_source_id = models.BigIntegerField()
    calendar_uid = models.TextField()
    scheduled_start = models.DateTimeField()
    scheduled_end = models.DateTimeField()
    starts_at = models.DateTimeField(null=True)
    ends_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=20, default="pending")
    reason = models.CharField(max_length=80, default="awaiting_complete_report")
    provenance = models.JSONField(default=dict)
    needs_review = models.BooleanField(default=False)


class TeamsReportEvidence(models.Model):
    """Immutable report revisions, including rejected/partial replies, for private audit."""
    series = models.ForeignKey(TeamsDiscoveredSeries, on_delete=models.PROTECT, related_name="reports")
    report_id = models.CharField(max_length=1024)
    sha256 = models.CharField(max_length=64)
    payload = models.JSONField()
    complete = models.BooleanField()
    retrieved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["series", "report_id", "sha256"], name="unique_discovery_report_revision")]


class TeamsOccurrenceAttendance(models.Model):
    occurrence = models.ForeignKey(TeamsOccurrence, on_delete=models.PROTECT, related_name="attendance")
    learner_source_id = models.BigIntegerField()
    first_join = models.DateTimeField()
    last_leave = models.DateTimeField()
    duration_microseconds = models.PositiveBigIntegerField()
    visit_count = models.PositiveIntegerField()
    intervals = models.JSONField(default=list)
    evidence_sha256 = models.CharField(max_length=64)
    needs_review = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["occurrence", "learner_source_id"], name="unique_discovery_learner_occurrence")]


class IdentitySourceSnapshot(models.Model):
    """Immutable original LMS tables, never the active merged/archive projection."""
    sha256 = models.CharField(max_length=64, unique=True)
    tables = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)


class IdentityState(models.Model):
    key = models.PositiveSmallIntegerField(primary_key=True, default=1)
    revision = models.PositiveBigIntegerField(default=0)
    restored = models.BooleanField(default=False)
    snapshot = models.ForeignKey(IdentitySourceSnapshot, null=True, on_delete=models.PROTECT)


class IdentityGroup(models.Model):
    import uuid
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    primary_source_id = models.BigIntegerField()
    active = models.BooleanField(default=True)
    # Direct parents are retained so undoing a nested merge restores its prior groups.
    parent_groups = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)


class OriginalLearner(models.Model):
    source_id = models.BigIntegerField(primary_key=True)
    name = models.CharField(max_length=300)
    email = models.CharField(max_length=300, blank=True)
    enrollments = models.JSONField(default=list)
    in_source = models.BooleanField(default=True)
    archived = models.BooleanField(default=False)
    group = models.ForeignKey(IdentityGroup, null=True, on_delete=models.PROTECT)


class IdentityMembership(models.Model):
    group = models.ForeignKey(IdentityGroup, on_delete=models.PROTECT, related_name='memberships')
    original = models.ForeignKey(OriginalLearner, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['group', 'original'], name='unique_identity_membership')]


class IdentityOperation(models.Model):
    import uuid
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL)
    kind = models.CharField(max_length=30)
    revision = models.PositiveBigIntegerField()
    snapshot_sha256 = models.CharField(max_length=64)
    request = models.JSONField(default=dict)
    preview = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True)


class IdentityEvidenceArchive(models.Model):
    """Retained pre-restoration observations and evidence needing identity review."""
    key = models.CharField(max_length=64, unique=True)
    reason = models.CharField(max_length=100)
    payload = models.JSONField()
    needs_review = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
