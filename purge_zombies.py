from apps.deployments.tasks import delete_service_task
ids = ["cba8cd9e-8356-494d-af34-ceefebf4edba", "9d2253ae-e421-4617-baef-a8070167f0fc", "4a27a126-fb14-4cff-ab7f-5f1035eb9cbf"]
for id in ids:
    try:
        delete_service_task(id, force=True)
        print(f"PURGED: {id}")
    except Exception as e:
        print(f"FAILED: {id} - {e}")
print("PURGE_DONE")
