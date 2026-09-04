# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""实例管理 API（instance_routers）单元测试。"""

from __future__ import annotations

import uuid

import pytest

from conftest import ManagerApiHarness
from demo_payloads import instance_create_body

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_instance_create_list_get_patch_delete(manager_api: ManagerApiHarness):
    h = manager_api

    create_resp = await h.http.post(
        h.instances_url(),
        json=instance_create_body(jiuwenclaw_name="ut-instance-crud"),
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["data"]
    jid = created["jiuwenclaw_id"]
    parsed = uuid.UUID(jid)
    assert str(parsed) == jid

    get_after_create = await h.http.get(h.instances_url(f"/{jid}"))
    assert get_after_create.status_code == 200
    assert get_after_create.json()["data"]["jiuwenclaw_name"] == "ut-instance-crud"

    list_resp = await h.http.get(h.instances_url(), params={"page": 1, "page_size": 20})
    assert list_resp.status_code == 200
    items = list_resp.json()["data"]["items"]
    assert any(item["jiuwenclaw_id"] == jid for item in items)

    get_resp = await h.http.get(h.instances_url(f"/{jid}"))
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["jiuwenclaw_name"] == "ut-instance-crud"

    patch_resp = await h.http.patch(
        h.instances_url(f"/{jid}"),
        json={"description": "updated by ut", "jiuwenclaw_name": "ut-renamed"},
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()["data"]
    assert patched["description"] == "updated by ut"
    assert patched["jiuwenclaw_name"] == "ut-renamed"

    delete_resp = await h.http.delete(h.instances_url(f"/{jid}"))
    assert delete_resp.status_code == 200

    missing_resp = await h.http.get(h.instances_url(f"/{jid}"))
    assert missing_resp.status_code == 404


@pytest.mark.asyncio
async def test_instance_list_search(manager_api: ManagerApiHarness):
    h = manager_api
    create_resp = await h.http.post(
        h.instances_url(),
        json=instance_create_body(jiuwenclaw_name="ut-search-target"),
    )
    assert create_resp.status_code == 200
    jid = create_resp.json()["data"]["jiuwenclaw_id"]
    try:
        by_name = await h.http.get(
            h.instances_url(),
            params={"page": 1, "page_size": 50, "search": "ut-search-target"},
        )
        assert by_name.status_code == 200
        names = [item["jiuwenclaw_name"] for item in by_name.json()["data"]["items"]]
        assert "ut-search-target" in names

        by_id = await h.http.get(
            h.instances_url(),
            params={"page": 1, "page_size": 50, "search": jid[:8]},
        )
        assert by_id.status_code == 200
        ids = [item["jiuwenclaw_id"] for item in by_id.json()["data"]["items"]]
        assert jid in ids

        missing = await h.http.get(
            h.instances_url(),
            params={"page": 1, "page_size": 50, "search": "ut-search-not-exists-xyz"},
        )
        assert missing.status_code == 200
        assert missing.json()["data"]["items"] == []
    finally:
        await h.http.delete(h.instances_url(f"/{jid}"))


@pytest.mark.asyncio
async def test_instance_list_sort_by_name(manager_api: ManagerApiHarness):
    h = manager_api
    created_ids: list[str] = []
    try:
        for idx, name in enumerate(("ut-sort-aaa", "ut-sort-zzz")):
            base = 20000 + idx * 100
            create_resp = await h.http.post(
                h.instances_url(),
                json=instance_create_body(
                    jiuwenclaw_name=name,
                    gateway_config_host=f"http://127.0.0.1:{base}",
                    runtime_config_host=f"http://127.0.0.1:{base + 1}",
                ),
            )
            assert create_resp.status_code == 200
            created_ids.append(create_resp.json()["data"]["jiuwenclaw_id"])

        asc_resp = await h.http.get(
            h.instances_url(),
            params={
                "page": 1,
                "page_size": 50,
                "sort_by": "jiuwenclaw_name",
                "sort_order": "asc",
            },
        )
        assert asc_resp.status_code == 200
        asc_names = [
            item["jiuwenclaw_name"]
            for item in asc_resp.json()["data"]["items"]
            if item["jiuwenclaw_id"] in created_ids
        ]
        assert asc_names == sorted(asc_names)

        desc_resp = await h.http.get(
            h.instances_url(),
            params={
                "page": 1,
                "page_size": 50,
                "sort_by": "jiuwenclaw_name",
                "sort_order": "desc",
            },
        )
        assert desc_resp.status_code == 200
        desc_names = [
            item["jiuwenclaw_name"]
            for item in desc_resp.json()["data"]["items"]
            if item["jiuwenclaw_id"] in created_ids
        ]
        assert desc_names == sorted(desc_names, reverse=True)
    finally:
        for jid in created_ids:
            await h.http.delete(h.instances_url(f"/{jid}"))


@pytest.mark.asyncio
async def test_instance_health_probe(manager_api: ManagerApiHarness):
    resp = await manager_api.http.post(manager_api.instances_url("/health-probe"))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert {"probed", "alive", "dead", "skipped"} <= set(data)


@pytest.mark.asyncio
async def test_instance_get_not_found(manager_api: ManagerApiHarness):
    resp = await manager_api.http.get(
        manager_api.instances_url("/sp-does-not-exist"),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_instance_patch_not_found(manager_api: ManagerApiHarness):
    resp = await manager_api.http.patch(
        manager_api.instances_url("/sp-does-not-exist"),
        json={"description": "noop"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_instance_create_rejects_duplicate_gateway_config_host(
    manager_api: ManagerApiHarness,
):
    h = manager_api
    body = instance_create_body(jiuwenclaw_name="ut-host-dup-gateway-a")
    create_resp = await h.http.post(h.instances_url(), json=body)
    assert create_resp.status_code == 200
    jid = create_resp.json()["data"]["jiuwenclaw_id"]
    try:
        dup_resp = await h.http.post(
            h.instances_url(),
            json=instance_create_body(
                jiuwenclaw_name="ut-host-dup-gateway-b",
            ),
        )
        assert dup_resp.status_code == 400
        assert "gateway_config_host already in use" in dup_resp.json()["detail"]
    finally:
        await h.http.delete(h.instances_url(f"/{jid}"))


@pytest.mark.asyncio
async def test_instance_create_rejects_duplicate_runtime_config_host(
    manager_api: ManagerApiHarness,
):
    h = manager_api
    body = instance_create_body(jiuwenclaw_name="ut-host-dup-runtime-a")
    create_resp = await h.http.post(h.instances_url(), json=body)
    assert create_resp.status_code == 200
    jid = create_resp.json()["data"]["jiuwenclaw_id"]
    try:
        dup_body = instance_create_body(jiuwenclaw_name="ut-host-dup-runtime-b")
        dup_body["gateway_config_host"] = "http://127.0.0.1:28080"
        dup_resp = await h.http.post(h.instances_url(), json=dup_body)
        assert dup_resp.status_code == 400
        assert "runtime_config_host already in use" in dup_resp.json()["detail"]
    finally:
        await h.http.delete(h.instances_url(f"/{jid}"))


@pytest.mark.asyncio
async def test_instance_create_allows_cross_column_config_host(
    manager_api: ManagerApiHarness,
):
    h = manager_api
    body = instance_create_body(jiuwenclaw_name="ut-host-cross-col-a")
    create_resp = await h.http.post(h.instances_url(), json=body)
    assert create_resp.status_code == 200
    first_jid = create_resp.json()["data"]["jiuwenclaw_id"]
    existing_gateway = body["gateway_config_host"]
    second_jid = ""
    try:
        dup_body = instance_create_body(jiuwenclaw_name="ut-host-cross-col-b")
        dup_body["gateway_config_host"] = "http://127.0.0.1:28080"
        dup_body["runtime_config_host"] = existing_gateway
        dup_resp = await h.http.post(h.instances_url(), json=dup_body)
        assert dup_resp.status_code == 200
        second_jid = dup_resp.json()["data"]["jiuwenclaw_id"]
    finally:
        await h.http.delete(h.instances_url(f"/{first_jid}"))
        if second_jid:
            await h.http.delete(h.instances_url(f"/{second_jid}"))


@pytest.mark.asyncio
async def test_instance_update_rejects_duplicate_config_host(
    manager_api: ManagerApiHarness,
):
    h = manager_api
    first_body = instance_create_body(jiuwenclaw_name="ut-host-update-a")
    first_resp = await h.http.post(h.instances_url(), json=first_body)
    assert first_resp.status_code == 200
    first_jid = first_resp.json()["data"]["jiuwenclaw_id"]

    second_body = instance_create_body(jiuwenclaw_name="ut-host-update-b")
    second_body["gateway_config_host"] = "http://127.0.0.1:38080"
    second_body["runtime_config_host"] = "http://127.0.0.1:38081"
    second_resp = await h.http.post(h.instances_url(), json=second_body)
    assert second_resp.status_code == 200
    second_jid = second_resp.json()["data"]["jiuwenclaw_id"]
    try:
        patch_resp = await h.http.patch(
            h.instances_url(f"/{second_jid}"),
            json={"gateway_config_host": first_body["gateway_config_host"]},
        )
        assert patch_resp.status_code == 400
        assert "gateway_config_host already in use" in patch_resp.json()["detail"]

        keep_resp = await h.http.patch(
            h.instances_url(f"/{second_jid}"),
            json={
                "gateway_config_host": second_body["gateway_config_host"] + "/",
            },
        )
        assert keep_resp.status_code == 200
    finally:
        await h.http.delete(h.instances_url(f"/{first_jid}"))
        await h.http.delete(h.instances_url(f"/{second_jid}"))
