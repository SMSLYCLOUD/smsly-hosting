with open("frontend/src/app/new/page.tsx", "r") as f:
    content = f.read()

# Add a little note about webhook auto-configuration
target = 'to browse your repositories.\n                        </p>\n                      )}'
replacement = 'to browse your repositories.\n                        </p>\n                      )}\n                      {ghConnected && (\n                        <p className="text-xs text-muted-foreground text-emerald-500/80">\n                          ✓ Push and Pull Request Webhooks will be configured automatically.\n                        </p>\n                      )}'

if target in content:
    content = content.replace(target, replacement)
    with open("frontend/src/app/new/page.tsx", "w") as f:
        f.write(content)
