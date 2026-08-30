from django.test import TestCase

from apps.domains.utils import normalize_domain, split_host_and_path


class NormalizeDomainTests(TestCase):
    def test_bare_domain_unchanged(self):
        self.assertEqual(normalize_domain('app.example.com'), 'app.example.com')

    def test_uppercase_and_trailing_dot_normalized(self):
        self.assertEqual(normalize_domain('App.Example.COM.'), 'app.example.com')

    def test_wildcard_requires_opt_in(self):
        with self.assertRaises(ValueError):
            normalize_domain('*.example.com')
        self.assertEqual(normalize_domain('*.example.com', allow_wildcard=True), '*.example.com')

    def test_scheme_rejected(self):
        with self.assertRaises(ValueError):
            normalize_domain('https://app.example.com')

    def test_path_rejected(self):
        with self.assertRaises(ValueError):
            normalize_domain('app.example.com/login')

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            normalize_domain('')


class SplitHostAndPathTests(TestCase):
    def test_no_path_returns_empty_string(self):
        self.assertEqual(split_host_and_path('app.example.com'), ('app.example.com', ''))

    def test_simple_path(self):
        self.assertEqual(split_host_and_path('app.example.com/login'), ('app.example.com', '/login'))

    def test_nested_path(self):
        self.assertEqual(split_host_and_path('app.example.com/v1/api/'), ('app.example.com', '/v1/api/'))

    def test_trailing_slash(self):
        self.assertEqual(split_host_and_path('app.example.com/'), ('app.example.com', '/'))

    def test_uppercase_normalized(self):
        self.assertEqual(split_host_and_path('App.Example.COM/Login'), ('app.example.com', '/Login'))

    def test_scheme_rejected(self):
        with self.assertRaises(ValueError):
            split_host_and_path('https://app.example.com/login')

    def test_empty_host_rejected(self):
        with self.assertRaises(ValueError):
            split_host_and_path('/login')

    def test_spaces_rejected(self):
        with self.assertRaises(ValueError):
            split_host_and_path('app.example.com/has space')
