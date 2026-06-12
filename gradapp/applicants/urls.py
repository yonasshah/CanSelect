from django.urls import path
from . import views
from .views import toggle_applicant_flag

urlpatterns = [

    # ── Core ──────────────────────────────────────────────────────────
    path("", views.dashboard, name="dashboard"),
    path("login/", views.email_login, name="login"),
    path("help/", views.help_page, name="help"),
    path("toggle-committee-mode/", views.toggle_committee_mode, name="toggle_committee_mode"),

    # ── Applicants ────────────────────────────────────────────────────
    path("applicant/", views.applicant_list, name="applicant_list"),
    path("applicant/new/", views.applicant_create, name="applicant_create"),
    path("applicant/batch-action/", views.batch_action, name="batch_action"),
    path("applicant/compare/", views.compare_applicants, name="compare_applicants"),
    path("applicant/export/", views.export_applicants_csv, name="export_applicants_csv"),
    path("applicant/<int:pk>/", views.applicant_detail, name="applicant_detail"),
    path("applicant/<int:pk>/edit/", views.applicant_edit, name="applicant_edit"),
    path("applicant/<int:pk>/vote/<str:value>/", views.vote, name="vote"),
    path("applicant/<int:pk>/add-files/", views.add_files, name="add_files"),
    path("applicant/<int:pk>/update-status/", views.update_status, name="update_status"),
    path("applicant/<int:pk>/update-score/", views.update_score, name="update_score"),
    path("applicant/<int:pk>/profile/", views.applicant_profile_partial, name="applicant_profile_partial"),
    path("applicant/<int:pk>/toggle-flag/", toggle_applicant_flag, name="toggle_applicant_flag"),

    # ── Datasets ──────────────────────────────────────────────────────
    path("datasets/", views.dataset_list, name="dataset_list"),
    path("datasets/new/", views.dataset_create, name="dataset_create"),
    path("datasets/<int:pk>/", views.dataset_detail, name="dataset_detail"),
    path("datasets/<int:pk>/edit/", views.dataset_edit, name="dataset_edit"),
    path("datasets/<int:pk>/archive/", views.dataset_archive, name="dataset_archive"),
    path("datasets/<int:pk>/decisions/", views.dataset_decisions, name="dataset_decisions"),
    path("datasets/<int:pk>/decisions/action/", views.dataset_decisions_action, name="dataset_decisions_action"),
    path("datasets/<int:pk>/decisions/export/", views.export_decisions_csv, name="export_decisions_csv"),
    path("datasets/<int:pk>/decisions/<str:section>/", views.dataset_decisions_section, name="dataset_decisions_section"),

    # ── Batches ───────────────────────────────────────────────────────
    path("batches/", views.batch_list, name="batch_list"),
    path("batches/new/", views.batch_create, name="batch_create"),
    path("batches/bulk-action/", views.batch_bulk_action, name="batch_bulk_action"),
    path("batches/<int:pk>/", views.batch_detail, name="batch_detail"),
    path("batches/<int:pk>/edit/", views.batch_edit, name="batch_edit"),
    path("batches/<int:pk>/assign/", views.batch_assign_reviewers, name="batch_assign_reviewers"),

    # ── Queue ─────────────────────────────────────────────────────────
    path("queue/", views.applicant_queue, name="applicant_queue"),

    # ── Committee ─────────────────────────────────────────────────────
    path("committee/dashboard/", views.committee_dashboard, name="committee_dashboard"),
    path("committee/reviews/", views.my_reviews, name="my_reviews"),
    path("committee/activity/", views.my_activity, name="my_activity"),

    # ── Notifications ─────────────────────────────────────────────────
    path("notifications/", views.notification_list, name="notification_list"),
    path("notifications/send/", views.send_notification, name="send_notification"),
    path("notifications/mark-all-read/", views.mark_all_notifications_read, name="mark_all_notifications_read"),
    path("notifications/<int:pk>/", views.notification_detail, name="notification_detail"),
    path("notifications/<int:pk>/read/", views.mark_notification_read, name="mark_notification_read"),

    # ── Admin Tools ───────────────────────────────────────────────────
    path("bulk-upload/", views.bulk_upload_applicants, name="bulk_upload"),
    path("candidate-info-upload/", views.candidate_info_upload, name="candidate_info_upload"),
    path("activity-feed/", views.activity_feed, name="activity_feed"),
    path("panels/", views.manage_panels, name="manage_panels"),
    path("search/", views.global_search, name="global_search"),
    path("members/update-type/", views.update_member_type, name="update_member_type"),
    path("members/toggle-reviewer/", views.toggle_reviewer_status, name="toggle_reviewer_status"),
]