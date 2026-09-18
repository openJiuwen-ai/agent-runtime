# coding: utf-8
"""A completed acquire is not a reservation for the requesting session (#4406)."""

import asyncio

import pytest

from agent_runtime.errors import NoPodAvailable
from tests.conftest import requires_lua

SCOPE = "scope-main"


def _bound_placement(runtime, monkeypatch):
    place = runtime.sm_state.route_place
    calls = 0

    async def bounded(**kwargs):
        nonlocal calls
        calls += 1
        assert calls <= 16, "route repeatedly replays an unusable acquire result"
        return await place(**kwargs)

    monkeypatch.setattr(runtime.sm_state, "route_place", bounded)


@requires_lua
@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("retry", [False, True])
async def test_route_progresses_when_acquired_pod_fills_before_placement(runtime, refresh, retry, monkeypatch):
    await runtime.seed_template(scope_concurrency=6, pod_concurrency=2)
    if refresh:
        old = await runtime.route("old")
        await runtime.config_store.config_refresh()
    _, template = await runtime.config_store.resolve("user", "grp", "bot")
    # The target's acquire succeeds, but concurrent routes claim both slots
    # before the target executes its next placement arbitration.
    acquired = await runtime.rm_facade.acquire(
        scope_id=SCOPE, pod_spec=template.deploy_subset(),
        pool_config=template.pool_config(), request_id="target-request",
    )
    await runtime.sm_state.register_pod(
        SCOPE, acquired["pod_id"], acquired["pod_sse_url"], template.deploy_ver(),
    )
    for sid in ("competitor-1", "competitor-2"):
        assert (await runtime.route(sid))["pod_id"] == acquired["pod_id"]
    _bound_placement(runtime, monkeypatch)
    if retry:
        register = runtime.sm_state.register_pod

        async def interrupted(scope, pod, url, ver):
            if pod != acquired["pod_id"]:
                raise ConnectionError("lost after acquire, before SM registration")
            await register(scope, pod, url, ver)

        monkeypatch.setattr(runtime.sm_state, "register_pod", interrupted)
        with pytest.raises(ConnectionError):
            await runtime.route("target", request_id="target-request")
        deployed = len(runtime.k8s.pods)
        monkeypatch.setattr(runtime.sm_state, "register_pod", register)
    result = await asyncio.wait_for(
        runtime.route("target", request_id="target-request"), timeout=2,
    )
    assert result["pod_id"] != acquired["pod_id"]
    if retry:
        assert len(runtime.k8s.pods) == deployed
    pod_count = len(runtime.k8s.pods)
    assert await runtime.route("target", request_id="target-request") == result
    assert len(runtime.k8s.pods) == pod_count
    for sid in ("competitor-1", "competitor-2"):
        assert (await runtime.route(sid))["pod_id"] == acquired["pod_id"]
    if refresh:
        # The old session keeps its binding across refresh and the new acquire.
        assert (await runtime.sm_state.session_hash("old"))["pod_id"] == old["pod_id"]


@requires_lua
async def test_route_reports_capacity_when_cached_acquire_cannot_progress(runtime, monkeypatch):
    await runtime.seed_template(scope_concurrency=6, pod_concurrency=2)
    # Direct orchestrator calls bypass handler idempotency; reuse the request ID
    # deliberately to exercise RM acquire's cached result for a now-full Pod.
    first = await runtime.route("competitor-1", request_id="target-request")
    await runtime.route("competitor-2")
    # Physical pool is full even though the SM candidate count has room.
    await runtime.rm_state.redis.hset(
        runtime.rm_state.k.scope_config(SCOPE), "max_pods", 1,
    )
    _bound_placement(runtime, monkeypatch)
    with pytest.raises(NoPodAvailable):
        await asyncio.wait_for(
            runtime.route("target", request_id="target-request"), timeout=2,
        )
    assert len(runtime.k8s.pods) == 1
    assert (await runtime.route("competitor-1"))["pod_id"] == first["pod_id"]
