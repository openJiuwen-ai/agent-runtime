def ensure_async_gaussdb_installed() -> None:
	from .gaussdb_asyncgaussdb import ensure_async_gaussdb_installed as _ensure_async_gaussdb_installed

	_ensure_async_gaussdb_installed()


def ensure_gaussdb_dialect_registered() -> None:
	from .gaussdb_asyncgaussdb import ensure_gaussdb_dialect_registered as _ensure_gaussdb_dialect_registered

	_ensure_gaussdb_dialect_registered()

__all__ = ["ensure_async_gaussdb_installed", "ensure_gaussdb_dialect_registered"]