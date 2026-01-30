from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth.models import User
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
import os

# Tracks installation time
INSTALL_TIME = timezone.now()

class SetupStatusView(APIView):
    """
    Checks if the instance is in 'Setup Mode'.
    Setup Mode is active if:
    1. No admin user exists AND
    2. Less than 10 minutes have passed since process start.
    """
    permission_classes = [] # Public access needed to check status

    def get(self, request):
        admin_exists = User.objects.filter(is_superuser=True).exists()
        time_since_install = timezone.now() - INSTALL_TIME
        time_remaining = 600 - time_since_install.total_seconds() # 10 minutes = 600s

        if admin_exists:
            return Response({"is_setup": True, "reason": "Admin exists"})

        if time_remaining <= 0:
            return Response({"is_setup": True, "reason": "Setup window expired. Use CLI to create admin."}, status=status.HTTP_403_FORBIDDEN)

        return Response({
            "is_setup": False,
            "time_remaining": int(time_remaining)
        })

class SetupInitView(APIView):
    """
    Perform the initial setup.
    """
    permission_classes = []

    def post(self, request):
        # Double check conditions to prevent race conditions/exploits
        admin_exists = User.objects.filter(is_superuser=True).exists()
        if admin_exists:
            return Response({"error": "Setup already completed"}, status=status.HTTP_403_FORBIDDEN)

        time_since_install = timezone.now() - INSTALL_TIME
        if time_since_install.total_seconds() > 600:
            return Response({"error": "Setup window expired"}, status=status.HTTP_403_FORBIDDEN)

        data = request.data
        password = data.get("password")
        env_vars = data.get("env_vars", {}) # Dict of key-value pairs

        if not password or len(password) < 8:
            return Response({"error": "Password must be at least 8 characters"}, status=status.HTTP_400_BAD_REQUEST)

        # 1. Create Admin User
        try:
            User.objects.create_superuser(
                username="admin",
                email=data.get("email", "admin@smsly.io"),
                password=password
            )
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # 2. Set Env Variables (Persist to .env file for global config)
        # Note: In a real container setup, modifying .env at runtime might not update process env immediately
        # without restart, but we can update the DB-backed Global Config if we had one.
        # For now, we will append to .env so they persist on next restart, and potentially
        # set them in os.environ for current process visibility (limited scope).

        env_file_path = os.path.join(settings.BASE_DIR, '.env')
        try:
            with open(env_file_path, "a") as f:
                f.write("\n# Added via Setup Wizard\n")
                for key, value in env_vars.items():
                    # Basic sanitization
                    clean_key = key.upper().strip()
                    clean_val = value.strip()
                    if clean_key and clean_val:
                        f.write(f"{clean_key}={clean_val}\n")
                        # Update current process too (hacky but useful for immediate feedback)
                        os.environ[clean_key] = clean_val
        except Exception as e:
            # Non-blocking error, user created
            print(f"Failed to write .env: {e}")

        return Response({"message": "Setup complete. Please login."})
