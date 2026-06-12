from django.db.models.signals import post_save
from django.contrib.auth.signals import user_logged_in
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
    if created:
        Activity.objects.create(
            actor=instance.author,
            action_type=Activity.COMMENT_ADDED,
            details=f"commented on",
            target_applicant=instance.applicant
        )


@receiver(user_logged_in)
def capture_previous_login(sender, request, user, **kwargs):
    """
    Before Django overwrites last_login, save the current value to
    previous_login on the user's profile. This lets the committee
    dashboard detect batches uploaded since the user's last session.
    """
    try:
        profile = user.profile
        # last_login at this point is still the *previous* login —
        # Django updates it after this signal fires
        profile.previous_login = user.last_login
        profile.save(update_fields=['previous_login'])
    except Exception:
        pass