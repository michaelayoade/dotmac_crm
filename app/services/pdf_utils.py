"""PDF helpers shared across web routes.

WeasyPrint uses pydyf internally. Some environments have older/newer pydyf APIs,
so we patch the minimal compatibility surface before generating PDFs.
"""

from __future__ import annotations

from typing import Any


def ensure_pydyf_compat() -> None:
    """Patch pydyf.PDF initializer for older API variants.

    Keep this helper tiny and dependency-free: if pydyf isn't importable, do nothing.
    """

    try:
        import pydyf  # type: ignore[import-not-found]
    except Exception:
        return
    pydyf_module: Any = pydyf

    try:
        init_args = pydyf_module.PDF.__init__.__code__.co_argcount
    except Exception:
        return

    if init_args == 1:
        original_init = pydyf_module.PDF.__init__

        def _compat_init(self, *args, **kwargs):
            original_init(self)
            version = args[0] if len(args) > 0 else kwargs.get("version")
            identifier = args[1] if len(args) > 1 else kwargs.get("identifier")
            if version is not None:
                self.version = version if isinstance(version, (bytes, bytearray)) else str(version).encode()
            if identifier is not None:
                self.identifier = identifier
            if not hasattr(self, "version"):
                self.version = b"1.7"
            if not hasattr(self, "identifier"):
                self.identifier = None
            return None

        pydyf_module.PDF.__init__ = _compat_init

    if not hasattr(pydyf_module.Stream, "transform"):

        def _compat_transform(self, a=1, b=0, c=0, d=1, e=0, f=0):
            return self.set_matrix(a, b, c, d, e, f)

        pydyf_module.Stream.transform = _compat_transform

    if not hasattr(pydyf_module.Stream, "text_matrix"):

        def _compat_text_matrix(self, a=1, b=0, c=0, d=1, e=0, f=0):
            return self.set_matrix(a, b, c, d, e, f)

        pydyf_module.Stream.text_matrix = _compat_text_matrix
