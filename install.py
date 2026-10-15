#!/usr/bin/env python3
"""Install component aliases into a caller workspace; never start services."""
import argparse
import json
import os
from pathlib import Path

from platform_paths import MODULES


def install(workspace, source=None):
    workspace = Path(workspace).expanduser().resolve(strict=True)
    source = Path(source or __file__).resolve()
    if source.is_file():
        source = source.parent
    if not workspace.is_dir():
        raise ValueError("workspace must be a directory")
    # Validate the entire plan before writing any alias.
    for name in MODULES:
        target = source / name
        if not target.is_dir():
            raise ValueError("missing component: " + name)
        alias = workspace / name
        if alias.is_symlink() and alias.resolve() == target:
            continue
        if alias.exists() or alias.is_symlink():
            raise ValueError("refusing to replace existing path: " + str(alias))
    for name in MODULES:
        alias = workspace / name
        if not alias.is_symlink():
            alias.symlink_to(os.path.relpath(source / name, workspace), target_is_directory=True)
    return {name: os.readlink(workspace / name) for name in MODULES}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(install(args.workspace), ensure_ascii=False))
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
