import paramiko, secrets
USER='root'; PW='agbonsalo'
PRIMARY='163.245.216.249'
REPLICA='163.245.216.248'
TIMEOUT=60

pkey='Ku5um9y1f8ue/tjhQOIUbUlKylPRdLRdezds7DSYjHI='
rkey='acvMT9bB9GiIn0zwCCgWnPQZv61/UWnv0ZUQls7dVgg='

confs={
    PRIMARY: """[Interface]
Address = 10.10.0.1/24
ListenPort = 51820
PrivateKey = $(cat /etc/wireguard/privatekey)

[Peer]
PublicKey = {rkey}
AllowedIPs = 10.10.0.2/32
Endpoint = {REPLICA}:51820
PersistentKeepalive = 15
""",
    REPLICA: """[Interface]
Address = 10.10.0.2/24
ListenPort = 51820
PrivateKey = $(cat /etc/wireguard/privatekey)

[Peer]
PublicKey = {pkey}
AllowedIPs = 10.10.0.1/32
Endpoint = {PRIMARY}:51820
PersistentKeepalive = 15
""",
}

for host in [PRIMARY, REPLICA]:
    c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy()); c.connect(host, username=USER, password=PW, timeout=10, banner_timeout=10, auth_timeout=10)
    sftp=c.open_sftp();
    with sftp.file('/etc/wireguard/wg0.conf','w') as f:
        f.write(confs[host])
    sftp.chmod('/etc/wireguard/wg0.conf',0o600)
    sftp.close(); c.close();
    print(f"{host} wg0.conf written")
