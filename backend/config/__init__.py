"""  Init   module."""
from .celery import app as celery_app

# Django Python 3.14 compatibility patch
import sys
try:
    from django.template.context import BaseContext
    import copy

    def _safe_copy(self):
        duplicate = self.__class__.__new__(self.__class__)
        for k, v in self.__dict__.items():
            setattr(duplicate, k, copy.copy(v))
        duplicate.dicts = self.dicts[:]
        return duplicate

    BaseContext.__copy__ = _safe_copy
except Exception:
    pass

__all__ = ('celery_app',)
