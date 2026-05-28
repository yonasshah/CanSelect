import os
from django.db import models
from django.contrib.auth.models import User
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

class Profile(models.Model):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Admin'
        COMMITTEE_MEMBER = 'COMMITTEE_MEMBER', 'Committee Member'
 
    class ReviewGroup(models.TextChoices):
        UNASSIGNED = '', 'Unassigned'
        GROUP_A = 'A', 'Group A'
        GROUP_B = 'B', 'Group B'
        GROUP_C = 'C', 'Group C'
 
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(
        max_length=50,
        choices=Role.choices,
        default=Role.COMMITTEE_MEMBER
    )
    review_group = models.CharField(
        max_length=1,
        choices=ReviewGroup.choices,
        default=ReviewGroup.UNASSIGNED,
        blank=True,
    )
 
    def __str__(self):
        group_label = f" [{self.get_review_group_display()}]" if self.review_group else ""
        return f"{self.user.username} - {self.get_role_display()}{group_label}"

class Applicant(models.Model):   
    class Status(models.TextChoices):
        NEW = 'NEW', 'New'
        UNDER_REVIEW = 'REVIEW', 'Under Review'
        INTERVIEW = 'INTERVIEW', 'Interview'
        DECIDED = 'DECIDED', 'Decision Made'
        
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(blank=True, null=True)
    age = models.PositiveIntegerField()
    gender = models.CharField(max_length=50)
    ethnicity = models.CharField(max_length=100, blank=True)
    round = models.ForeignKey("Batch", on_delete=models.SET_NULL, null=True, blank=True)
    dataset = models.ForeignKey("DataSet", on_delete=models.SET_NULL, null=True, blank=True, related_name="applicants")
    description = models.TextField(blank=True, max_length=100000)
    street = models.TextField(blank=True, max_length=100000)
    created_at = models.DateTimeField(auto_now_add=True)
    profile_picture = models.ImageField(upload_to='applicant_profiles/', blank=True, null=True)
    external_id = models.CharField(max_length=100, blank=True, null=True)
    source_folder = models.CharField(max_length=255, blank=True, default='', help_text='Top-level folder name from bulk upload')
    flagged_by = models.ManyToManyField(User, blank=True, related_name='flagged_applicants')
    # ── Candidate Info (populated via Excel upload, matched by external_id) ──
    total_ai = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True, verbose_name='Total AI')
    total_nc = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True, verbose_name='Total NC')
    first_gen = models.BooleanField(default=False, verbose_name='First Generation')
    re_applicant = models.BooleanField(default=False, verbose_name='Re-Applicant')
    pb_to_dmd = models.BooleanField(default=False, verbose_name='PB to DMD')
    z_score = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True, verbose_name='Z-Score')
    former_post_bacc = models.BooleanField(default=False, verbose_name='Former Post Bacc')
    three_plus_four = models.BooleanField(default=False, verbose_name='3+4')
    candidate_info_imported = models.BooleanField(default=False, help_text='Whether candidate info was imported from Excel')

    
    status = models.CharField(
        max_length=50,
        choices=Status.choices,
        default=Status.NEW,
        db_index=True  
    )
    
    assigned_reviewers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="assigned_applicants",
        blank=True,
        limit_choices_to={'profile__role': Profile.Role.COMMITTEE_MEMBER}
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
    
    assigned_reviewers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="assigned_batches",
        blank=True,
        limit_choices_to={'profile__role': Profile.Role.COMMITTEE_MEMBER}
    )
    review_group = models.CharField(
        max_length=1,
        choices=[('', 'Unassigned'), ('A', 'Group A'), ('B', 'Group B'), ('C', 'Group C')],
        blank=True,
        default='',
    )

    class Meta:
        ordering = ["DisplayName"]

    def __str__(self):
        return f"{self.DisplayName} (Dataset: {self.DataSet.DisplayName})"
    

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
    FLAG_ADDED = 'flag_added'

    ACTION_CHOICES = [
        (VOTE_CAST, 'Vote Cast'),
        (COMMENT_ADDED, 'Comment Added'),
        (FLAG_ADDED, 'Flag Added'),
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
    
class Notification(models.Model):
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='sent_notifications')
    subject = models.CharField(max_length=255)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(blank=True, null=True)
    deadline = models.DateTimeField(blank=True, null=True)
 
    class Meta:
        ordering = ['-created_at']
 
    def __str__(self):
        return f"To {self.recipient.username}: {self.subject}"
    
    
class NotificationAttachment(models.Model):
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to='notification_attachments/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Attachment for: {self.notification.subject}"

    @property
    def filename(self):
        import os
        return os.path.basename(self.file.name)
