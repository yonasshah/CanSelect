from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

def admin_required(function):
    def wrap(request, *args, **kwargs):
        if not request.user.profile or request.user.profile.role != 'ADMIN':
            raise PermissionDenied
        return function(request, *args, **kwargs)
    return wrap