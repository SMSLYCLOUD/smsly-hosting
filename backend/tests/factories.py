"""Shared test object factories (no external dependencies)."""
from django.contrib.auth import get_user_model

User = get_user_model()


def create_user(
    username="testuser",
    email="test@example.com",
    password="testpass123",
    **kwargs,
):
    return User.objects.create_user(
        username=username,
        email=email,
        password=password,
        **kwargs,
    )


def create_staff_user(**kwargs):
    kwargs.setdefault("username", "staffuser")
    kwargs.setdefault("email", "staff@example.com")
    kwargs.setdefault("is_staff", True)
    return create_user(**kwargs)
