"""Jules auto‑fix package.

Provides a robust Celery task that reacts to deployment failures, asks the
Google‑Jules AI to generate a fix, applies the fix in a temporary branch and
opens a Pull Request on GitHub.
"""

from .jules_fix import jules_fix_deployment_failure  # noqa: F401
