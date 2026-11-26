"""Read-only JSON API. All data endpoints require an authenticated session."""

from django.contrib.auth import authenticate, login, logout
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.http import Http404
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.decorators import api_view, permission_classes, throttle_classes, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from django.utils.dateparse import parse_date
from django.db.models import Max, Q

from rest_framework.exceptions import APIException, NotFound

from .. import arm as arm_module
from .. import metrics
from ..identity import lms_links
from ..lms_live import ARM_COURSE_ID, ARM_LEARNER_ID_OFFSET, ARM_TITLE, LmsUnavailable, configured, load_snapshot
from .permissions import has_dashboard_access
from ..models import ActivityKind, Course, Enrollment, Learner
from ..lectures import library
from ..lectures.media import resource_response
from ..lectures.lms_media import response as lms_resource_response
from lecture_library.models import LectureResource


class LmsUnavailableError(APIException):
    status_code = 503
    default_detail = "The LMS is currently unavailable. Please try again."


class TranscriptFormatRenderer(JSONRenderer):
    # Accept the existing ?format=vtt download link during DRF negotiation.
    # Successful downloads supply their own text/vtt HttpResponse; errors stay JSON.
    format = 'vtt'


def _arm(required: bool):
    """This request's live LMS view of Advanced Research Methods (never stored).

    Required: the module itself was requested, so an LMS failure is an error.
    Optional: the module is added beside existing data; existing data is returned unchanged if the LMS fails.
    """
    if not configured():
        if required:
            raise NotFound()
        return None
    try:
        return arm_module.Arm(load_snapshot())
    except LmsUnavailable as exc:
        if required:
            raise LmsUnavailableError(str(exc))
        return None


def _existing_selection(engagement):
    """(learner IDs in the existing modules for this status filter, learner IDs active in existing modules)."""
    enrollments, stats = metrics.filtered_enrollments(None, None)
    everyone = {e.learner.source_id for e in enrollments}
    active = {e.learner.source_id for e in enrollments if stats[e.id]["has_activity"]}
    selected = active if engagement == "active" else everyone - active if engagement == "inactive" else everyone
    return selected, active


class LoginThrottle(SimpleRateThrottle):
    scope = "login"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


def _int_param(request, name):
    v = request.query_params.get(name)
    if v in (None, "", "all"):
        return None
    try:
        return int(v)
    except ValueError:
        raise ValidationError({name: "must be an integer"})


def _choice_param(request, name, choices):
    v = request.query_params.get(name)
    if v in (None, "", "all"):
        return None
    if v not in choices:
        raise ValidationError({name: f"must be one of {sorted(choices)}"})
    return v


ENGAGEMENT = {"active", "inactive"}


# --- Auth ---------------------------------------------------------------------------------

@api_view(["GET"])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def auth_session(request):
    user = request.user
    return Response({"authenticated": user.is_authenticated and has_dashboard_access(user),
                     "user": _user_payload(user) if user.is_authenticated else None})


def _user_payload(user):
    from .permissions import can_manage_identities
    owner = can_manage_identities(user)
    access = None if owner else getattr(user, "dashboard_access", None)
    return {"username": user.get_username(), "name": user.get_full_name() or user.get_username(),
            "temporary": bool(access and access.is_temporary),
            "can_manage_identities": owner,
            "expires_at": access.expires_at if access else None}


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([LoginThrottle])
def auth_login(request):
    username = str(request.data.get("username", "")).strip()
    password = str(request.data.get("password", ""))
    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active:
        return Response({"error": {"status": 400, "message": "Invalid username or password."}}, status=400)
    if not has_dashboard_access(user):
        return Response({"error": {"status": 403, "message": "This account does not have dashboard access, "
                                   "or its temporary access has expired."}}, status=403)
    login(request, user)
    return Response({"authenticated": True, "user": _user_payload(user)})


@api_view(["POST"])
@permission_classes([AllowAny])
def auth_logout(request):
    logout(request)
    return Response({"authenticated": False})


# --- Dashboard ----------------------------------------------------------------------------

@api_view(["GET"])
def filters(request):
    courses = [{"course_id": c.source_id, "title": c.display_title}
               for c in sorted(Course.objects.all(), key=lambda c: c.display_order_key)]
    if configured():
        courses.append({"course_id": ARM_COURSE_ID, "title": ARM_TITLE})
    return Response({
        "courses": courses,
        "activity_types": [{"value": v, "label": l} for v, l in ActivityKind.choices],
        "engagement": [{"value": "active", "label": "Active in LMS"},
                       {"value": "inactive", "label": "No recorded LMS activity"}],
    })


@api_view(["GET"])
def dashboard_summary(request):
    course = _int_param(request, "course")
    engagement = _choice_param(request, "engagement", ENGAGEMENT)
    if course == ARM_COURSE_ID:
        return Response(_arm(required=True).summary(engagement))
    result = metrics.summary(course_id=course, engagement=engagement)
    if course is None and (arm := _arm(required=False)):
        result = arm_module.merge_summary(result, arm, engagement, _existing_selection(engagement))
    return Response(result)


@api_view(["GET"])
def dashboard_charts(request):
    course = _int_param(request, "course")
    engagement = _choice_param(request, "engagement", ENGAGEMENT)
    activity_type = _choice_param(request, "activity_type", set(ActivityKind.values))
    if course == ARM_COURSE_ID:
        return Response(_arm(required=True).charts(engagement, activity_type))
    result = metrics.charts(course_id=course, engagement=engagement, activity_type=activity_type)
    if course is None and (arm := _arm(required=False)):
        result = arm_module.merge_charts(result, arm, engagement, activity_type, _existing_selection(engagement))
    return Response(result)


# --- Learners -----------------------------------------------------------------------------

@api_view(["GET"])
def learner_export(request):
    from ..course_labels import COURSE_DISPLAY_ORDER
    from ..learner_export import module_export
    try:
        selected = {int(value) for value in request.query_params.get('courses', '').split(',')}
    except ValueError:
        raise ValidationError({'courses': 'Select one or more valid modules.'})
    if not selected or not selected.issubset(COURSE_DISPLAY_ORDER):
        raise ValidationError({'courses': 'Select only the four dashboard modules.'})
    file_type = request.query_params.get('file_type', 'xlsx')
    if file_type not in ('xlsx', 'csv') or (file_type == 'csv' and len(selected) != 1):
        raise ValidationError({'file_type': 'Use Excel for multiple modules, or CSV for one module.'})
    with transaction.atomic():  # one consistent read snapshot, including concurrent identity changes
        if Course.objects.filter(source_id__in=selected).count() != len(selected):
            raise ValidationError({'courses': 'A selected module is not available.'})
        return module_export([cid for cid in COURSE_DISPLAY_ORDER if cid in selected], file_type)


@api_view(["GET"])
def learner_list(request):
    ordering = request.query_params.get("ordering", "name")
    search = (request.query_params.get("search") or "").strip() or None
    course = _int_param(request, "course")
    engagement = _choice_param(request, "engagement", ENGAGEMENT)
    if course == ARM_COURSE_ID:
        rows = arm_module.directory(_arm(required=True), search=search, engagement=engagement, ordering=ordering)
    elif course is None and (arm := _arm(required=False)):
        existing = metrics.learner_directory(search=search, course_id=None, engagement=None, ordering=ordering)
        rows = arm_module.merge_directory(existing, arm, search=search, engagement=engagement, ordering=ordering)
    else:
        rows = metrics.learner_directory(search=search, course_id=course, engagement=engagement, ordering=ordering)
    page = max(1, _int_param(request, "page") or 1)
    size = min(100, max(1, _int_param(request, "page_size") or 20))
    total = len(rows)
    return Response({
        "count": total, "page": page, "page_size": size, "pages": max(1, -(-total // size)),
        "ordering": ordering, "results": rows[(page - 1) * size: page * size],
    })


def _learner_detail(learner_id: int) -> dict:
    learner = Learner.objects.filter(source_id=learner_id).first()
    if learner is not None:
        detail = metrics.learner_detail(learner)
        arm = _arm(required=False)
        return arm_module.merge_learner_detail(detail, arm, learner_id) if arm else detail
    if configured() and (learner_id > ARM_LEARNER_ID_OFFSET or learner_id in set(lms_links().values())):
        arm = _arm(required=True)
        if arm.standalone(learner_id):
            return arm.learner_detail(learner_id)
    raise Http404


@api_view(["GET"])
def learner_detail(request, learner_id: int):
    return Response(_learner_detail(learner_id))


@api_view(["GET"])
def learner_courses(request, learner_id: int):
    return Response({"learner_id": learner_id, "courses": _learner_detail(learner_id)["courses"]})


@api_view(["GET"])
def learner_module_matrix(request, learner_id: int, course_id: int):
    if course_id == ARM_COURSE_ID:
        arm = _arm(required=True)
        if learner_id not in arm.by_learner_id:
            raise NotFound()
        return Response(arm.matrix(learner_id))
    enrollment = get_object_or_404(
        Enrollment.objects.select_related("learner", "course"),
        learner__source_id=learner_id, course__source_id=course_id,
    )
    return Response(metrics.module_matrix(enrollment))


# --- Lecture library ----------------------------------------------------------------------

def _lecture_dates(request) -> dict:
    dates = {}
    for name in ("from", "to"):
        value = request.query_params.get(name)
        if value:
            try:
                date = parse_date(value)
            except ValueError:
                date = None
            if date is None:
                raise ValidationError({name: "Use a valid YYYY-MM-DD date."})
            dates[name] = date
    return dates


def _lecture_order(date, starts_at, pk):
    """The lecture library's ordering: newest date, then newest start, then ID (nulls last)."""
    return (date is None, -date.toordinal() if date else 0,
            starts_at is None, -starts_at.timestamp() if starts_at else 0, str(pk))


@api_view(["GET"])
def lecture_list(request):
    course = _int_param(request, "course")
    dates = _lecture_dates(request)
    size = min(200, max(1, _int_param(request, "page_size") or 100))
    if course == ARM_COURSE_ID:
        arm = _arm(required=True)
        rows = sorted(arm.lectures(dates.get("from"), dates.get("to")),
                      key=lambda r: _lecture_order(r["date"], r["starts_at"], r["id"]))
        count = len(rows)
        page = min(max(1, _int_param(request, "page") or 1), max(1, -(-count // size)))
        return Response({
            "count": count, "page": page, "page_size": size, "pages": max(1, -(-count // size)),
            "cohort_count": len(arm.learners), "timezone": "Africa/Cairo",
            "total_recordings": sum(row['recordings'] for row in rows),
            "playable_recordings": sum(row['playable_recordings'] for row in rows), "updated_at": None,
            "results": rows[(page - 1) * size:page * size],
        })
    queryset = library.catalogue_queryset()
    if course is not None:
        queryset = queryset.filter(course_source_id=course)
    for name, lookup in (("from", "session_date__gte"), ("to", "session_date__lte")):
        if name in dates:
            queryset = queryset.filter(**{lookup: dates[name]})
    learners, emails, enrolled = library.registry()
    canonical = library.canonical_ids()
    course_registry = library.courses()
    queryset = queryset.filter(Q(course_source_id__in=[cid for cid, ids in enrolled.items() if ids]) | Q(course_source_id__isnull=True))
    arm = _arm(required=False) if course is None else None
    arm_rows = arm.lectures(dates.get("from"), dates.get("to")) if arm else []
    if arm_rows:
        # "All modules": completed LMS lectures sit beside library lectures in the library's own order.
        entries = [("library", pk, _lecture_order(day, start, pk))
                   for pk, day, start in queryset.values_list("pk", "session_date", "starts_at")]
        entries += [("lms", row, _lecture_order(row["date"], row["starts_at"], row["id"])) for row in arm_rows]
        entries.sort(key=lambda entry: entry[2])
        count = len(entries)
        page = min(max(1, _int_param(request, "page") or 1), max(1, -(-count // size)))
        window = entries[(page - 1) * size:page * size]
        stored = {row.pk: row for row in queryset.filter(pk__in=[pk for kind, pk, _ in window if kind == "library"])}
        results = [library.lecture_summary(stored[item], emails, enrolled, course_registry, canonical) if kind == "library" else item
                   for kind, item, _ in window]
    else:
        count = queryset.count()
        page = min(max(1, _int_param(request, "page") or 1), max(1, -(-count // size)))
        results = [library.lecture_summary(row, emails, enrolled, course_registry, canonical) for row in queryset[(page - 1) * size:page * size]]
    recording_count, playable_count = library.recording_counts(queryset)
    return Response({
        "count": count, "page": page, "page_size": size, "pages": max(1, -(-count // size)),
        "cohort_count": len(learners), "timezone": "Africa/Cairo",
        "total_recordings": recording_count + sum(row['recordings'] for row in arm_rows),
        "playable_recordings": playable_count + sum(row['playable_recordings'] for row in arm_rows),
        "updated_at": queryset.aggregate(last=Max("updated_at"))["last"],
        "results": results,
    })


@api_view(["GET"])
def lecture_detail(request, lecture_id):
    lecture = library.resolve_lecture(lecture_id)
    if lecture is None:
        if configured():
            detail = _arm(required=True).lecture_detail(str(lecture_id))
            if detail is not None:
                return Response(detail)
        raise Http404
    if lecture.course_source_id is not None and not Enrollment.objects.using('default').filter(course__source_id=lecture.course_source_id).exists():
        from rest_framework.exceptions import NotFound
        raise NotFound()
    return Response(library.detail(lecture))


@api_view(["GET"])
@renderer_classes([JSONRenderer, TranscriptFormatRenderer])
def lecture_resource(request, lecture_id, resource_id):
    lecture = library.resolve_lecture(lecture_id)
    if lecture is None:
        if configured():
            asset = _arm(required=True).lecture_resource(str(lecture_id), resource_id)
            if asset is not None:
                return lms_resource_response(request, lecture_id, asset)
        raise Http404
    asset = get_object_or_404(LectureResource.objects.select_related("lecture"),
                              pk=resource_id, lecture_id=lecture_id)
    if asset.lecture_id != lecture.pk:
        asset = get_object_or_404(LectureResource, lecture=lecture, provider=asset.provider,
                                  kind=asset.kind, source_id=asset.source_id, organizer_id=asset.organizer_id,
                                  meeting_id=asset.meeting_id, drive_id=asset.drive_id)
    if lecture.course_source_id is not None and not Enrollment.objects.using('default').filter(course__source_id=lecture.course_source_id).exists():
        from rest_framework.exceptions import NotFound
        raise NotFound()
    return resource_response(request, asset)


# --- Sync ---------------------------------------------------------------------------------

@api_view(["GET"])
def sync_status(request):
    return Response(metrics.sync_status())
