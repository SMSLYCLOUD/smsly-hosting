import paramiko
host='163.245.216.248'
user='root'
pw='agbonsalo'
cmds=[
"docker run --rm --network host --entrypoint ollama ollama/ollama pull llama3:8b",
"docker run --rm --network host --entrypoint ollama ollama/ollama pull mistral",
"docker run --rm --network host --entrypoint ollama ollama/ollama pull gemma"
]
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect(host, username=user, password=pw, timeout=10)
for cmd in cmds:
    stdin,stdout,stderr=ssh.exec_command(cmd, timeout=300)
    out=stdout.read().decode(errors='replace'); err=stderr.read().decode(errors='replace'); rc=stdout.channel.recv_exit_status()
    print(f"CMD: {cmd}\nRC: {rc}\nOUT: {out}\nERR: {err}\n---")
ssh.close()
