with open("backend/apps/deployments/tasks.py", "r") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if 'LITELLM_MASTER_KEY": "${RANDOM_PASSWORD}"' in line:
        line = line.replace('LITELLM_MASTER_KEY": "${RANDOM_PASSWORD}"', 'LITELLM_MASTER_KEY": "sk-${RANDOM_PASSWORD}"')
    if "prisma migrate db push" in line:
        continue # remove the hook injected earlier
    new_lines.append(line)

with open("backend/apps/deployments/tasks.py", "w") as f:
    f.writelines(new_lines)
