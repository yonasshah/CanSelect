from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Vote, Activity, Comment

@receiver(post_save, sender=Vote)
def create_vote_cast_activity(sender, instance, **kwargs):
    """Log when a vote is cast or changed."""
    Activity.objects.create(
        actor=instance.voter,
        action_type=Activity.VOTE_CAST,
        details=f"voted '{instance.get_value_display()}' for",
        target_applicant=instance.applicant
    )
    
@receiver(post_save, sender=Comment)
def create_comment_added_activity(sender, instance, created, **kwargs):
    """Log when a new comment is posted."""
    if created: # Only run when a new comment is created
        Activity.objects.create(
            actor=instance.author,
            action_type=Activity.COMMENT_ADDED,
            details=f"commented on",
            target_applicant=instance.applicant
        )