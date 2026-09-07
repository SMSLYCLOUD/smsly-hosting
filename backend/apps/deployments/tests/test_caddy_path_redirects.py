from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.deployments.services.caddy_manager.config_generation import (
    _path_redirect_site_lines,
    _service_path_redirect_rules,
)


class CaddyPathRedirectTests(SimpleTestCase):
    def test_path_only_redirect_applies_to_site(self):
        service = SimpleNamespace(
            path_redirects=[{'path': '/account', 'target': 'accounts.example.com/login'}]
        )

        rules = _service_path_redirect_rules(service)
        lines = _path_redirect_site_lines(rules, site_domain='app.example.com')

        self.assertEqual(rules, [('', '/account', 'accounts.example.com', '/login')])
        self.assertIn('        redir https://accounts.example.com/login 301', lines)

    def test_domain_path_redirect_is_scoped_to_source_domain(self):
        service = SimpleNamespace(
            path_redirects=[
                {'path': 'app.example.com/account', 'target': 'accounts.example.com/login'},
            ]
        )

        rules = _service_path_redirect_rules(service)

        self.assertTrue(_path_redirect_site_lines(rules, site_domain='app.example.com'))
        self.assertEqual(
            _path_redirect_site_lines(rules, site_domain='other.example.com'), []
        )
