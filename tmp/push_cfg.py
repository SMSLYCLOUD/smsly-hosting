import paramiko, textwrap, io
HOST='163.245.214.62'; PW='agbonsalo'
cfg=textwrap.dedent('''
model_list:
  - model_name: phi3
    litellm_params:
      model: phi3
      provider: ollama
      api_base: http://ollama-smsly:11434
      api_key: ""
''')
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect(HOST, username='root', password=PW, timeout=20, banner_timeout=20, auth_timeout=20, allow_agent=False, look_for_keys=False)
sftp=ssh.open_sftp();
f=sftp.file('/tmp/proxy_server_config.yaml','w'); f.write(cfg); f.close(); sftp.close()
ssh.exec_command('docker cp /tmp/proxy_server_config.yaml ai-router-b4635e3a:/app/proxy_server_config.yaml', timeout=30)
ssh.exec_command('docker restart ai-router-b4635e3a', timeout=60)
ssh.close()
