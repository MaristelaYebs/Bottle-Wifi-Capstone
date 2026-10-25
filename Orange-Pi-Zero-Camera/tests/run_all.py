"""Run every automated test suite and print a pass/fail summary.

    python tests/run_all.py

Each suite is a standalone script (also pytest-compatible). This runs them in
isolated subprocesses so module side effects can't cross-contaminate. Exit code
is non-zero if any suite fails. Hardware/on-device checks are listed in
tests/README.md and are NOT run here.
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    suites = sorted(
        p for p in glob.glob(os.path.join(HERE, "test_*.py"))
        if os.path.basename(p) != "run_all.py"
    )
    results = []
    for path in suites:
        name = os.path.basename(path)
        proc = subprocess.run([sys.executable, path], capture_output=True, text=True)
        ok = proc.returncode == 0
        last = (proc.stdout.strip().splitlines() or ["(no output)"])[-1]
        results.append((name, ok, last, proc.stderr.strip()))

    print("=" * 68)
    passed = sum(1 for _, ok, _, _ in results if ok)
    for name, ok, last, err in results:
        tag = "PASS" if ok else "FAIL"
        print(f"[{tag}] {name:32s} {last}")
        if not ok and err:
            print("       " + err.replace("\n", "\n       ")[-800:])
    print("=" * 68)
    print(f"{passed}/{len(results)} suites passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
