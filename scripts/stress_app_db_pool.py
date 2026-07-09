#!/usr/bin/env python3
import asyncio
import os
import time

import aiohttp

API_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

async def fetch(session, url):
    try:
        async with session.get(url, timeout=5) as response:
            return response.status
    except Exception as e:
        return str(e)

async def main():
    print(f"Starting App DB Pool Stress Test against {API_URL}...")

    # We will hit a lightweight health or API endpoint concurrently
    url = f"{API_URL}/api/v1/health/"

    async with aiohttp.ClientSession() as session:
        tasks = []
        for _ in range(200): # 200 concurrent requests
            tasks.append(fetch(session, url))

        start_time = time.time()
        results = await asyncio.gather(*tasks)
        end_time = time.time()

        success = [r for r in results if r == 200]
        failures = [r for r in results if r != 200]

        print(f"Test completed in {end_time - start_time:.2f} seconds.")
        print(f"Successful requests: {len(success)}")
        print(f"Failed requests: {len(failures)}")

        if failures:
            print("Sample failures:", failures[:5])

if __name__ == "__main__":
    asyncio.run(main())
