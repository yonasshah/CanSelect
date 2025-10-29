import os
from django.db import models
from django.contrib.auth.models import User
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

class Applicant(models.Model):   
    class Status(models.TextChoices):
        NEW = 'NEW', 'New'
        UNDER_REVIEW = 'REVIEW', 'Under Review'
        INTERVIEW = 'INTERVIEW', 'Interview'
        DECIDED = 'DECIDED', 'Decision Made'
        
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField()
    age = models.PositiveIntegerField()
    gender = models.CharField(max_length=50)
    ethnicity = models.CharField(max_length=100, blank=True)
    round = models.ForeignKey("Batch", on_delete=models.SET_NULL, null=True, blank=True)
    dataset = models.ForeignKey("DataSet", on_delete=models.SET_NULL, null=True, blank=True, related_name="applicants")
    description = models.TextField(blank=True, max_length=100000)
    street = models.TextField(blank=True, max_length=100000)
    created_at = models.DateTimeField(auto_now_add=True)
    
    status = models.CharField(
        max_length=50,
        choices=Status.choices,
        default=Status.NEW,
        db_index=True  # Good for filtering
    )

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

    @property
    def is_video(self):
        # You can add more video extensions to this list if you need to
        video_extensions = ['.mp4', '.mov', '.webm', '.avi', '.mkv', '.wmv']
        try:
            # os.path.splitext splits the filename into its name and extension
            name, extension = os.path.splitext(self.file.name)
            return extension.lower() in video_extensions
        except:
            # If there's any error, safely assume it's not a video
            return False

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
    
class Score(models.Model):
    SCORE_CHOICES = [
        (1, '1 - Poor'),
        (2, '2 - Fair'),
        (3, '3 - Good'),
        (4, '4 - Very Good'),
        (5, '5 - Excellent'),
    ]
    applicant = models.ForeignKey(Applicant, related_name="scores", on_delete=models.CASCADE)
    voter = models.ForeignKey(User, on_delete=models.CASCADE)
    research_score = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, null=True, blank=True)
    statement_score = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, null=True, blank=True)
    overall_score = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('applicant', 'voter') # Each user can only score an applicant once

    def __str__(self):
        return f"Score by {self.voter.username} for {self.applicant}"

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
    
class Profile(models.Model):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Admin'
        COMMITTEE_MEMBER = 'COMMITTEE_MEMBER', 'Committee Member'

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(
        max_length=50,
        choices=Role.choices,
        default=Role.COMMITTEE_MEMBER
    )

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"

# This signal automatically creates a Profile when a User is created
@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.profile.save()
    
class Activity(models.Model):
    VOTE_CAST = 'vote_cast'
    COMMENT_ADDED = 'comment_added'

    ACTION_CHOICES = [
        (VOTE_CAST, 'Vote Cast'),
        (COMMENT_ADDED, 'Comment Added'),
    ]
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action_type = models.CharField(max_length=50, choices=ACTION_CHOICES)
    details = models.TextField()
    target_applicant = models.ForeignKey(Applicant, on_delete=models.CASCADE, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = "Activities"

    def __str__(self):
        actor_name = self.actor.username if self.actor else 'System'
        return f"{actor_name} - {self.get_action_type_display()}"
    
class Comment(models.Model):
    applicant = models.ForeignKey(Applicant, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Comment by {self.author.username} on {self.applicant}"