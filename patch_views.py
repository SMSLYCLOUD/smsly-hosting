import re

with open("backend/apps/deployments/views.py", "r") as f:
    content = f.read()

# Make sure we don't patch twice
if "setup_github_webhook" not in content:
    # Add import
    import_statement = "from apps.deployments.services.github_webhooks import setup_github_webhook\nimport threading"
    content = re.sub(r'from \.models import', f'{import_statement}\nfrom .models import', content)

    # Patch perform_create
    old_create = """    def perform_create(self, serializer):
        deploy_type = serializer.validated_data.get('deploy_type', 'GIT')
        tier_gates_disabled = bool(getattr(settings, "SMSLY_DISABLE_TIER_GATES", False))
        if deploy_type == 'FUNCTION' and not tier_gates_disabled:
            license = PlatformLicense.load()
            if license.is_community:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Serverless Functions require Pro tier.")
        serializer.save(owner=self.request.user)"""

    new_create = """    def perform_create(self, serializer):
        deploy_type = serializer.validated_data.get('deploy_type', 'GIT')
        tier_gates_disabled = bool(getattr(settings, "SMSLY_DISABLE_TIER_GATES", False))
        if deploy_type == 'FUNCTION' and not tier_gates_disabled:
            license = PlatformLicense.load()
            if license.is_community:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Serverless Functions require Pro tier.")

        service = serializer.save(owner=self.request.user)

        # Setup GitHub Webhook if applicable
        if service.deploy_type == 'GIT' and service.repository_url:
            threading.Thread(
                target=setup_github_webhook,
                args=(self.request.user, service.repository_url),
                daemon=True
            ).start()"""

    content = content.replace(old_create, new_create)

    # Patch perform_update
    old_update = """    def perform_update(self, serializer):
        old_status = serializer.instance.status"""

    new_update = """    def perform_update(self, serializer):
        old_repo = serializer.instance.repository_url
        old_status = serializer.instance.status

        service = serializer.save()

        if service.deploy_type == 'GIT' and service.repository_url and service.repository_url != old_repo:
            threading.Thread(
                target=setup_github_webhook,
                args=(self.request.user, service.repository_url),
                daemon=True
            ).start()"""

    if "def perform_update(self, serializer):" in content and "old_repo =" not in content:
        content = content.replace(old_update, new_update)

    with open("backend/apps/deployments/views.py", "w") as f:
        f.write(content)
