from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.deployments.services.caddy_manager.config_generation import (
    _alias_scoped_redirects,
    _build_host_alias_block,
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

    def test_alias_host_source_generates_inside_alias_block(self):
        rules = [('accounts.example.com', '/login', 'app.example.com', '/')]
        block = _build_host_alias_block(
            'accounts.example.com', '', 'http://127.0.0.1:8000',
            'app.example.com', path_redirect_rules=rules,
        )
        self.assertIn('redir https://app.example.com/ 301', block)
        # Redirect handles precede the proxy handle.
        self.assertLess(block.index('redir '), block.index('reverse_proxy'))

    def test_plain_path_rules_do_not_leak_into_alias_block(self):
        rules = [
            ('', '/login', 'app.example.com', '/'),
            ('accounts.example.com', '/billing', 'app.example.com', '/'),
            ('evil.example.com', '/x', 'app.example.com', '/'),
        ]
        scoped = _alias_scoped_redirects(rules, 'accounts.example.com')
        self.assertEqual(
            scoped, [('accounts.example.com', '/billing', 'app.example.com', '/')])
        block = _build_host_alias_block(
            'accounts.example.com', '', 'http://127.0.0.1:8000',
            'app.example.com', path_redirect_rules=scoped,
        )
        self.assertIn('billing', block)
        self.assertNotIn('/login', block.split('reverse_proxy')[0])

    def test_foreign_source_generates_nowhere(self):
        rules = [('evil.example.com', '/login', 'app.example.com', '/')]
        self.assertEqual(
            _path_redirect_site_lines(rules, site_domain='app.example.com'), []
        )
        block = _build_host_alias_block(
            'accounts.example.com', '', 'http://127.0.0.1:8000',
            'app.example.com', path_redirect_rules=rules,
        )
        self.assertNotIn('redir ', block)

    def test_alias_block_without_rules_unchanged(self):
        block = _build_host_alias_block(
            'accounts.example.com', '/login', 'http://127.0.0.1:8000',
            'app.example.com',
        )
        self.assertNotIn('redir ', block)
        self.assertIn('rewrite * /login', block)

    def test_root_source_redirects_only_exact_root(self):
        service = SimpleNamespace(
            path_redirects=[
                {'path': 'accounts.example.com/', 'target': 'accounts.example.com/login'},
            ]
        )
        rules = _service_path_redirect_rules(service)
        self.assertEqual(
            rules, [('accounts.example.com', '/', 'accounts.example.com', '/login')])
        lines = _path_redirect_site_lines(rules, site_domain='accounts.example.com')
        text = "\n".join(lines)
        self.assertIn('@path_redir_0_root path /', text)
        self.assertIn('redir https://accounts.example.com/login 301', text)
        # No sub-path handler: /anything-else must keep proxying.
        self.assertNotIn('handle_path', text)
        # Other sites unaffected; unscoped root rules never generate.
        self.assertEqual(
            _path_redirect_site_lines(rules, site_domain='other.example.com'), [])
        self.assertEqual(
            _path_redirect_site_lines([('', '/', 'x.example.com', '/')], site_domain='x.example.com'), [])

    def test_slashless_bare_source_still_skipped(self):
        # Conservative: only the serializer-normalized `domain/` form
        # generates. Slash-less entries keep the old skip behavior so no
        # legacy row can come alive as a root redirect on regen.
        service = SimpleNamespace(
            path_redirects=[{'path': 'old.example.com', 'target': 'new.example.com'}]
        )
        self.assertEqual(_service_path_redirect_rules(service), [])
        # Stored form always carries the trailing slash (the serializer
        # normalizes bare `old.example.com` to `old.example.com/`); a
        # slash-less entry is skipped exactly like before this feature.
        service = SimpleNamespace(
            path_redirects=[{'path': 'old.example.com/', 'target': 'new.example.com'}]
        )
        rules = _service_path_redirect_rules(service)
        self.assertEqual(rules, [('old.example.com', '/', 'new.example.com', '')])
        lines = _path_redirect_site_lines(rules, site_domain='old.example.com')
        self.assertIn('redir https://new.example.com/ 301', "\n".join(lines))
