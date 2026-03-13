import paramiko, sys
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect('163.245.214.62', username='root', password='agbonsalo', timeout=25, banner_timeout=25, auth_timeout=25, allow_agent=False, look_for_keys=False)
cmd="docker logs --tail 200 ai-router-b4635e3a"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=40)
out = stdout.read()
err = stderr.read()
sys.stdout.buffer.write(out)
sys.stdout.buffer.write(b"\nERR:\n")
sys.stdout.buffer.write(err)
ssh.close()
