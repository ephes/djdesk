"""Descriptor durability shared by notebook and sample publishers."""

import errno
import os

try:  # pragma: no cover - absent on Windows
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


_UNSUPPORTED = frozenset(
    {errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS, errno.EINVAL, errno.ENOTTY}
)


def sync_descriptor(fd: int, *, fcntl_module=fcntl) -> None:
    """Sync the descriptor and, on macOS, flush the drive cache when supported."""
    os.fsync(fd)
    full_sync = getattr(fcntl_module, "F_FULLFSYNC", None)
    if full_sync is not None:
        try:
            fcntl_module.fcntl(fd, full_sync)
        except OSError as error:
            if error.errno not in _UNSUPPORTED:
                raise
