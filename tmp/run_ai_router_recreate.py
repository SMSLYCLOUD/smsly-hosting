import paramiko
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect('163.245.214.62', username='root', password='agbonsalo', timeout=25, banner_timeout=25, auth_timeout=25, allow_agent=False, look_for_keys=False)
# remove old container if exists
ssh.exec_command('docker rm -f ai-router-b4635e3a || true', timeout=20)
run_cmd = " \
 docker run -d --name ai-router-b4635e3a --network smsly-net --memory=2g \
  -e PORT=4000 \
  -e UVICORN_WORKERS=1 \
  -e LITELLM_MASTER_KEY=agbonsalo \
  -e PUBLIC_DOMAIN=ai-router-b4635e3a-454fd9.pcloud.linadeluxe.com \
  -e DATABASE_URL=postgresql://postgres_ai_router_b4635e3a:_VNR2ZEiYa_7XUsnbJ7LvJO9hvdnTOYG@postgres-ai-router-b4635e3a:5432/ai_router_b4635e3a \
  -e HOSTNAME=0.0.0.0 \
  -e SECRET_KEY=R78d_zex8DvHHlVFIgCVazRDmIMT120md4K5KR7Loll4kNiQ5lditEsmck80oPVGsE8 \
  -e FERNET_KEY=t5HqBQWewxvxhhh0ZyjbKVX2lpyHKUVAA9-yAHI3VGk= \
  -e ADMIN_EMAIL=admin@example.com \
  -e ADMIN_USERNAME=admin \
  -e OPS_HEALTH_TOKEN=RAz0ZAmbP3we425DJrxVQg \
  -e ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0,ai-router-b4635e3a-454fd9.pcloud.linadeluxe.com,ai.smsly.cloud \
  -e DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0,ai-router-b4635e3a-454fd9.pcloud.linadeluxe.com,ai.smsly.cloud \
  -e MARKETER_ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0,ai-router-b4635e3a-454fd9.pcloud.linadeluxe.com,ai.smsly.cloud \
  -e API_INTERNAL_URL=http://127.0.0.1:4000 \
  -e SMSLY_BACKEND_URL=http://127.0.0.1:4000 \
  -e CUSTOM_DOMAINS=ai.smsly.cloud \
  ghcr.io/berriai/litellm:main-stable \
  litellm --port 4000 --host 0.0.0.0".replace('\n',' ')
stdin, stdout, stderr = ssh.exec_command(run_cmd, timeout=60)
print(stdout.read().decode())
print('ERR', stderr.read().decode())
ssh.close()
