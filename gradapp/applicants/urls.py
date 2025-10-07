from django.urls import path
from . import views

urlpatterns = [
    path("", views.applicant_list, name="applicant_list"),
    path("login/", views.email_login, name="login"),
    path("applicant/<int:pk>/", views.applicant_detail, name="applicant_detail"),
    path("applicant/new/", views.applicant_create, name="applicant_create"),
    path("applicant/<int:pk>/vote/<str:value>/", views.vote, name="vote"),
    path("applicant/<int:pk>/add-files/", views.add_files, name="add_files"),
    path("applicant/<int:pk>/edit/", views.applicant_edit, name="applicant_edit"),
    path("datasets/", views.dataset_list, name="dataset_list"),
    path("datasets/new/", views.dataset_create, name="dataset_create"),
    path("datasets/<int:pk>/", views.dataset_detail, name="dataset_detail"),
    path("datasets/<int:pk>/edit/", views.dataset_edit, name="dataset_edit"),
    path("batches/", views.batch_list, name="batch_list"),
    path("batches/new/", views.batch_create, name="batch_create"),
    path("batches/<int:pk>/", views.batch_detail, name="batch_detail"),
    path("batches/<int:pk>/edit/", views.batch_edit, name="batch_edit"),
]
