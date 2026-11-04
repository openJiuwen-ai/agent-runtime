"""Expert package dependencies survive Agent publication and template export."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from openpyxl import load_workbook

from conftest import ManagerApiHarness
from demo_payloads import expert_package_templates


@pytest.mark.asyncio
async def test_expert_package_published_with_agent_and_exported_as_dependency(
    manager_api: ManagerApiHarness,
    monkeypatch: pytest.MonkeyPatch,
):
    h = manager_api
    monkeypatch.setattr(
        "manager_server.core.instance_resource.runtime_config_sync.runtime_request",
        AsyncMock(return_value={"ok": True}),
    )
    _, expert_body = expert_package_templates()[0]
    expert = await h.post_json("/expert-package-templates", expert_body)
    expert_id = expert["template_id"]
    template_ref = {"expert_package": [expert_id]}

    response = await h.http.post(
        "/api/v1/agent-templates/",
        json={"template_name": "Expert-enabled Agent", "template_ref": template_ref},
    )
    assert response.status_code == 200, response.text
    agent_id = response.json()["data"]["template_id"]
    assert response.json()["data"]["template_ref"] == template_ref
    expert = await h.get_json(f"/expert-package-templates/{expert_id}")
    assert expert["reference_count"] == 1

    jid = await h.create_instance()
    await h.handler.update("instance_info", {"jiuwenclaw_id": jid}, {"gateway_status": "online"})
    h.gateway_sim.calls.clear()
    response = await h.http.post(
        f"/api/v1/instances/{jid}/agent-resources",
        json={"ref_template_id": agent_id, "resource_name": "Expert Agent", "match_exprs": [[]]},
    )
    assert response.status_code == 200, response.text

    publications = [
        (path, body) for instance_id, method, path, body in h.gateway_sim.calls
        if instance_id == jid and method == "POST"
    ]
    expert_index = next(
        i for i, (path, _) in enumerate(publications) if path == "/api/v1/expert-package-templates"
    )
    agent_index = next(
        i for i, (path, _) in enumerate(publications) if path == "/api/v1/agent-templates"
    )
    assert expert_index < agent_index
    assert publications[expert_index][1]["template_id"] == expert_id
    assert publications[expert_index][1]["packages"] == expert_body["packages"]
    assert publications[agent_index][1]["template_ref"] == template_ref

    exported = await h.http.post(
        "/api/v1/import-export/agent-template/export", json={"resource_ids": [agent_id]},
    )
    assert exported.status_code == 200, exported.text
    workbook = load_workbook(BytesIO(exported.content))
    sheet = workbook["29_ExpertPackageTemplates"]
    headers = [str(cell.value or "") for cell in sheet[1]]
    row = dict(zip(headers, [cell.value for cell in sheet[2]]))
    assert row["template_id"] == expert_id
    extras = workbook["90_ExtraFields"]
    extra_headers = [str(cell.value or "") for cell in extras[1]]
    exported_fields = {
        row["field_path"]: row["value"]
        for cells in list(extras.rows)[1:]
        if (row := dict(zip(extra_headers, [cell.value for cell in cells])))["object_type"]
        == "expert_package_template" and row["object_id"] == expert_id
    }
    assert exported_fields == {
        f"packages[{index}].{key}": value
        for index, package in enumerate(expert_body["packages"])
        for key, value in package.items()
    }

    updated_packages = [{**expert_body["packages"][0], "version": "1.1.0"}]
    await h.patch_json(f"/expert-package-templates/{expert_id}", {"packages": updated_packages})
    updates = [
        body for instance_id, method, path, body in h.gateway_sim.calls
        if instance_id == jid and method == "PATCH"
        and path == f"/api/v1/expert-package-templates/{expert_id}"
    ]
    assert updates[-1]["packages"] == updated_packages

    deleted = await h.http.delete(h.templates_url(f"/expert-package-templates/{expert_id}"))
    assert deleted.status_code == 400
    assert (await h.get_json(f"/expert-package-templates/{expert_id}"))["reference_count"] == 1
