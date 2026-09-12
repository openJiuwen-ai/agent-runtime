# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Small stdlib-only secret-to-private-volume bridge for non-root AgentServers.

Executed in an init/helper container with the SAME image UID/GID as the agent.
Only the helper mounts the source Secret; the agent sees a read-only private
copy, other sidecars see neither. No fsGroup/chown of unrelated PVC/NFS data.
"""

import argparse
import hashlib
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

FILES = ("profile.json", "ca.crt", "tls.crt", "tls.key")


def synchronize(source, destination):
    source, destination = Path(source), Path(destination)
    # Resolve the Kubernetes atomic-writer directory ONCE so all four files
    # belong to the same generation. Never print private file contents.
    snapshot = (source / "..data").resolve(strict=True)
    data = {name: (snapshot / name).read_bytes() for name in FILES}
    digest = hashlib.sha256(b"".join(data[name] for name in FILES)).hexdigest()
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    current = destination / "identity"
    if current.is_symlink() and current.readlink().name == digest:
        return False
    generation = destination / digest
    previous = current.resolve() if current.is_symlink() else None
    if not generation.exists():
        staging = Path(tempfile.mkdtemp(prefix=".material-", dir=destination))
        try:
            for name, value in data.items():
                fd = os.open(staging / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(value)
                    stream.flush()
                    os.fsync(stream.fileno())
            os.rename(staging, generation)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    temporary = destination / ".identity-next"
    if temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(generation.name, target_is_directory=True)
    os.replace(temporary, current)
    # Retain the immediately previous generation for readers already resolving
    # paths during the swap. Only owned generation directories are collected.
    keep = {generation, previous}
    for child in destination.iterdir():
        is_generation = len(child.name) == 64 and all(c in "0123456789abcdef" for c in child.name)
        if child in keep or child.is_symlink():
            continue
        if child.is_dir() and is_generation:
            shutil.rmtree(child)
    return True


def invalidate(destination):
    """A failed refresh must not silently preserve stale authorization forever."""
    current = Path(destination) / "identity"
    if current.is_symlink():
        current.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        try:
            synchronize("/run/link-source", "/run/link-owned/private")
        except Exception as exc:
            logging.getLogger(__name__).error("link material sync failed: %s", type(exc).__name__)
            invalidate("/run/link-owned/private")
            # No plaintext/default identity fallback. Init must not report ready.
            if args.once:
                raise SystemExit(1) from None
        if args.once:
            return
        time.sleep(0.5)


if __name__ == "__main__":
    main()
