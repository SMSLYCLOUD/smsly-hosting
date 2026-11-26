"""In-process trailing-slash normalization for /api/* and /auth/* routes.

Prevents external 301 APPEND_SLASH redirect loops when a browser or proxy
has cached a stale 308 trailing-slash-strip redirect (e.g. from Next.js
fallback routing) or strips trailing slashes on API calls. Placed AFTER
SecurityMiddleware so HMAC path verification sees the exact wire path.
"""
from django.urls import is_valid_path


class ApiTrailingSlashNormalizeMiddleware:
    """Normalize slashless /api/* and /auth/* paths in-process before routing."""

    _PREFIXES = ("/api/", "/auth/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path_info = request.path_info or ""
        normalized = False
        if (
            path_info.startswith(self._PREFIXES)
            and not path_info.endswith("/")
        ):
            urlconf = getattr(request, "urlconf", None)
            if not is_valid_path(path_info, urlconf) and is_valid_path(f"{path_info}/", urlconf):
                script_prefix = (
                    request.path[: len(request.path) - len(path_info)]
                    if request.path.endswith(path_info)
                    else ""
                )
                request.path_info = f"{path_info}/"
                request.path = f"{script_prefix}{request.path_info}"
                normalized = True

        response = self.get_response(request)
        if normalized:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            # Clear poisoned 308/301 HTTP disk cache entries in the browser
            # without touching cookies, session, or localStorage.
            if (
                request.is_secure()
                or request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower() == "https"
            ):
                response.headers.setdefault("Clear-Site-Data", '"cache"')
        return response
