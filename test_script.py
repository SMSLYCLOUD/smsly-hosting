import re

domain = "addon-minio-a0ab03c1.domain.com"
router_name = "smsly-addon-minio-a0ab03c1-1234-1234"
print(f"traefik.http.routers.{router_name}.rule=Host(`{domain}`)")
