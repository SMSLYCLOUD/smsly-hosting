import paramiko, base64
HOST='163.245.214.62'; PW='agbonsalo'
pycode = "import requests\nr=requests.get('http://localhost:4000/v1/models', headers={'Authorization':'Bearer agbonsalo'})\nprint(r.status_code)\nprint(r.text)\n"
b64 = base64.b64encode(pycode.encode()).decode()
cmd = f"docker exec ai-router-b4635e3a python -c \"import base64; exec(base64.b64decode('{b64}'))\""
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect(HOST, username='root', password=PW, timeout=20, banner_timeout=20, auth_timeout=20, allow_agent=False, look_for_keys=False)
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=40)
print(stdout.read().decode())
print('ERR', stderr.read().decode())
ssh.close()
