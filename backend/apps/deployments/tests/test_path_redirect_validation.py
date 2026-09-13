"""Serializer validation for path redirects (domain/root sources, loop guards)."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import serializers

from apps.deployments.models import Service
from apps.deployments.serializers.service import ServiceSerializer

User = get_user_model()


class PathRedirectValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="redir-val", password="p")
        self.service = Service.objects.create(
            name="redir-svc",
            owner=self.user,
            deploy_type="DOCKER",
            docker_image="registry:5000/redir-svc:latest",
            public_domain="app.example.com",
            custom_domains=["shop.example.com"],
            host_aliases=[{"host": "accounts.example.com", "rewrite_root": "/login"}],
        )
        self.ser = ServiceSerializer(instance=self.service)

    def test_bare_domain_source_normalized_to_root(self):
        out = self.ser.validate_path_redirects(
            [{"path": "accounts.trulay.co", "target": "accounts.trulay.co/login"}])
        self.assertEqual(out, [{
            "path": "accounts.trulay.co/",
            "target": "accounts.trulay.co/login",
        }])

    def test_trailing_slash_domain_source_accepted(self):
        out = self.ser.validate_path_redirects(
            [{"path": "accounts.trulay.co/", "target": "accounts.trulay.co/login"}])
        self.assertEqual(out[0]["path"], "accounts.trulay.co/")

    def test_bare_slash_rejected(self):
        with self.assertRaises(serializers.ValidationError):
            self.ser.validate_path_redirects(
                [{"path": "/", "target": "app.example.com"}])

    def test_root_self_loop_rejected(self):
        with self.assertRaises(serializers.ValidationError):
            self.ser.validate_path_redirects(
                [{"path": "accounts.trulay.co", "target": "accounts.trulay.co"}])
        with self.assertRaises(serializers.ValidationError):
            self.ser.validate_path_redirects(
                [{"path": "accounts.trulay.co", "target": "accounts.trulay.co/"}])

    def test_same_segment_self_loop_on_routed_host_rejected(self):
        with self.assertRaises(serializers.ValidationError):
            self.ser.validate_path_redirects(
                [{"path": "app.example.com/account",
                  "target": "app.example.com/account"}])

    def test_plain_source_self_loop_on_routed_host_rejected(self):
        with self.assertRaises(serializers.ValidationError):
            self.ser.validate_path_redirects(
                [{"path": "/account", "target": "shop.example.com/account"}])

    def test_plain_source_to_foreign_host_allowed(self):
        out = self.ser.validate_path_redirects(
            [{"path": "/account", "target": "other.example.com/account"}])
        self.assertEqual(out, [{
            "path": "/account", "target": "other.example.com/account"}])

    def test_existing_plain_behavior_preserved(self):
        out = self.ser.validate_path_redirects(
            [{"path": "/account", "target": "accounts.example.com/login"}])
        self.assertEqual(out, [{
            "path": "/account", "target": "accounts.example.com/login"}])
        with self.assertRaises(serializers.ValidationError):
            self.ser.validate_path_redirects(
                [{"path": "/account", "target": "accounts.example.com/login"},
                 {"path": "/account", "target": "x.example.com"}])
