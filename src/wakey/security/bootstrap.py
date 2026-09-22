# SPDX-License-Identifier: Apache-2.0
"""Persistent first-run encryption key for explicitly opted-in local installs."""

import os
from pathlib import Path

from wakey.security.secretbox import new_master_key


def persistent_master_key(directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "master.key"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "r") as source:
            if os.fstat(source.fileno()).st_mode & 0o077:
                raise ValueError(
                    "master.key must be readable only by its owner (chmod 600)"
                ) from None
            return source.read().strip()
    with os.fdopen(descriptor, "w") as destination:
        key = new_master_key()
        destination.write(key)
        destination.flush()
        os.fsync(destination.fileno())
    return key
