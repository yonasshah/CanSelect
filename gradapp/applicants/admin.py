from django.contrib import admin

from .models import Applicant, DataSet, Batch, Profile

@admin.register(DataSet)
class DataSetAdmin(admin.ModelAdmin):
    list_display = ("DisplayName", "ProgramId", "PublicView", "Active")

@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ("DisplayName", "DataSet", "Active", "PublicView", "VoteExpire")

@admin.register(Applicant)
class ApplicantAdmin(admin.ModelAdmin):
    list_display = ("first_name", "last_name", "email", "age", "gender", "created_at")
    
@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role')
    list_filter = ('role',)

