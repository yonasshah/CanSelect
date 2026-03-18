def unread_notification_count(request):
    if request.user.is_authenticated:
        from django.db.models import Case, When, Value, IntegerField
        recent_notifications = (
            request.user.notifications
            .annotate(
                has_deadline=Case(
                    When(deadline__isnull=False, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            )
            .order_by('has_deadline', 'deadline', '-created_at')[:8]
        )
        return {
            'unread_notification_count': request.user.notifications.filter(is_read=False).count(),
            'recent_notifications': recent_notifications,
        }
    return {'unread_notification_count': 0, 'recent_notifications': []}