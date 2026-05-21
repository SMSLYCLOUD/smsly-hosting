import paramiko
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('69.164.244.51', username='root', password='agbonsalo', timeout=15)

print("=== Fixing DB password in Node 1 .env and recovery seed ===")
# Replace b3b500d6e13ce9d8c382c3566d456c96 with e74185277b3086f498c276507845d609
stdin, stdout, stderr = client.exec_command('''
sed -i 's/b3b500d6e13ce9d8c382c3566d456c96/e74185277b3086f498c276507845d609/g' /opt/smsly-hosting/.env
if [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
    sed -i 's/b3b500d6e13ce9d8c382c3566d456c96/e74185277b3086f498c276507845d609/g' /opt/smsly-hosting/.agent_lite_seed
fi
echo "✓ Replacements done in Node 1 files"
''')
print(stdout.read().decode('utf-8'))

client.close()
