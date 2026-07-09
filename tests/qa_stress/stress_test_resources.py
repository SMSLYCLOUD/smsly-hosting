import time

import psutil


def log_memory():
    mem = psutil.virtual_memory()
    print(f"🧠 RAM: {mem.used / 1024**3:.2f}GB / {mem.total / 1024**3:.2f}GB ({mem.percent}%)")

def stress_test_ram(target_percent=80, chunk_size_mb=100):
    print("🔥 Starting RAM Stress Test...")
    log_memory()

    data = []
    try:
        while True:
            mem = psutil.virtual_memory()
            if mem.percent >= target_percent:
                print(f"🛑 Reached target {target_percent}% RAM usage.")
                break

            # Allocate 100MB chunk
            chunk = b'0' * (chunk_size_mb * 1024 * 1024)
            data.append(chunk)
            # print(f"➕ Allocated {chunk_size_mb}MB")

            if len(data) % 10 == 0:
                log_memory()

            time.sleep(0.1)

        print("⏸️ Holding memory for 5 seconds...")
        time.sleep(5)

    except MemoryError:
        print("❌ MemoryError: Hit system limit!")
    except KeyboardInterrupt:
        print("⚠️ Interrupted.")
    finally:
        print("🗑️ Releasing memory...")
        del data
        import gc
        gc.collect()
        log_memory()
        print("✅ RAM Stress Test Complete.")

if __name__ == "__main__":
    stress_test_ram()
