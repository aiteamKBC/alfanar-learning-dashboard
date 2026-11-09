from django.contrib import admin
from django.urls import include, path

from analytics.api import views

api = [
    path("auth/session", views.auth_session),
    path("auth/login", views.auth_login),
    path("auth/logout", views.auth_logout),
    path("filters", views.filters),
    path("dashboard/summary", views.dashboard_summary),
    path("dashboard/charts", views.dashboard_charts),
    path("learners", views.learner_list),
    path("lectures", views.lecture_list),
    path("lectures/<uuid:lecture_id>", views.lecture_detail),
    path("lectures/<uuid:lecture_id>/resources/<int:resource_id>", views.lecture_resource),
    path("learners/<int:learner_id>", views.learner_detail),
    path("learners/<int:learner_id>/courses", views.learner_courses),
    path("learners/<int:learner_id>/courses/<int:course_id>/matrix", views.learner_module_matrix),
    path("sync/status", views.sync_status),
]

urlpatterns = [
    # Django admin is used only for managing dashboard user accounts.
    path("admin/", admin.site.urls),
    path("api/", include(api)),
]
