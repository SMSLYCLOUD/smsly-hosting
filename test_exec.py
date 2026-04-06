import docker
import select

client = docker.from_env()
container = client.containers.run("alpine", "sleep 300", detach=True)
print("Started container:", container.id)

exec_id = client.api.exec_create(container.id, "sh", stdin=True, stdout=True, tty=True)
sock = client.api.exec_start(exec_id, socket=True, tty=True)

raw = getattr(sock, '_sock', None)
if raw is None:
    raw = sock

print("sock type:", type(sock))
print("raw type:", type(raw))

try:
    print("Has fileno?", hasattr(raw, 'fileno'))
    r, w, x = select.select([raw], [], [], 1.0)
    print("select returned:", r)
except Exception as e:
    print("select exception:", type(e), e)

container.stop()
container.remove()
