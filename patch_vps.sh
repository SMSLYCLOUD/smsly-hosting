cd /opt/smsly-hosting
sed -i 's/"docker_image": "ghcr.io\/berriai\/litellm:main-stable"/"docker_image": "ghcr.io\/berriai\/litellm:main-v1.45.0"/' backend/apps/deployments/fixtures/templates.json
sed -i 's/"value": "${RANDOM_PASSWORD}"/"value": "sk-${RANDOM_PASSWORD}"/' backend/apps/deployments/fixtures/templates.json
sed -i 's/"LITELLM_MASTER_KEY": "${RANDOM_PASSWORD}"/"LITELLM_MASTER_KEY": "sk-${RANDOM_PASSWORD}"/' backend/apps/deployments/tasks.py

git add backend/apps/deployments/fixtures/templates.json backend/apps/deployments/tasks.py
git commit -m "fix(deployments): lock litellm version and add sk- prefix to master key"
