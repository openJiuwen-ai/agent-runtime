---
name: model_driven_fund_planning_skill
description: 模型驱动的理财卡资金汇聚与指定产品购买（通用工具版）
---

# model_driven_fund_planning_skill

本 Skill 由模型逐步调用 `call_versatile` 完成：
理财卡余额查询 → 默认卡余额查询 → 资金判断 → 必要时转账 → 购买。

## 工具白名单（严格）

只允许调用以下工具：
- `call_versatile`

禁止调用 `ask_user` 或其他非白名单工具。

## 固定参数

本 Skill 所有 `call_versatile` 调用的 `query_response_analysis_scripts` 参数固定为：
```
python model_driven_fund_planning_skill/scripts/run_fund_planning.py
```

## 输入槽位（结构化）

从用户请求和上文对话中提取并保持一致：
- `wealth_card_tail`：理财卡尾号（可空）
- `product_id`：产品ID
- `product_name`：产品名称
- `buy_amount`：购买金额（数字）

## 执行顺序

### 第一步：提取槽位（仅思考，不调工具）

从用户输入和上文对话中识别 `wealth_card_tail`、`product_id`、`product_name`、`buy_amount`。

### 第二步：查询理财卡余额

```
call_versatile(
  query_description="查询尾号为{wealth_card_tail}的卡的余额",
  query_intent="查询账户余额",
  query_response_analysis_scripts="python model_driven_fund_planning_skill/scripts/run_fund_planning.py"
)
```

工具返回结构：
```json
{
  "account_id": "6605",
  "bank_card_number": "6222021234566605",
  "balance": "80,000.00",
  "currency": "001",
  "balance_numeric": 80000.0
}
```

### 第三步：判断余额是否充足

- 若 `balance_numeric >= buy_amount`，跳到第六步直接购买。
- 若 `balance_numeric < buy_amount`，继续查询默认卡余额。

### 第四步：查询默认储蓄卡余额（仅余额不足时）

```
call_versatile(
  query_description="查余额",
  query_intent="查询账户余额",
  query_response_analysis_scripts="python model_driven_fund_planning_skill/scripts/run_fund_planning.py"
)
```

返回结构同第二步。根据返回的 `bank_card_number` 提取默认卡尾号。

判断逻辑：
- 若默认卡与理财卡是同一张卡 → 回复"只有一张卡，无法完成跨卡资金汇聚"，结束。
- 若两卡总额不足 → 回复"两张卡钱不够，无法完成本次购买"，结束。
- 若总额足够 → 计算缺口金额 = `buy_amount - wealth_balance_numeric`，进入转账。

### 第五步：转账（仅余额不足时）

```
call_versatile(
  query_description="从尾号{default_card_tail}的卡转账{gap_amount}元到尾号为{wealth_card_tail}的卡",
  query_intent="快速转账",
  query_response_analysis_scripts="python model_driven_fund_planning_skill/scripts/run_fund_planning.py"
)
```

返回结构：
```json
{
  "status": "success",
  "from_account": "3344",
  "to_account": "6605",
  "amount": 30000.0,
  "currency": "CNY",
  "transaction_id": "tx-001",
  "transfer_amount_str": "30000.00"
}
```

- 若 `status` 为 `"failed"` → 回复"转账失败，无法完成本次购买"，结束。
- 若 `status` 为 `"success"` → 继续购买。

### 第六步：购买理财

```
call_versatile(
  query_description="购买理财产品：产品名称：{product_name}，产品代码：{product_id}，金额：{buy_amount}元",
  query_intent="理财选品购买",
  query_response_analysis_scripts="python model_driven_fund_planning_skill/scripts/run_fund_planning.py"
)
```

返回结构：
```json
{
  "status": "success",
  "product_id": "XLT1801",
  "product_name": "添利宝",
  "amount": 50000,
  "buy_status": "购买成功",
  "fail_cause": "",
  "transaction_id": "wealth-tx-001"
}
```

- 若 `status` 为 `"success"` → 回复"购买成功"。
- 若 `status` 为 `"failed"` → 回复"购买失败"并附带 `fail_cause`。

## query_description 格式规范

| query_intent | query_description 格式 | 示例 |
|---|---|---|
| 查询账户余额 | `查询尾号为{卡尾号}的卡的余额` 或 `查余额`（默认卡） | `查询尾号为6605的卡的余额` |
| 快速转账 | `从尾号{付款卡尾号}的卡转账{金额}元到尾号为{收款卡尾号}的卡` | `从尾号3344的卡转账30000元到尾号为6605的卡` |
| 理财选品购买 | `购买理财产品：产品名称：{名称}，产品代码：{代码}，金额：{金额}元` | `购买理财产品：产品名称：添利宝，产品代码：XLT1801，金额：50000元` |

## 强约束

- 购买类请求禁止首步直接调用购买，必须先查余额。
- 每轮只调用一个工具，工具返回后再做下一步决策。
- query_description 必须严格按照上表格式填写，归一化脚本依赖该格式解析参数。
- 余额比较必须使用工具返回的 `balance_numeric`。
- 默认按自动资金汇聚执行，禁止向用户发起追问。

## 终态回复模板

- 缺槽位：`缺少必要信息（理财卡尾号/产品ID/金额），无法执行资金汇聚。`
- 只有一张卡：`只有一张卡，无法完成跨卡资金汇聚。`
- 两张卡钱不够：`两张卡钱不够，无法完成本次购买。`
- 转账失败：`转账失败，无法完成本次购买。`
- 购买失败：`购买失败`
