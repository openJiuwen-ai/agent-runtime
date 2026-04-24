"""run_product_recommend_skill：理财产品推荐归一化脚本。

设计说明
--------
本脚本在沙箱 subprocess 中独立运行，不依赖 framework import。
通过环境变量 SKILL_INPUT 接收 JSON 输入，归一化后输出 JSON 到 stdout。

Rail 通过 sys_op.shell().execute_cmd() 调用本脚本完成归一化。
"""

from __future__ import annotations

import ast
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 产品列表解析与归一化
# ------------------------------------------------------------------


def parse_product_list_str(raw: Any) -> list[dict]:
    """
    解析 productList 字段，兼容三种格式：
    - Python repr 字符串（单引号，生产环境实际格式）
    - JSON 字符串（双引号）
    - list 对象（已解析）
    """
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, str):
        return []
    s = raw.strip()
    if not s:
        return []

    try:
        result = ast.literal_eval(s)
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
    except Exception:
        pass

    try:
        result = json.loads(s)
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
    except Exception:
        pass

    logger.warning(
        "parse_product_list_str: 无法解析 productList，原始值（前100字）: %s", s[:100]
    )
    return []


def normalize_product_filter_result(business_data: Any) -> dict:
    """
    将工作流 business_data 归一化为标准产品筛选结果 dict。

    输入：
        {
            "bankCardNumber": "6605",
            "productList": "[{'productCode': 'XLT1801', 'productName': '...', ...}]"
        }

    输出：
        {
            "products": [{"productCode": ..., "productName": ..., ...}, ...],
            "bankCardNumber": "6605",
            "total": 3
        }
    """
    if not isinstance(business_data, dict):
        logger.warning(
            "normalize_product_filter_result: business_data 不是 dict（类型=%s），返回空结果",
            type(business_data).__name__,
        )
        return {"products": [], "bankCardNumber": "", "total": 0}

    products = parse_product_list_str(business_data.get("productList", []))
    bank_card_number = str(business_data.get("bankCardNumber", ""))

    result = {
        "products": products,
        "bankCardNumber": bank_card_number,
        "total": len(products),
    }
    logger.info(
        "normalize_product_filter_result: total=%d bankCardNumber=%s",
        result["total"],
        result["bankCardNumber"],
    )
    return result


# ------------------------------------------------------------------
# 沙箱执行入口
# ------------------------------------------------------------------

if __name__ == "__main__":
    """沙箱入口：从 SKILL_INPUT 环境变量读取 business_data，归一化后输出 JSON 到 stdout。"""
    import os

    params = json.loads(os.environ.get("SKILL_INPUT", "{}"))
    business_data = params.get("business_data", {})
    normalized = normalize_product_filter_result(business_data)
    print(json.dumps(normalized, ensure_ascii=False))
