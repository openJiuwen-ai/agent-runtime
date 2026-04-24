"""run_fund_planning: sandbox normalization script for fund planning skill.

Design
------
Runs independently in sandbox subprocess, no framework imports.
Receives JSON via SKILL_INPUT env var, normalizes and outputs JSON to stdout.

Rail calls this script via sys_op.shell().execute_cmd().

SKILL_INPUT format (from call_versatile):
{
    "query_intent": "查询账户余额" | "快速转账" | "理财选品购买",
    "query_description": "查询尾号为6605的卡的余额",
    "business_data": { ... }
}
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


# ------------------------------------------------------------------
# Utility functions
# ------------------------------------------------------------------

def parse_balance_thousands(balance_str: str) -> float:
    s = str(balance_str or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def to_money_decimal(value: Any) -> Decimal:
    try:
        dec = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        dec = Decimal("0")
    return dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_money_decimal(value: Any) -> str:
    return str(to_money_decimal(value))


# ------------------------------------------------------------------
# query_description parsers
# ------------------------------------------------------------------

def _parse_account_tail(desc: str) -> str:
    m = re.search(r"尾号(?:为)?(\d{4})", desc)
    return m.group(1) if m else ""


def _parse_transfer_desc(desc: str) -> tuple:
    from_m = re.search(r"(?:从|付款)尾号(?:为)?(\d{4})", desc)
    to_m = re.search(r"到尾号(?:为)?(\d{4})", desc)
    amount_m = re.search(r"转账([\d.]+)元", desc)
    return (
        from_m.group(1) if from_m else "",
        to_m.group(1) if to_m else "",
        float(amount_m.group(1)) if amount_m else 0.0,
    )


def _parse_purchase_desc(desc: str) -> tuple:
    code_m = re.search(r"产品代码[：:]([^，,]+)", desc)
    amount_m = re.search(r"金额[：:]([\d.]+)元?", desc)
    return (
        code_m.group(1).strip() if code_m else "",
        float(amount_m.group(1)) if amount_m else 0.0,
    )


# ------------------------------------------------------------------
# Normalization functions
# ------------------------------------------------------------------

def normalize_balance_result(data: dict[str, Any], account_id: str) -> dict[str, Any]:
    card_no = ""
    balance = ""
    currency = ""

    bank_list = data.get("bankCardBalanceList")
    if isinstance(bank_list, list) and bank_list:
        first = bank_list[0] if isinstance(bank_list[0], dict) else {}
        card_no = str(first.get("bankCardNumber", ""))
        c_list = first.get("currencyBalanceList", [])
        if isinstance(c_list, list):
            for entry in c_list:
                if not isinstance(entry, dict):
                    continue
                code = str(entry.get("currencyCode", ""))
                if code == "001":
                    balance = str(entry.get("balance", ""))
                    currency = "001"
                    break
            if not balance and c_list and isinstance(c_list[0], dict):
                balance = str(c_list[0].get("balance", ""))
                currency = str(c_list[0].get("currencyCode", "") or "001")

    if not balance:
        response_data = data.get("responseData", [])
        if isinstance(response_data, list):
            for item in response_data:
                page = item.get("pageData") if isinstance(item, dict) else None
                if not isinstance(page, dict):
                    continue
                bank_data = page.get("bankBalanceData", [])
                if not isinstance(bank_data, list):
                    continue
                for entry in bank_data:
                    if not isinstance(entry, dict):
                        continue
                    bal_list = entry.get("balanceList", [])
                    if not isinstance(bal_list, list):
                        continue
                    for bal in bal_list:
                        if not isinstance(bal, dict):
                            continue
                        bal_obj = bal.get("balance", {})
                        title = bal.get("balanceTitle", {})
                        title_name = str(title.get("titleValue", "")) if isinstance(title, dict) else ""
                        val = str(bal_obj.get("titleValue", "")) if isinstance(bal_obj, dict) else ""
                        if val and ("人民币余额" in title_name or not balance):
                            balance = val

    return {
        "account_id": account_id,
        "bank_card_number": card_no,
        "balance": balance or "0.00",
        "currency": currency or "001",
        "balance_numeric": parse_balance_thousands(balance or "0"),
    }


def normalize_transfer_result(
    data: dict[str, Any], from_account: str, to_account: str, amount: Any
) -> dict[str, Any]:
    status = str(data.get("transferStatus", "success")).lower()
    ok = status in {"success", "1", "true"}
    transfer_amount_raw = data.get("transferAmount", amount)
    transfer_amount = float(to_money_decimal(transfer_amount_raw))
    return {
        "status": "success" if ok else "failed",
        "from_account": from_account,
        "to_account": to_account,
        "amount": transfer_amount,
        "currency": "CNY",
        "transaction_id": data.get("transactionId", ""),
        "payer_card": data.get("payerCardNumber", ""),
        "payee_card": data.get("payeeCardNumber", ""),
        "transfer_amount_str": format_money_decimal(transfer_amount_raw),
    }


def normalize_purchase_result(
    data: dict[str, Any], product_id: str, amount: float
) -> dict[str, Any]:
    inner: dict[str, Any] = {}
    if isinstance(data.get("productBuyResponse"), dict):
        inner = data
    elif isinstance(data.get("data"), dict) and isinstance(data["data"].get("text"), str):
        try:
            parsed = json.loads(data["data"]["text"])
            inner = parsed if isinstance(parsed, dict) else {}
        except Exception:
            inner = {}
    elif isinstance(data, dict):
        inner = data

    resp = inner.get("productBuyResponse") if isinstance(inner, dict) else None
    if not isinstance(resp, dict):
        resp = {}

    status_raw = str(resp.get("buyStatus", ""))
    ok = status_raw in {"1", "true", "success", "购买成功"}
    return {
        "status": "success" if ok else "failed",
        "product_id": str(resp.get("productCode", product_id)),
        "product_name": str(resp.get("productName", "")),
        "amount": amount,
        "buy_status": status_raw,
        "fail_cause": str(resp.get("failCause", "")),
        "transaction_id": str(resp.get("transactionId", "")),
    }


# ------------------------------------------------------------------
# Sandbox entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import sys

    raw_input = os.environ.get("SKILL_INPUT", "{}")
    params = json.loads(raw_input)

    query_intent = params.get("query_intent", "")
    business_data = params.get("business_data", {})
    query_description = params.get("query_description", "")

    if query_intent == "查询账户余额":
        account_id = _parse_account_tail(query_description)
        result = normalize_balance_result(business_data, account_id)
    elif query_intent == "快速转账":
        from_acc, to_acc, amount = _parse_transfer_desc(query_description)
        result = normalize_transfer_result(business_data, from_acc, to_acc, amount)
    elif query_intent == "理财选品购买":
        product_id, amount = _parse_purchase_desc(query_description)
        result = normalize_purchase_result(business_data, product_id, amount)
    else:
        print(json.dumps({"error": f"Unknown query_intent: {query_intent}"}), file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))
