#!/usr/bin/env python3
"""Run component regressions with an explicit caller workspace."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from platform_paths import MODULES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--module", choices=MODULES)
    parser.add_argument("--integration", action="store_true", help="include agentd end-to-end scenarios")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent
    env = dict(os.environ, PLATFORM_WORKSPACE=str(Path(args.workspace).expanduser().resolve()))
    tests = [repo / "test_platform_paths.py"]
    for name in (args.module,) if args.module else MODULES:
        tests.extend(sorted((repo / name).glob("test_*.py")))
    if args.integration and args.module in (None, "agentd"):
        tests.append(repo / "agentd/e2e.py")
    failed = []
    for test in tests:
        print("Running " + str(test.relative_to(repo)), flush=True)
        if subprocess.run([sys.executable, str(test)], env=env).returncode:
            failed.append(str(test.relative_to(repo)))
    print("Failed: " + ", ".join(failed) if failed else "All regression programs passed")
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
