"""
Simulate API routes
API routes for the Simulate System
"""

import os
from fastapi import APIRouter
from fastapi.responses import HTMLResponse


# Create simulate router
router = APIRouter()

# 定义 index.html 的路径（当前目录下）
INDEX_HTML_PATH = os.path.join(os.path.dirname(__file__), "index.html")


@router.get("/gh", response_class=HTMLResponse)
async def root():
    if os.path.exists(INDEX_HTML_PATH):
        with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Simulate HTML not found.</h1>"
