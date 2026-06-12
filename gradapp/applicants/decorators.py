from functools import wraps
from django.shortcuts import render, redirect
from django.contrib import messages


def admin_required(function):
    """
    Requires the user to be an ADMIN and NOT in committee mode.
    If an admin-reviewer has switched to committee mode and hits an
    admin-only URL directly, they are redirected to their committee
    dashboard with an explanatory message.
    """
    @wraps(function)
    def wrap(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return render(request, '403.html', status=403)
        if not hasattr(request.user, 'profile'):
            return render(request, '403.html', status=403)

        # Block non-admins outright
        if request.user.profile.role != 'ADMIN':
            return render(request, '403.html', status=403)

        # Block admins who are currently in committee mode
        if request.session.get('committee_mode'):
            messages.warning(
                request,
                "You are currently in committee member mode. "
                "Switch back to admin view to access this page."
            )
            return redirect('committee_dashboard')

        return function(request, *args, **kwargs)
    return wrap


def committee_access_required(function):
    """
    Allows access to committee-member views for:
      - Users with role COMMITTEE_MEMBER
      - Admin users who are in committee mode (is_reviewer=True + session flag)
    Redirects everyone else to 403.
    """
    @wraps(function)
    def wrap(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return render(request, '403.html', status=403)
        if not hasattr(request.user, 'profile'):
            return render(request, '403.html', status=403)

        profile = request.user.profile

        # Regular committee members always pass
        if profile.role == 'ADMIN' and request.session.get('committee_mode'):
            # Only admins marked as reviewers can be in committee mode
            if not profile.is_reviewer:
                # Shouldn't happen in normal flow, but be safe
                request.session.pop('committee_mode', None)
                return render(request, '403.html', status=403)
            return function(request, *args, **kwargs)

        if profile.role == profile.Role.COMMITTEE_MEMBER:
            return function(request, *args, **kwargs)

        return render(request, '403.html', status=403)
    return wrap