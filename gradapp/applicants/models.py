from django.db import models
from django.contrib.auth.models import User
from django.contrib.auth.models import AbstractUser

class Applicant(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField()
    age = models.PositiveIntegerField()
    gender = models.CharField(max_length=50)
    ethnicity = models.CharField(max_length=100, blank=True)
    round = models.CharField(max_length=100, blank=True, null=True)
    dataset = models.ForeignKey("DataSet", on_delete=models.SET_NULL, null=True, blank=True, related_name="applicants")
    description = models.TextField(blank=True, max_length=100000)
    street = models.TextField(blank=True, max_length=100000)
    created_at = models.DateTimeField(auto_now_add=True)

    def votes_summary(self):
        counts = {"accept": 0, "deny": 0, "waitlist": 0}
        for v in self.votes.all():
            if v.value == 1:
                counts["accept"] += 1
            elif v.value == -1:
                counts["deny"] += 1
            elif v.value == 0:
                counts["waitlist"] += 1
        return counts

    def __str__(self):
        return f"{self.last_name}, {self.first_name}"

class ApplicantFile(models.Model):
    applicant = models.ForeignKey(Applicant, related_name="files", on_delete=models.CASCADE)
    file = models.FileField(upload_to="applicants/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

class Vote(models.Model):
    VOTE_CHOICES = (
        (1, "Accept"),
        (-1, "Deny"),
        (0, "Waitlist"),
    )
    applicant = models.ForeignKey(Applicant, related_name="votes", on_delete=models.CASCADE)
    voter = models.ForeignKey(User, on_delete=models.CASCADE)
    value = models.SmallIntegerField(choices=VOTE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("applicant", "voter")

class DataSet(models.Model):
    DisplayName = models.CharField(max_length=255)
    Description = models.TextField(blank=True, null=True)
    AdminNotes = models.TextField(blank=True, null=True)
    PublicView = models.BooleanField(default=False)
    ProgramId = models.IntegerField(blank=True, null=True)
    Active = models.BooleanField(default=True)
    IsLive = models.BooleanField(default=False)   # 
    CreatedAt = models.DateTimeField(auto_now_add=True)
    UpdatedAt = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["DisplayName"]

    def __str__(self):
        return self.DisplayName

class Batch(models.Model):
    DataSet = models.ForeignKey("DataSet", related_name="batches", on_delete=models.CASCADE)
    DisplayName = models.CharField(max_length=255)
    Description = models.TextField(blank=True, null=True)
    VoteExpire = models.DateTimeField(blank=True, null=True)
    AdminNotes = models.TextField(blank=True, null=True)
    PublicView = models.BooleanField(default=False)
    Active = models.BooleanField(default=True)
    HighlightBefore = models.DateTimeField(blank=True, null=True)
    RoundId = models.IntegerField(blank=True, null=True)
    CreatedAt = models.DateTimeField(auto_now_add=True)
    UpdatedAt = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["DisplayName"]

    def __str__(self):
        return f"{self.DisplayName} (Dataset: {self.DataSet.DisplayName})"