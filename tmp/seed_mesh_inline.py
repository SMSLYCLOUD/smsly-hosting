import paramiko

HOST = "163.245.216.249"
USER = "root"
PW = "agbonsalo"

code = (
    "from django.contrib.auth import get_user_model; "
    "from apps.deployments.models_mesh import MeshNetwork, WireGuardPeer; "
    "from apps.deployments.models_servers import ManagedServer; "
    "User = get_user_model(); owner = User.objects.order_by('date_joined').first(); "
    "assert owner, 'no users'; "
    "mesh, _ = MeshNetwork.objects.get_or_create(name='vps-mesh', defaults={'subnet':'10.10.0.0/24','listen_port':51820,'interface_name':'wg0','is_active':True}); "
    "mesh.subnet='10.10.0.0/24'; mesh.listen_port=51820; mesh.interface_name='wg0'; mesh.is_active=True; mesh.save(); "
    "servers=["
    "{'name':'primary-249','host':'163.245.216.249','wg_address':'10.10.0.1','endpoint':'163.245.216.249:51820','private_key':'mKrcJ8k/EcUEDWtNbgm4tsqlUhbZZ9wkAobJbKIGC3Q=','public_key':'Ku5um9y1f8ue/tjhQOIUbUlKylPRdLRdezds7DSYjHI=','is_primary':True,'is_local':True},"
    "{'name':'replica-248','host':'163.245.216.248','wg_address':'10.10.0.2','endpoint':'163.245.216.248:51820','private_key':'CPr1K0qj3QMEeZUd7Gt9WfH1UJaysLNSXHeXZ+25T2w=','public_key':'acvMT9bB9GiIn0zwCCgWnPQZv61/UWnv0ZUQls7dVgg=','is_primary':False,'is_local':False}"
    "]; "
    "import json; "
    "for sd in servers: "
    " srv,_=ManagedServer.objects.get_or_create(host=sd['host'], defaults={'owner':owner,'name':sd['name'],'ssh_password':'agbonsalo','ssh_user':'root','status':ManagedServer.Status.ONLINE,'role':ManagedServer.ClusterRole.LEADER if sd['is_primary'] else ManagedServer.ClusterRole.FOLLOWER,'wg_address':sd['wg_address'],'is_primary':sd['is_primary']}); "
    " srv.name=sd['name']; srv.ssh_password='agbonsalo'; srv.ssh_user='root'; srv.status=ManagedServer.Status.ONLINE; srv.role=ManagedServer.ClusterRole.LEADER if sd['is_primary'] else ManagedServer.ClusterRole.FOLLOWER; srv.wg_address=sd['wg_address']; srv.is_primary=sd['is_primary']; srv.provision_status=ManagedServer.ProvisionStatus.DONE; srv.save(); "
    " peer,_=WireGuardPeer.objects.get_or_create(mesh=mesh, server=srv, defaults={'private_key':sd['private_key'],'public_key':sd['public_key'],'wg_address':sd['wg_address'],'endpoint':sd['endpoint'],'allowed_ips':'10.10.0.0/24','is_active':True,'is_local':sd['is_local']}); "
    " peer.private_key=sd['private_key']; peer.public_key=sd['public_key']; peer.wg_address=sd['wg_address']; peer.endpoint=sd['endpoint']; peer.allowed_ips='10.10.0.0/24'; peer.is_active=True; peer.is_local=sd['is_local']; peer.save(); "
    "print('mesh', mesh.id); print('peers', list(mesh.peers.values('wg_address','endpoint','is_local')))"
)

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PW, timeout=20, banner_timeout=20, auth_timeout=20)
    cmd = (
        "docker exec smsly-hosting-backend-1 python manage.py shell -c "
        f"\\\"{code}\\\""
    )
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=40)
    print(stdout.read().decode())
    print(stderr.read().decode())
    ssh.close()

if __name__ == '__main__':
    main()
