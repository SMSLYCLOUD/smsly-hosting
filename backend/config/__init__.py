"""  Init   module."""
# Django Python 3.14 compatibility patch

from .celery import app as celery_app

try:
    import copy

    from django.template.context import BaseContext

    def _safe_copy(self):
        duplicate = self.__class__.__new__(self.__class__)
        for k, v in self.__dict__.items():
            setattr(duplicate, k, copy.copy(v))
        duplicate.dicts = self.dicts[:]
        return duplicate

    BaseContext.__copy__ = _safe_copy  # type: ignore[method-assign]
except Exception:
    pass

__all__ = ('celery_app',)
