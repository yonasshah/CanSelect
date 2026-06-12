from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.contrib.auth import views as auth_views
from applicants.media_serve import serve_media_with_range


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("applicants.urls")),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    re_path(r'^media/(?P<path>.*)$', serve_media_with_range, {'document_root': settings.MEDIA_ROOT}),
]