#!/usr/bin/env python3
"""
Real parity measurement harness for rust_twin vs Django.

Replaces the placeholder test_parity.py. Measures actual latency, asserts
actual status code matches, fails loudly on mismatch.
"""
import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, asdict, field
from typing import Optional

import requests


@dataclass
class EndpointCase:
    method: str
    path: str
    expected_status: int  # status code we expect both sides to return
    requires_auth: bool = False
    note: str = ""


# The 5 endpoints from the original PARITY_REPORT, but now with REAL assertions
# about what they should return.
DEFAULT_CASES = [
    EndpointCase("GET", "/health", 200, note="Both should return 200 with 'OK' or JSON"),
    EndpointCase("GET", "/api/v1/projects", 401, note="Both should require auth (401 unauth)"),
    EndpointCase("GET", "/api/v1/billing/license", 401, note="Both should require auth"),
    EndpointCase("GET", "/api/v1/teams", 401, note="Both should require auth"),
    EndpointCase("POST", "/api/v1/auth/login", 400, note="Both should reject empty creds (400)"),
]


@dataclass
class LatencyStats:
    samples: list  # raw ms samples
    min_ms: float
    median_ms: float
    mean_ms: float
    p95_ms: float
    max_ms: float

    @classmethod
    def from_samples(cls, samples: list) -> "LatencyStats":
        if not samples:
            return cls([], 0, 0, 0, 0, 0)
        return cls(
            samples=samples,
            min_ms=round(min(samples), 3),
            median_ms=round(statistics.median(samples), 3),
            mean_ms=round(statistics.mean(samples), 3),
            p95_ms=round(sorted(samples)[int(len(samples) * 0.95)], 3),
            max_ms=round(max(samples), 3),
        )


@dataclass
class EndpointResult:
    case: dict
    django: dict  # {status, latency: LatencyStats, error}
    rust: dict
    passed: bool
    notes: str = ""


def measure_side(base_url: str, case: EndpointCase, n: int) -> dict:
    """Make n calls to the given side. Return a result dict."""
    statuses = []
    latencies_ms = []
    errors = []
    for _ in range(n):
        url = f"{base_url.rstrip('/')}{case.path}"
        try:
            start = time.perf_counter()
            resp = requests.request(
                case.method,
                url,
                timeout=10,
                verify=False,  # self-signed certs OK in lab
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            statuses.append(resp.status_code)
            latencies_ms.append(elapsed_ms)
        except requests.RequestException as e:
            errors.append(str(e))
    if errors:
        return {"error": errors[0], "latency": None, "status": None}
    return {
        "status": statuses[0],  # first-call status
        "all_statuses": statuses,
        "latency": asdict(LatencyStats.from_samples(latencies_ms)),
    }


def run_parity(
    django_url: str,
    rust_url: str,
    cases: list,
    n: int,
) -> list:
    results = []
    for case in cases:
        django = measure_side(django_url, case, n)
        rust = measure_side(rust_url, case, n)
        passed = (
            django.get("status") == case.expected_status
            and rust.get("status") == case.expected_status
        )
        notes = ""
        if django.get("status") != case.expected_status:
            notes += f" Django returned {django.get('status')}, expected {case.expected_status}."
        if rust.get("status") != case.expected_status:
            notes += f" Rust returned {rust.get('status')}, expected {case.expected_status}."
        results.append(
            EndpointResult(
                case=asdict(case),
                django=django,
                rust=rust,
                passed=passed,
                notes=notes.strip(),
            )
        )
    return results


def render_table(results: list) -> str:
    lines = []
    lines.append(f"{'METHOD':<7} {'PATH':<30} {'EXPECT':<7} {'DJANGO':<7} {'RUST':<7} {'DJ.MED':<8} {'RS.MED':<8} PASS")
    for r in results:
        c = r.case
        dj = r.django
        rs = r.rust
        dj_med = dj["latency"]["median_ms"] if dj.get("latency") else "N/A"
        rs_med = rs["latency"]["median_ms"] if rs.get("latency") else "N/A"
        lines.append(
            f"{c['method']:<7} {c['path']:<30} {c['expected_status']:<7} "
            f"{dj.get('status', 'ERR'):<7} {rs.get('status', 'ERR'):<7} "
            f"{str(dj_med):<8} {str(rs_med):<8} {'✅' if r.passed else '❌'}"
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--django-url", default=os.environ.get("DJANGO_URL", "http://localhost:8000"))
    parser.add_argument("--rust-url", default=os.environ.get("RUST_URL", "http://localhost:8080"))
    parser.add_argument("--n", type=int, default=10, help="Number of samples per endpoint")
    parser.add_argument("--out", default="parity_results.json", help="JSON output path")
    args = parser.parse_args()

    print(f"Django: {args.django_url}")
    print(f"Rust:   {args.rust_url}")
    print(f"Samples per endpoint: {args.n}")
    print()

    results = run_parity(args.django_url, args.rust_url, DEFAULT_CASES, args.n)
    print(render_table(results))
    print()

    # JSON output
    output = {
        "django_url": args.django_url,
        "rust_url": args.rust_url,
        "samples_per_endpoint": args.n,
        "results": [
            {
                "case": r.case,
                "django": r.django,
                "rust": r.rust,
                "passed": r.passed,
                "notes": r.notes,
            }
            for r in results
        ],
    }
    with open(args.out, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Full results written to {args.out}")

    # Exit code
    if all(r.passed for r in results):
        print("\n[OK] All endpoints pass parity check")
        sys.exit(0)
    else:
        failing = [r for r in results if not r.passed]
        print(f"\n[FAIL] {len(failing)} endpoint(s) fail parity:")
        for r in failing:
            print(f"   {r.case['method']} {r.case['path']}: {r.notes}")
        sys.exit(1)


if __name__ == "__main__":
    main()
