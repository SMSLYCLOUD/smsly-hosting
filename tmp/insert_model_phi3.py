import paramiko, json, textwrap
HOST='163.245.214.62'; PW='agbonsalo'
params = json.dumps({"model_name":"phi3","provider":"ollama","api_base":"http://ollama-smsly:11434"})
sql = textwrap.dedent(f"""
INSERT INTO "LiteLLM_ProxyModelTable" (model_id, model_name, litellm_params, model_info, created_by, updated_by)
VALUES ('phi3','phi3', '{params}', '{{}}', 'system','system')
ON CONFLICT (model_id) DO NOTHING;
""")
cmd = textwrap.dedent(f"""
docker exec -i postgres-ai-router-b4635e3a psql -U postgres_ai_router_b4635e3a -d ai_router_b4635e3a <<'SQL'
{sql}SQL
""")
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect(HOST, username='root', password=PW, timeout=20, banner_timeout=20, auth_timeout=20, allow_agent=False, look_for_keys=False)
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=40)
print(stdout.read().decode())
print('ERR', stderr.read().decode())
ssh.close()
