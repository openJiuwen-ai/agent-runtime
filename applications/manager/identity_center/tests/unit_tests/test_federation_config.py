"""Federation settings exposed through the Manager environment file."""

from pathlib import Path

from identity_center.infrastructure.config import Settings


def test_manager_env_example_exposes_safe_federation_defaults():
    manager_dir = Path(__file__).resolve().parents[3]

    settings = Settings(_env_file=manager_dir / ".env.example")

    assert settings.federation_demo_enabled is False
    assert settings.federation_public_path_prefix == "/idp"
    assert settings.federation_request_ttl_seconds == 300
    assert settings.federation_code_ttl_seconds == 60
    assert settings.federation_demo_admin_group == "enterprise-admins"


def test_federation_settings_accept_explicit_environment_values(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "IDENTITY_FEDERATION_DEMO_ENABLED=true",
                "IDENTITY_FEDERATION_PUBLIC_PATH_PREFIX=/idp",
                "IDENTITY_FEDERATION_REQUEST_TTL=420",
                "IDENTITY_FEDERATION_CODE_TTL=90",
                "IDENTITY_FEDERATION_DEMO_ADMIN_GROUP=verified-admins",
            )
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.federation_demo_enabled is True
    assert settings.federation_public_path_prefix == "/idp"
    assert settings.federation_request_ttl_seconds == 420
    assert settings.federation_code_ttl_seconds == 90
    assert settings.federation_demo_admin_group == "verified-admins"
