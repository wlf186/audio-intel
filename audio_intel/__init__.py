"""Local-first audio intelligence service."""

from .version import RELEASE_VERSION, resolve_version


__version__ = resolve_version()

__all__ = ["RELEASE_VERSION", "__version__"]
