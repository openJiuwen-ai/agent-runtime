# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Clearly labelled local enterprise IdP simulator for the runnable example."""

from __future__ import annotations

import html

from fastapi import FastAPI
from fastapi.responses import HTMLResponse


class DemoEnterpriseIdentityProvider:
    """Render a local form that occupies the upstream SAML IdP adapter slot."""

    @staticmethod
    def mount(fastapi: FastAPI) -> None:
        @fastapi.get(
            "/demo-enterprise-idp/login",
            response_class=HTMLResponse,
            tags=["federation-demo"],
        )
        async def login(connection_id: str, authorization_request_id: str):
            safe_connection = html.escape(connection_id, quote=True)
            safe_request = html.escape(authorization_request_id, quote=True)
            return HTMLResponse(
                f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Enterprise Demo IdP</title>
<style>
body{{font-family:system-ui;max-width:460px;margin:60px auto;color:#222}}
.card{{border:1px solid #ddd;border-radius:12px;padding:24px}}
label,input,button{{display:block;width:100%;box-sizing:border-box}}
input{{padding:10px;margin:6px 0 14px}}button{{padding:11px}}
.warning{{background:#fff3cd;padding:10px;border-radius:6px;font-size:14px}}
</style></head><body><div class="card">
<h2>Enterprise Demo IdP</h2>
<p class="warning">Local simulation only. No SAML XML is accepted or verified here.</p>
<form method="post" action="/auth/federation/{safe_connection}/callback">
<input type="hidden" name="authorization_request_id" value="{safe_request}">
<label>Employee ID<input name="employee_id" value="employee-10086" required></label>
<label>Display name<input name="display_name" value="Enterprise Alice" required></label>
<label>Email<input name="email" value="alice@enterprise.example"></label>
<button type="submit">Enterprise sign in</button></form>
</div></body></html>"""
            )
