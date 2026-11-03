"""Opt-in local UI development: save Manager data without contacting a cluster."""

from manager_server.infrastructure.config import settings


def offline_dev_enabled() -> bool:
    if not settings.dev_offline:
        return False
    if not (
        settings.local_dev
        and settings.rest_host in {"127.0.0.1", "::1", "localhost"}
        and settings.db_type.lower() == "sqlite"
    ):
        raise RuntimeError(
            "MANAGER_DEV_OFFLINE requires the local development launcher, "
            "a loopback MANAGER_REST_HOST and SQLite."
        )
    return True
