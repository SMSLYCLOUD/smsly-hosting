import importlib
import inspect

from django.test import SimpleTestCase

import apps.deployments.views as deployments_views
from apps.deployments.utils import validate_and_sanitize_path


class Finding96ValidatePathAliasRemovedTests(SimpleTestCase):

    def test_underscore_alias_not_importable_from_views(self):
        self.assertFalse(
            hasattr(deployments_views, '_validate_and_sanitize_path'),
            'The _validate_and_sanitize_path alias should be removed '
            'from apps.deployments.views; use validate_and_sanitize_path.',
        )

    def test_canonical_name_imported_into_views(self):
        self.assertTrue(hasattr(deployments_views, 'validate_and_sanitize_path'))
        self.assertIs(
            deployments_views.validate_and_sanitize_path,
            validate_and_sanitize_path,
        )

    def test_views_module_source_has_no_alias(self):
        src = inspect.getsource(deployments_views)
        self.assertNotIn('_validate_and_sanitize_path', src)

    def test_views_module_does_not_import_with_as_alias(self):
        importlib.reload(deployments_views)
        src = inspect.getsource(deployments_views)
        self.assertNotIn(
            'validate_and_sanitize_path as _validate_and_sanitize_path',
            src,
        )
