from django.utils.cache import add_never_cache_headers


class PrivateInspectorResponsesMiddleware:
    """Apply the same private policy after Django has converted view exceptions."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        match = request.resolver_match
        if (
            match is not None
            and match.app_name == "inspector"
            and match.url_name not in {"docs-offline", "docs-offline-root"}
        ):
            add_never_cache_headers(response)
        return response
