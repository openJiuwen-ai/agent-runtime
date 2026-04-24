---
name: rebuild_product_recommend_skill
description: >
  根据用户理财意图进入理财推荐流程并展示推荐产品列表。
  触发词：推荐理财产品、帮我看看理财、有什么理财可以买。
  不要用于：产品选择确认、资金筹划、账户查询。
---

# 产品推荐 Skill

## 职责

接收用户的理财购买意向，通过 `call_versatile` 触发理财推荐工作流，
获取推荐产品列表，按清晰格式展示给用户，并告知用户可进入选品流程。

## 工具白名单（严格）

只允许调用以下工具：
- `call_versatile`

禁止调用 `ask_user` 或其他非白名单工具。

## 固定参数

本 Skill 所有 `call_versatile` 调用的固定参数：
- `query_intent`：`"理财推荐"`
- `query_response_analysis_scripts`：`"python rebuild_product_recommend_skill/scripts/run_product_recommend_skill.py"`

## 执行流程

### 第一步：提取用户偏好（仅思考，不调工具）

从用户输入中识别：
- 风险偏好：保守 → R1/R2，稳健 → R2/R3，进取 → R3/R4
- 产品类型：固收类 / 混合类 / 权益类
- 如无明确偏好，不传任何过滤参数

### 第二步：调用 `call_versatile` 触发推荐工作流

```
call_versatile(
  query_description="推荐理财产品，关键词：固收，风险等级：R2",
  query_intent="理财推荐",
  query_response_analysis_scripts="python rebuild_product_recommend_skill/scripts/run_product_recommend_skill.py"
)
```

参数说明：
- `query_description`：自然语言查询，根据用户偏好拼装。如无偏好，使用 `"推荐理财产品"`。
- `query_intent`：固定为 `"理财推荐"`
- `query_response_analysis_scripts`：固定为 `"python rebuild_product_recommend_skill/scripts/run_product_recommend_skill.py"`

工具返回结构：
```json
{
  "products": [
    {
      "productCode": "XLT1801",
      "productName": "工银理财「添利宝」净值型理财产品(XLT1801)",
      "productType": "固定收益类",
      "profitValue": "3.2%",
      "riskLevel": "R2"
    }
  ],
  "bankCardNumber": "6605",
  "total": 3
}
```

### 第三步：处理返回结果

**若 products 为空（total == 0）：**
```
抱歉，暂无符合您条件的理财产品，请调整筛选条件后重试。
```
结束，不需要调用其他工具。

**若 products 不为空但 bankCardNumber 为空：**
```
已查询到 {total} 款理财产品，但未获取到您的理财卡信息，不符合购买要求。
请先绑定理财卡后再尝试。
```
结束，不需要调用其他工具。

**若 products 不为空且 bankCardNumber 不为空：**
按下方格式展示产品列表（最多展示前 5 条）：

---
为您推荐以下理财产品：

| 序号 | 产品名称 | 产品类型 | 预期年化收益 | 风险等级 |
|------|----------|----------|-------------|----------|
| 1 | {productName} | {productType} | {profitValue} | {riskLevel} |
| 2 | ... | ... | ... | ... |

您的理财卡尾号：{bankCardNumber}

如需购买，请告诉我您想选择哪款产品及购买金额。
---

## 字段说明

| 字段 | 说明 |
|------|------|
| productCode | 产品代码（内部唯一标识，选品时需要） |
| productName | 产品全名 |
| productType | 产品类型：固定收益类 / 混合类 / 权益类 |
| profitValue | 预期年化收益率，如 3.2% |
| riskLevel | 风险等级：R1（最低）~ R5（最高） |
| bankCardNumber | 用户理财卡后四位，如 6605 |

## 约束

- 禁止自行编造产品信息，只展示 `call_versatile` 返回的真实数据。
- 推荐列表超过 5 条时截取前 5 条，末尾加提示"（共 {total} 个产品，已为您展示前 5 条）"。
- 不要替用户做出选择，那是 product_select_skill 的职责。
- 每次执行只调用一次 `call_versatile`，不重复调用。
