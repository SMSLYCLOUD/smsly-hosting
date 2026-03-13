import paramiko
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect('163.245.214.62', username='root', password='agbonsalo', timeout=20, banner_timeout=20, auth_timeout=20, allow_agent=False, look_for_keys=False)
cmd="docker ps -q --filter 'name=^ollama'"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=20)
ids = [i.strip() for i in stdout.read().decode().split() if i.strip()]
for i in ids:
    ssh.exec_command(f'docker stop {i}', timeout=30)
print(f'stopped {len(ids)} ollama containers')
ssh.exec_command('docker rm -f ai-router-b4635e3a || true', timeout=20)
ssh.exec_command('docker start ai-router-b4635e3a || true', timeout=20)
ssh.close()
