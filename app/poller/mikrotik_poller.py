"""Placeholder MikroTik poller module.

This project no longer ships the poller implementation, but the package
entry points still reference these symbols. Keep lightweight stubs here
to avoid import errors and make the missing implementation explicit.
"""

from __future__ import annotations


class MikroTikConnection:
    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError("MikroTik poller is not available in this build.")


class DevicePool:
    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError("MikroTik poller is not available in this build.")


class BandwidthPoller:
    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError("MikroTik poller is not available in this build.")


async def main() -> None:
    raise RuntimeError("MikroTik poller is not available in this build.")
