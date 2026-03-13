import paramiko, textwrap
HOST='163.245.214.62'; PW='agbonsalo'
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect(HOST, username='root', password=PW, timeout=20, banner_timeout=20, auth_timeout=20, allow_agent=False, look_for_keys=False)
inner = textwrap.dedent('''
import requests
try:
    r = requests.get("http://ollama-phi3-0f882aea:11434/api/tags", timeout=5)
    print(r.status_code)
    print(r.text[:500])
except Exception as e:
    print('ERR', e)
''')
cmd = f"docker exec ai-router-b4635e3a python - <<'PY'\n{inner}\nPY"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
print(stdout.read().decode())
print('ERR', stderr.read().decode())
ssh.close()
