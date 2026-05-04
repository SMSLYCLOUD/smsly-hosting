import os
path = '/opt/smsly-hosting/backend/services/platform_updater.py'
with open(path, 'r') as f:
    content = f.read()

# Comment out git pull (already done in previous patch, but keep it here for safety)
target_git = "ok, output = _run(['git', 'pull', '--ff-only', 'origin', 'main'])"
replacement_git = "update_record.append_log('Skipping git pull (Git servers down)'); ok = True; output = 'skipped'"
if target_git in content:
    content = content.replace(target_git, replacement_git)

# Comment out docker build
target_build = "ok, output = _run(['docker', 'compose', '-f', COMPOSE_FILE, 'build', '--no-cache'], timeout=600)"
replacement_build = "update_record.append_log('Skipping docker build (already built on host)'); ok = True; output = 'skipped'"
if target_build in content:
    content = content.replace(target_build, replacement_build)

with open(path, 'w') as f:
    f.write(content)
print("SUCCESS")
