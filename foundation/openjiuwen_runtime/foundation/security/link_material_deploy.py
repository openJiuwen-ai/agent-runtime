# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""INTERNAL deployment image worker. stdin/stdout is a PRIVATE IPC channel.

Never invoke this as a user-facing diagnostic: ensure returns role Secrets to
the shell installer in memory. The installer prints public summaries only.
This module runs once in the deployment image, not as a Runtime background task.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Awaitable, TypeVar

from sqlalchemy import MetaData, Table, inspect, select

from .link_certificate_bundle import materialize_role, validate_bundle
from .link_material_store import _read_bundle, ensure, sync_runtime
from .link_profile import LinkProfile, LinkProfileError


_T = TypeVar("_T")


def _safe_database_code(exc: Exception) -> str | None:
    """Return only a non-secret driver code, never the DB exception message."""
    original = getattr(exc, "orig", None)
    code = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if code is None:
        arguments = getattr(original, "args", ())
        if arguments and isinstance(arguments[0], int):
            code = arguments[0]
    if isinstance(code, int):
        return str(code)
    if isinstance(code, str) and code.isalnum() and len(code) <= 12:
        return code
    return None


async def _database_stage(stage: str, operation: Awaitable[_T]) -> _T:
    """Add a fixed stage and safe driver code without leaking SQL/credentials."""
    try:
        return await operation
    except LinkProfileError:
        raise
    except Exception as exc:
        code = _safe_database_code(exc)
        detail = f", database_code={code}" if code else ""
        raise LinkProfileError(
            f"{stage} failed ({type(exc).__name__}{detail})"
        ) from None


def secret_name(role):
    return "jiuwenswarm-link-" + role


def require_database_names(settings):
    for key in ("GATEWAY_DB_NAME", "RUNTIME_DB_NAME"):
        value = settings.get(key)
        if not isinstance(value, str) or not value.strip():
            raise LinkProfileError(
                f"{key} must be resolved before certificate preparation"
            )


def deployment_plan(settings):
    namespace = settings["NAMESPACE"]
    domain = settings.get("AGENT_RUNTIME_LINK_MTLS_CLUSTER_DOMAIN") or "cluster.local"
    headless = settings.get("AGENT_SERVER_NAME") or "jiuwenclaw-agentserver"
    names = {
        "gateway": settings["GATEWAY_NAME"],
        "runtime": settings["AGENT_RUNTIME_NAME"],
    }
    ports = {
        "gateway": int(settings["GATEWAY_CONFIG_HTTP_PORT"]),
        "runtime": int(settings["AGENT_RUNTIME_PORT"]),
    }
    endpoints = {r: f"{n}:{ports.get(r)}" for r, n in names.items()}
    sans = {
        r: [
            n,
            f"{n}.{namespace}",
            f"{n}.{namespace}.svc",
            f"{n}.{namespace}.svc.{domain}",
            "127.0.0.1",
        ]
        for r, n in names.items()
    }
    sans["agentserver"] = [f"*.{headless}.{namespace}.svc.{domain}"]
    return {
        "endpoints": endpoints,
        "sans": sans,
        "headless": headless,
        "cluster_domain": domain,
    }


async def connect(settings, role, *, initialize=False):
    from openjiuwen_runtime.foundation.db import MySQLHandler, PostgreSQLHandler

    prefix = role.upper()
    database = settings.get(f"{prefix}_DB_NAME")
    if not isinstance(database, str) or not database.strip():
        raise LinkProfileError(
            f"{prefix}_DB_NAME must be resolved before certificate preparation"
        )
    values = dict(
        host=settings.get("LINK_DB_CONNECT_HOST") or settings["DB_HOST"],
        port=int(settings["DB_PORT"]),
        database=database,
        user=settings[f"{prefix}_DB_USER"],
        password=settings[f"{prefix}_DB_PASSWORD"],
    )
    if settings["DB_TYPE"] == "mysql":
        handler = MySQLHandler(**values)
    elif settings["DB_TYPE"] == "postgresql":
        handler = PostgreSQLHandler(
            **values, schema=settings.get(f"{prefix}_PG_SCHEMA") or "public"
        )
    else:
        raise LinkProfileError("enterprise material store requires MySQL or PostgreSQL")
    if initialize:
        await handler.init_database()
    await handler.connect()
    handler.get_engine().sync_engine.hide_parameters = True
    return handler


async def load_current(settings, plan):
    db = await connect(settings, "gateway")
    try:
        async with db.get_engine().connect() as connection:
            exists = await connection.run_sync(
                lambda c: inspect(c).has_table("link_binding_state")
            )
            if not exists:
                raise LinkProfileError("certificate material has not been initialized")
            table = await connection.run_sync(
                lambda c: Table("link_binding_state", MetaData(), autoload_with=c)
            )
            rows = (await connection.execute(select(table))).mappings().all()
            if len(rows) != 1 or rows[0]["service_role"] != "gateway":
                raise LinkProfileError("missing or ambiguous Gateway binding")
            if "certificate_materials" not in table.c:
                raise LinkProfileError(
                    "binding state has no recoverable certificate material"
                )
            return _read_bundle(
                rows[0],
                endpoints=plan["endpoints"],
                sans=plan["sans"],
            )
    finally:
        await db.disconnect()


def validate_existing_secrets(bundle, existing):
    for role, secret in existing.items():
        if secret is None:
            continue
        wanted = bundle["materials"][role]
        try:
            actual = {
                key: base64.b64decode(value, validate=True).decode()
                for key, value in secret.get("data", {}).items()
            }
        except Exception:
            raise LinkProfileError("existing certificate Secret is invalid") from None
        if actual != wanted:
            raise LinkProfileError(
                "existing Secret conflicts with authoritative database material"
            )


async def dispatch(message):
    settings, action = message["settings"], message["action"]
    mode = settings.get("JIUWENSWARM_LINK_MTLS_MODE", "off")
    if mode != "enforce":
        raise LinkProfileError("certificate preparation requires explicit enforce mode")
    if action == "imports":
        from . import link_material_sync

        return {
            "ready": True,
            "helper_code": Path(link_material_sync.__file__).read_text(),
        }
    require_database_names(settings)
    plan = deployment_plan(settings)
    if action == "ensure":
        if settings["GATEWAY_DB_NAME"] == settings["RUNTIME_DB_NAME"] and (
            settings["DB_TYPE"] != "postgresql"
            or settings.get("GATEWAY_PG_SCHEMA", "public")
            == settings.get("RUNTIME_PG_SCHEMA", "public")
        ):
            raise LinkProfileError(
                "Gateway and Runtime require separate database namespaces"
            )
        gateway = await _database_stage(
            "Gateway database initialization",
            connect(settings, "gateway", initialize=True),
        )
        runtime = None
        try:
            runtime = await _database_stage(
                "Runtime database initialization",
                connect(settings, "runtime", initialize=True),
            )
            bundle, reused = await _database_stage(
                "Gateway certificate binding persistence",
                ensure(
                    gateway.get_engine(),
                    endpoints=plan["endpoints"],
                    sans=plan["sans"],
                    existing_secret=any(message.get("existing", {}).values()),
                ),
            )
            validate_existing_secrets(bundle, message.get("existing", {}))
            await _database_stage(
                "Runtime public binding synchronization",
                sync_runtime(
                    runtime.get_engine(),
                    bundle,
                    secret_name=secret_name("agentserver"),
                ),
            )
        finally:
            await gateway.disconnect()
            if runtime is not None:
                await runtime.disconnect()
        documents = []
        for role, files in bundle["materials"].items():
            documents.append(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "type": "Opaque",
                    "metadata": {
                        "name": secret_name(role),
                        "namespace": settings["NAMESPACE"],
                        "labels": {"jiuwenswarm-link-role": role},
                    },
                    "data": {
                        key: base64.b64encode(value.encode()).decode()
                        for key, value in files.items()
                    },
                }
            )
        summary = validate_bundle(bundle)
        return {"summary": {**summary, "reused": reused}, "secrets": documents}
    bundle = await load_current(settings, plan)
    if action == "status":
        validate_existing_secrets(bundle, message.get("existing", {}))
        return {"summary": validate_bundle(bundle)}
    if action not in {"request", "wait"}:
        raise LinkProfileError("unknown internal deployment action")
    if action == "request":
        message["body"] = json.loads(settings.pop("__LINK_REQUEST_BODY"))
    target = message["target"]
    if target not in {"gateway", "runtime"}:
        raise LinkProfileError("invalid request target")
    path = message["request_path"]
    if not path.startswith("/") or path.startswith("//"):
        raise LinkProfileError("invalid managed request path")
    import httpx

    with tempfile.TemporaryDirectory(prefix="link-manager-") as temporary:
        profile_path = materialize_role(bundle, "manager", Path(temporary) / "manager")
        profile = LinkProfile.load(str(profile_path), roles={"manager"})
        logical = profile.endpoint("link://" + target + path, role=target)
        attempts = 90 if action == "wait" else 1
        async with httpx.AsyncClient(
            trust_env=False,
            timeout=3 if action == "wait" else 60,
            **profile.client_kwargs(role=target),
        ) as client:
            for attempt in range(attempts):
                # Connect to the resolved ClusterIP, retain original SNI/Host and
                # fingerprint validation; no verify=False or HTTPS->HTTP downgrade.
                request = client.build_request(
                    "GET" if action == "wait" else "POST",
                    logical,
                    headers=profile.headers(),
                    json=message.get("body") if action == "request" else None,
                )
                original = request.url
                request.url = original.copy_with(host=message["connect_host"])
                request.extensions["sni_hostname"] = original.host
                request.headers["Host"] = original.netloc.decode()
                try:
                    response = await client.send(request)
                    response.raise_for_status()
                    data = response.json()
                    if (
                        target == "runtime"
                        and action == "request"
                        and data.get("ok") is not True
                    ):
                        raise LinkProfileError(
                            "Runtime rejected configuration; inspect service logs"
                        )
                    return {
                        "status": response.status_code,
                        "target": target,
                        "path": path,
                    }
                except (httpx.TransportError, httpx.HTTPStatusError):
                    if attempt == attempts - 1:
                        raise LinkProfileError(
                            "authenticated service request failed; inspect service logs"
                        ) from None
                    await asyncio.sleep(1)


def main() -> int:
    # Dependencies sometimes log to stdout; isolate logs from the private protocol.
    output = sys.stdout
    try:
        message = json.load(sys.stdin)
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(sys.stderr):
            result = asyncio.run(asyncio.wait_for(dispatch(message), timeout=420))
        json.dump(result, output)
        return 0
    except Exception as exc:
        # Never include SQLAlchemy parameter dumps, passwords or response bodies.
        safe = str(exc) if isinstance(exc, LinkProfileError) else type(exc).__name__
        # Emit only this sanitized record; third-party DB logs remain disabled.
        handler = logging.StreamHandler(sys.stderr)
        try:
            record = logging.LogRecord(
                __name__,
                logging.ERROR,
                __file__,
                0,
                "mTLS deployment stopped: %s",
                (safe,),
                None,
            )
            handler.handle(record)
        finally:
            handler.close()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
