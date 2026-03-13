import paramiko, base64
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect('163.245.214.62', username='root', password='agbonsalo', timeout=20, banner_timeout=20, auth_timeout=20, allow_agent=False, look_for_keys=False)
code = """
import requests
r = requests.get('http://localhost:4000/v1/models', headers={'Authorization':'Bearer agbonsalo'})
print(r.status_code)
print(r.text[:2000])
"""
b64 = base64.b64encode(code.encode()).decode()
cmd = f"docker exec ai-router-b4635e3a python -c \"import base64; exec(base64.b64decode('{b64}'))\""
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=40)
print(stdout.read().decode())
print('ERR', stderr.read().decode())
ssh.close()
