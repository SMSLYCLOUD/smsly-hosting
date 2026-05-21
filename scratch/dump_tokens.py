from apps.deployments.api_token_auth import APIToken
for t in APIToken.objects.all():
    print(t.name, t.prefix, t.user.username)
