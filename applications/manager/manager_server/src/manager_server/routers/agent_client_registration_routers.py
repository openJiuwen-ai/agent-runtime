"""Admin-only endpoints for optional Keycloak client registration."""

from fastapi import APIRouter, Response
from starlette.concurrency import run_in_threadpool

from manager_server.infrastructure.config import settings
from manager_server.integrations.keycloak.agent_clients import RegistrationError, load_registration_config
from manager_server.routers.auth_guards import AdminUser

registration_router = APIRouter(prefix="/integrations/keycloak/agent-client-registrations")


@registration_router.get("/capabilities")
async def registration_capabilities(user: AdminUser, response: Response):
    response.headers["Cache-Control"] = "no-store"
    enabled = settings.agent_client_registration_enabled
    try:
        if enabled:
            await run_in_threadpool(load_registration_config, settings)
        return {"enabled": enabled, "configured": enabled}
    except RegistrationError as exc:
        return {"enabled": True, "configured": False, "reason": str(exc)}
