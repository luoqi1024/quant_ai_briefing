# Quant AI Briefing

中文 | [English](README.md)

Quant AI Briefing 是一个用于个人投资影子记账和每日 AI 简报的小型 Python 服务。它会把定投规则、影子交易和行情快照存入 SQLite，抓取公开行情，计算持仓成本、市值、今日盈亏和累计浮动盈亏，然后调用兼容 OpenAI Chat Completions 的接口生成 Markdown 日报，并可通过企业微信自建应用推送。

本项目不会连接券商交易接口，也不会执行真实交易。它适合用于个人复盘、资产记录和组合监控。

## 功能

- 使用 SQLite 存储定投规则、影子交易和行情快照。
- 支持日、周、月频率的规则触发。
- 对同一天生成的影子交易做去重保护。
- 计算持仓成本、市值、今日盈亏、累计浮动盈亏和盈亏率。
- 支持美股、国内基金/ETF、黄金和部分热门观察资产的公开行情源。
- 生成 AI Markdown 投资日报，AI 不可用时提供本地兜底报告。
- 支持企业微信 Markdown 推送，并按企业微信字节限制自动拆分长消息。
- 支持 `--dry-run` 本地测试，不需要真实 AI 和推送凭据。

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 配置

复制环境变量示例文件：

```bash
cp .env.example .env
```

环境变量说明：

| 变量 | 说明 |
| --- | --- |
| `DATABASE_PATH` | SQLite 数据库路径，默认 `quant_data.db`。 |
| `LOG_LEVEL` | Python 日志等级，默认 `INFO`。 |
| `AI_API_KEY` | 兼容 OpenAI Chat Completions 接口的 API Key。 |
| `AI_URL` | Chat Completions 请求地址，例如 `https://api.deepseek.com/chat/completions`。 |
| `AI_MODEL` | 模型名称，例如 `deepseek-v4-flash`。 |
| `WECOM_CORPID` | 企业微信企业 ID。 |
| `WECOM_AGENTID` | 企业微信自建应用 Agent ID。 |
| `WECOM_SECRET` | 企业微信自建应用 Secret。 |

旧的 `XIAOMI_AI_API_KEY`、`XIAOMI_AI_URL`、`XIAOMI_AI_MODEL` 仍然兼容，但新部署建议使用通用的 `AI_*` 变量名。

不要提交 `.env`、SQLite 数据库、日志文件或个人持仓数据。仓库默认已经通过 `.gitignore` 忽略这些文件。

## 运行

使用模拟行情本地试跑，不调用 AI，也不推送：

```bash
python -m src.main --dry-run
```

生成真实报告，但只在终端打印：

```bash
python -m src.main
```

生成报告并通过企业微信推送：

```bash
python -m src.main --send
```

指定日期运行：

```bash
python -m src.main --date 2026-05-07 --dry-run
```

## 导入个人组合

应用会初始化两条简单示例规则。接入自己的组合时，可以直接维护 SQLite，也可以基于 `scripts/import_portfolio_csv.py` 从 CSV 导入自己整理后的数据。

CSV 需要包含以下列：

```text
asset_name,asset_code,market_type,currency,rule_amount,freq_type,freq_value,enabled,trade_date,amount,price,shares
```

导入前建议先备份数据库。真实金额、账户截图、交易流水和个人资产结构都不应提交到公开仓库。

## 部署示例

每天 08:00 推送的 cron 示例：

```cron
0 8 * * * cd /opt/quant-ai-briefing && /usr/bin/flock -n /tmp/quant-ai-briefing.lock /opt/quant-ai-briefing/.venv/bin/python -m src.main --send >> /opt/quant-ai-briefing/cron.log 2>&1
```

实际部署时请根据服务器路径调整命令。

## 测试

```bash
pytest -q
```

## 免责声明

本项目生成的报告仅用于个人复盘，不构成投资建议。公开行情源可能存在延迟、不可用或数据误差。涉及真实决策前，请独立核对关键数据。
