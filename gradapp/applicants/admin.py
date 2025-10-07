from django.contrib import admin

from .models import Applicant, DataSet, Batch

@admin.register(DataSet)
class DataSetAdmin(admin.ModelAdmin):
    list_display = ("DisplayName", "ProgramId", "PublicView", "Active")

@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ("DisplayName", "DataSet", "Active", "PublicView", "VoteExpire")

@admin.register(Applicant)
class ApplicantAdmin(admin.ModelAdmin):
    list_display = ("first_name", "last_name", "email", "age", "gender", "ethnicity", "created_at")
