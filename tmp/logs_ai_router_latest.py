import paramiko, sys
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect('163.245.214.62', username='root', password='agbonsalo', timeout=20, banner_timeout=20, auth_timeout=20, allow_agent=False, look_for_keys=False)
cmd='docker logs --tail 120 ai-router-b4635e3a'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
sys.stdout.buffer.write(stdout.read())
sys.stdout.buffer.write(b"\nERR:\n")
sys.stdout.buffer.write(stderr.read())
ssh.close()
