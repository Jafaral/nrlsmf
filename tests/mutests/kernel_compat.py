"""Skip a mutest when the running kernel is older than a given version."""

import os

from munet.mutest.userapi import test_step


def kernel_version():
    release = os.uname().release.split("-", 1)[0]
    parts = []
    for token in release.split(".")[:3]:
        try:
            parts.append(int(token))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def min_kernel_version(min_version):
    """Return True if the caller should return early.

    min_version is a tuple like (5, 0) or (5, 4, 1).
    """
    min_version = tuple(min_version) + (0,) * (3 - len(min_version))
    have = kernel_version()
    if have >= min_version:
        return False
    need_parts = list(min_version)
    while len(need_parts) > 2 and need_parts[-1] == 0:
        need_parts.pop()
    need = ".".join(str(p) for p in need_parts)
    have_s = ".".join(str(p) for p in have)
    test_step(True, f"SKIP: needs Linux {need}+; this kernel is {have_s}")
    return True
