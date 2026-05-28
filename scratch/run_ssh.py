import paramiko
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

def run_ssh(host, password, command):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username="root", password=password, timeout=30.0, banner_timeout=30.0)
        stdin, stdout, stderr = client.exec_command(command)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        if out: print(out)
        if err: print(err, file=sys.stderr)
    finally:
        client.close()

if __name__ == "__main__":
    host = sys.argv[1]
    password = sys.argv[2]
    command = sys.argv[3]
    run_ssh(host, password, command)
