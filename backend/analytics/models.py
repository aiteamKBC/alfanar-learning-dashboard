"""Normalized application models for the Alfanar learning export.

Every record carries its source identifier so repeated imports (Excel or
Google Sheets) update rows in place instead of duplicating them. Nullable
fields mean "not present in the source" and are never coerced to zero.
"""

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

    def __str__(self):
        return self.title


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
