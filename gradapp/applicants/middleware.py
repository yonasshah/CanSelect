from django.shortcuts import redirect
from django.urls import reverse

EXEMPT_PATH_NAMES = {'confidentiality_agreement', 'email_login', 'logout'}

class ConfidentialityAgreementMiddleware:
    """
    Redirects authenticated users who haven't signed the confidentiality
    agreement to the agreement page, before any other view runs.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            try:
                profile = user.profile
            except Exception:
                profile = None

            if profile is not None and not profile.confidentiality_acknowledged_at:
                match = request.resolver_match
                url_name = match.url_name if match else None
                is_static = request.path.startswith('/static/') or request.path.startswith('/media/')
                if url_name not in EXEMPT_PATH_NAMES and not is_static:
                    agreement_url = reverse('confidentiality_agreement')
                    if request.path != agreement_url:
                        return redirect(f'{agreement_url}?next={request.path}')

        return self.get_response(request)