"""Read-only JSON API. All data endpoints require an authenticated session."""

from django.contrib.auth import authenticate, login, logout
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.http import Http404
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from django.utils.dateparse import parse_date
from django.db.models import Max, Q

from .. import metrics
from .permissions import has_dashboard_access
from ..models import ActivityKind, Course, Enrollment, Learner
from ..lectures import library
from ..lectures.media import resource_response
from ..models import LectureResource


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
    return Response({
        "courses": [{"course_id": c.source_id, "title": c.display_title}
                    for c in sorted(Course.objects.all(), key=lambda c: c.display_order_key)],
        "activity_types": [{"value": v, "label": l} for v, l in ActivityKind.choices],
        "engagement": [{"value": "active", "label": "Active in LMS"},
                       {"value": "inactive", "label": "No recorded LMS activity"}],
    })


@api_view(["GET"])
def dashboard_summary(request):
    return Response(metrics.summary(course_id=_int_param(request, "course"),
                                    engagement=_choice_param(request, "engagement", ENGAGEMENT)))


@api_view(["GET"])
def dashboard_charts(request):
    return Response(metrics.charts(course_id=_int_param(request, "course"),
                                   engagement=_choice_param(request, "engagement", ENGAGEMENT),
                                   activity_type=_choice_param(request, "activity_type", set(ActivityKind.values))))


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
    rows = metrics.learner_directory(
        search=(request.query_params.get("search") or "").strip() or None,
        course_id=_int_param(request, "course"),
        engagement=_choice_param(request, "engagement", ENGAGEMENT),
        ordering=ordering,
    )
    page = max(1, _int_param(request, "page") or 1)
    size = min(100, max(1, _int_param(request, "page_size") or 20))
    total = len(rows)
    return Response({
        "count": total, "page": page, "page_size": size, "pages": max(1, -(-total // size)),
        "ordering": ordering, "results": rows[(page - 1) * size: page * size],
    })


@api_view(["GET"])
def learner_detail(request, learner_id: int):
    learner = get_object_or_404(Learner, source_id=learner_id)
    return Response(metrics.learner_detail(learner))


@api_view(["GET"])
def learner_courses(request, learner_id: int):
    learner = get_object_or_404(Learner, source_id=learner_id)
    return Response({"learner_id": learner_id, "courses": metrics.learner_detail(learner)["courses"]})


@api_view(["GET"])
def learner_module_matrix(request, learner_id: int, course_id: int):
    enrollment = get_object_or_404(
        Enrollment.objects.select_related("learner", "course"),
        learner__source_id=learner_id, course__source_id=course_id,
    )
    return Response(metrics.module_matrix(enrollment))


# --- Lecture library ----------------------------------------------------------------------

@api_view(["GET"])
def lecture_list(request):
    queryset = library.lecture_queryset()
    course = _int_param(request, "course")
    if course is not None:
        queryset = queryset.filter(course__source_id=course)
    for name, lookup in (("from", "session_date__gte"), ("to", "session_date__lte")):
        value = request.query_params.get(name)
        if value:
            try:
                date = parse_date(value)
            except ValueError:
                date = None
            if date is None:
                raise ValidationError({name: "Use a valid YYYY-MM-DD date."})
            queryset = queryset.filter(**{lookup: date})
    learners, emails, enrolled = library.registry()
    queryset = queryset.filter(Q(course_id__in=[cid for cid, ids in enrolled.items() if ids]) | Q(course__isnull=True))
    count = queryset.count()
    size = min(200, max(1, _int_param(request, "page_size") or 100))
    page = min(max(1, _int_param(request, "page") or 1), max(1, -(-count // size)))
    return Response({
        "count": count, "page": page, "page_size": size, "pages": max(1, -(-count // size)),
        "cohort_count": len(learners), "timezone": "Africa/Cairo",
        "total_recordings": LectureResource.objects.filter(lecture__in=queryset, kind="recording").count(),
        "playable_recordings": LectureResource.objects.filter(lecture__in=queryset, kind="recording", readable=True).count(),
        "updated_at": queryset.aggregate(last=Max("updated_at"))["last"],
        "results": [library.lecture_summary(row, emails, enrolled) for row in queryset[(page - 1) * size:page * size]],
    })


@api_view(["GET"])
def lecture_detail(request, lecture_id):
    lecture = library.resolve_lecture(lecture_id)
    if lecture is None:
        raise Http404
    if lecture.course_id is not None and not Enrollment.objects.filter(course_id=lecture.course_id).exists():
        from rest_framework.exceptions import NotFound
        raise NotFound()
    return Response(library.detail(lecture))


@api_view(["GET"])
def lecture_resource(request, lecture_id, resource_id):
    lecture = library.resolve_lecture(lecture_id)
    if lecture is None:
        raise Http404
    asset = get_object_or_404(LectureResource.objects.select_related("lecture"),
                              pk=resource_id, lecture_id=lecture_id)
    if asset.lecture_id != lecture.pk:
        asset = get_object_or_404(LectureResource, lecture=lecture, provider=asset.provider,
                                  kind=asset.kind, source_id=asset.source_id, organizer_id=asset.organizer_id,
                                  meeting_id=asset.meeting_id, drive_id=asset.drive_id)
    if lecture.course_id is not None and not Enrollment.objects.filter(course_id=lecture.course_id).exists():
        from rest_framework.exceptions import NotFound
        raise NotFound()
    return resource_response(request, asset)


# --- Sync ---------------------------------------------------------------------------------

@api_view(["GET"])
def sync_status(request):
    return Response(metrics.sync_status())
