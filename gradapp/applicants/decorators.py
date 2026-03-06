from functools import wraps
from django.shortcuts import render

def admin_required(function):
    @wraps(function)
    def wrap(request, *args, **kwargs):
        if not request.user.is_authenticated or not hasattr(request.user, 'profile') or request.user.profile.role != 'ADMIN':
            return render(request, '403.html', status=403)
        return function(request, *args, **kwargs)
    return wrap