"""AI report generation with OpenAI-compatible chat completions."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import Settings, load_settings


logger = logging.getLogger(__name__)

DAILY_SYSTEM_PROMPT = (
    "你是一位理性、克制但有人情味的个人投资复盘助手。"
    "请根据用户提供的账户快照和热门资产观察池，生成一份 Markdown 投资日报。"
    "目标长度 700 到 1100 个中文字符，语气像熟悉用户投资习惯的朋友：温和、具体、不空泛。"
    "报告必须包含：1. 今日账户总览；2. 逐项持仓明细表；3. 主要持仓贡献和拖累；"
    "4. 热门投资方式涨跌表；5. 结合观察池给出市场评价；6. 给出 2 到 4 条纪律性建议或明日观察点。"
    "持仓明细表必须逐一列出 positions 中的每项资产，至少包含资产名称、市值、成本、今日涨跌、"
    "今日盈亏 daily_pnl、累计盈亏 floating_pnl、累计盈亏率 floating_pnl_pct。"
    "positions 是持仓事实的唯一来源。asset_name 必须逐字原样使用，禁止缩写、改名、脱敏或根据 asset_code 猜测品种；"
    "如果正文使用 asset_code，同一行必须同时出现对应的完整 asset_name。"
    "输入中的资产名称已经获准用于报告，禁止出现‘名称脱敏’或类似占位文字。"
    "market_type 只是市场类型；average_cost 是平均成本价，valuation_price 是当前估值价，二者绝不能混淆。"
    "price 与 valuation_price 含义相同；除非明确引用 average_cost，否则不得自行推导或描述成本价。"
    "positions 的持仓估值与 market_context 的观察池行情来自不同用途；来源不同的报价不得相互替代或直接当作同一价格比较。"
    "如果 daily_pnl 为空，要写“暂无”，不要虚构。金额统一保留两位小数。"
    "daily_data_status 为 manual_reconcile_without_daily_change 时，只能说明手工对账未提供单日涨跌，"
    "不得据此断言数据源故障，也不得建议用户手动补行情。"
    "如果 market_context.is_sufficient 为 false，必须明确写出‘有效行情样本不足’，"
    "不得据此判断市场整体方向，也不得基于该观察池提出针对具体持仓的调整建议。"
    "market_context 中 status 为 historical_snapshot 的行情必须明确标注‘历史快照’并写出 quote_date，"
    "不得描述成今日实时行情。"
    "建议只能是复盘、风险控制、仓位纪律、定投纪律和观察提醒，不能给确定性的买卖指令。"
    "不得建议加仓、减仓、清仓或合并某一具体持仓。"
    "如果缺少新闻或宏观事件，只能基于涨跌和组合表现做谨慎判断，并明确使用“可能”“倾向于”等表述。"
)

WEEKLY_SYSTEM_PROMPT = (
    "你是一位理性、克制但有人情味的个人投资复盘助手。"
    "请根据用户提供的周报数据，生成一份 Markdown 投资周报。"
    "目标长度 900 到 1400 个中文字符，写成周总结，而不是把日报改个标题。"
    "报告必须包含：1. 本周区间与组合总览；2. 期末持仓明细表；3. 本周主要贡献和拖累；"
    "4. 热门投资方式观察；5. 对本周组合变化的评价；6. 下周观察点与纪律提醒。"
    "如果提供了 baseline_date 和 weekly_changes_by_currency，需要明确写出相对上周基线的市值变化与浮盈变化。"
    "如果 baseline_date 为空，必须明确说明“缺少上周基线，本周仅展示期末快照，不展示周环比”。"
    "持仓明细表至少包含资产名称、期末市值、成本、本周浮盈变化、累计浮盈、累计浮盈率。"
    "positions 是持仓事实的唯一来源，asset_name 必须逐字原样使用，禁止缩写、改名、脱敏或根据 asset_code 猜测品种。"
    "average_cost 是平均成本价，valuation_price 是当前估值价，二者绝不能混淆。"
    "如果 market_context.is_sufficient 为 false，必须明确写出‘有效行情样本不足’，且不得据此判断市场方向。"
    "market_context 中 status 为 historical_snapshot 的行情必须明确标注‘历史快照’并写出 quote_date。"
    "不要虚构周内新闻、政策或事件；没有可靠信息时，只能基于组合表现和观察池涨跌做谨慎判断。"
    "建议部分仍然只能给复盘、节奏、风险控制和观察提醒，不能给确定性的买卖指令。"
)


class AIReportError(RuntimeError):
    """Raised when the AI report cannot be generated in strict mode."""


class AIReporter:
    """Generate Markdown briefings with an optional local fallback."""

    def __init__(
        self,
        settings: Settings | None = None,
        timeout: float = 30.0,
        retries: int = 2,
        session: requests.Session | None = None,
        fallback_on_failure: bool = False,
    ) -> None:
        self.settings = settings or load_settings()
        self.timeout = timeout
        self.session = session or _build_retry_session(retries)
        self.fallback_on_failure = fallback_on_failure

    def generate_report(
        self,
        snapshot: dict[str, Any],
        *,
        report_kind: str = "daily",
    ) -> str:
        """Generate a report, optionally falling back to local Markdown."""

        if report_kind not in {"daily", "weekly"}:
            raise AIReportError(f"Unsupported report kind: {report_kind}")

        snapshot_error = _snapshot_validation_error(snapshot)
        if snapshot_error:
            raise AIReportError(f"Snapshot validation failed: {snapshot_error}")

        if not self._configured():
            return self._handle_failure("AI settings are incomplete", snapshot, report_kind)

        payload = {
            "model": self.settings.resolved_ai_model,
            "messages": [
                {"role": "system", "content": _system_prompt(report_kind)},
                {
                    "role": "user",
                    "content": json.dumps(snapshot, ensure_ascii=False),
                },
            ],
            "temperature": 0.2,
        }
        try:
            response = self.session.post(
                self.settings.resolved_ai_url,
                headers={
                    "Authorization": f"Bearer {self.settings.resolved_ai_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            report = str(content).strip()
            if not report:
                return self._handle_failure(
                    "AI report API returned empty content",
                    snapshot,
                    report_kind,
                )
            validation_error = _report_validation_error(report, snapshot)
            if validation_error:
                return self._handle_failure(
                    f"AI report validation failed: {validation_error}",
                    snapshot,
                    report_kind,
                )
            return report
        except AIReportError:
            raise
        except Exception as exc:  # noqa: BLE001 - remote APIs can fail many ways.
            return self._handle_failure(f"AI report API failed: {exc}", snapshot, report_kind)

    def _configured(self) -> bool:
        return all(
            [
                self.settings.resolved_ai_api_key,
                self.settings.resolved_ai_url,
                self.settings.resolved_ai_model,
            ]
        )

    def _handle_failure(
        self,
        message: str,
        snapshot: dict[str, Any],
        report_kind: str,
    ) -> str:
        if self.fallback_on_failure:
            logger.warning("%s; using fallback report", message)
            return self._fallback_report(snapshot, report_kind=report_kind)
        raise AIReportError(message)

    def _fallback_report(self, snapshot: dict[str, Any], *, report_kind: str) -> str:
        if report_kind == "weekly":
            return self._fallback_weekly_report(snapshot)
        return self._fallback_daily_report(snapshot)

    def _fallback_daily_report(self, snapshot: dict[str, Any]) -> str:
        totals = snapshot.get("totals_by_currency", {})
        run_date = snapshot.get("run_date")
        if not totals:
            return f"## 投资日报\n\n{run_date} 暂无持仓或可计算盈亏。"

        lines = ["## 投资日报", "", "### 今日总览", f"{run_date} 的账户快照如下："]
        for currency, values in totals.items():
            lines.append(
                f"- {currency}: 成本 {values['cost']:.2f}，市值 {values['market_value']:.2f}，"
                f"浮动盈亏 {values['floating_pnl']:+.2f}"
            )

        positions = snapshot.get("positions", [])
        if positions:
            lines.extend(
                [
                    "",
                    "### 持仓明细",
                    "| 资产 | 市值 | 成本 | 日涨跌 | 今日盈亏 | 累计盈亏 | 累计盈亏率 |",
                    "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
                ]
            )
            for item in positions:
                lines.append(
                    "| {asset} | {mv:.2f} | {cost:.2f} | {change} | {daily} | {pnl:+.2f} | {pnl_pct:+.2f}% |".format(
                        asset=item.get("asset_name", item.get("asset_code")),
                        mv=float(item.get("market_value", 0)),
                        cost=float(item.get("cost", 0)),
                        change=_pct_text(item.get("change_pct")),
                        daily=_money_text(item.get("daily_pnl")),
                        pnl=float(item.get("floating_pnl", 0)),
                        pnl_pct=float(item.get("floating_pnl_pct", 0)),
                    )
                )

            best = max(positions, key=lambda item: item.get("floating_pnl", 0))
            worst = min(positions, key=lambda item: item.get("floating_pnl", 0))
            lines.extend(
                [
                    "",
                    "### 持仓复盘",
                    f"- 当前贡献较多的是 {best.get('asset_name', best.get('asset_code'))}，"
                    f"累计浮动盈亏 {float(best.get('floating_pnl', 0)):+.2f}。",
                    f"- 当前拖累较明显的是 {worst.get('asset_name', worst.get('asset_code'))}，"
                    f"累计浮动盈亏 {float(worst.get('floating_pnl', 0)):+.2f}。",
                ]
            )

        _append_market_context(lines, snapshot)
        lines.extend(
            [
                "",
                "### 观察与建议",
                "- 先看每项持仓的投入、日内波动和累计盈亏，再判断组合是否仍符合你的长期配置逻辑。",
                "- 定投类资产建议继续按既定规则复盘，避免因为一天的涨跌临时改变节奏。",
                "- 对波动较大的方向，可以重点观察连续几天的变化，而不是只看一次日报。",
                "",
                "数据仅用于影子记账复盘，不构成具体买卖建议。",
            ]
        )
        return "\n".join(lines)

    def _fallback_weekly_report(self, snapshot: dict[str, Any]) -> str:
        week_start = snapshot.get("week_start")
        week_end = snapshot.get("week_end")
        if not snapshot.get("has_week_data"):
            return (
                "## 投资周报\n\n"
                f"统计区间：{week_start} 至 {week_end}\n\n"
                "本周没有可用的日报快照，暂时无法生成完整周报。"
            )

        lines = [
            "## 投资周报",
            "",
            "### 本周总览",
            f"统计区间：{week_start} 至 {week_end}",
            f"期末快照日期：{snapshot.get('snapshot_date')}",
        ]
        for currency, values in snapshot.get("totals_by_currency", {}).items():
            change = (snapshot.get("weekly_changes_by_currency") or {}).get(currency, {})
            market_value_change = _money_text(change.get("market_value_change"))
            floating_pnl_change = _money_text(change.get("floating_pnl_change"))
            lines.append(
                f"- {currency}: 期末市值 {values['market_value']:.2f}，期末浮盈 {values['floating_pnl']:+.2f}，"
                f"本周市值变化 {market_value_change}，本周浮盈变化 {floating_pnl_change}"
            )

        if not snapshot.get("baseline_date"):
            lines.append("- 缺少上周基线，本周仅展示期末快照，不展示周环比。")

        positions = snapshot.get("positions", [])
        if positions:
            lines.extend(
                [
                    "",
                    "### 期末持仓明细",
                    "| 资产 | 期末市值 | 成本 | 本周浮盈变化 | 累计浮盈 | 累计浮盈率 |",
                    "| --- | ---: | ---: | ---: | ---: | ---: |",
                ]
            )
            for item in positions:
                lines.append(
                    "| {asset} | {mv:.2f} | {cost:.2f} | {weekly} | {pnl:+.2f} | {pnl_pct:+.2f}% |".format(
                        asset=item.get("asset_name", item.get("asset_code")),
                        mv=float(item.get("market_value", 0)),
                        cost=float(item.get("cost", 0)),
                        weekly=_money_text(item.get("weekly_floating_pnl_change")),
                        pnl=float(item.get("floating_pnl", 0)),
                        pnl_pct=float(item.get("floating_pnl_pct", 0)),
                    )
                )

        contributor = snapshot.get("top_contributor")
        detractor = snapshot.get("top_detractor")
        if contributor or detractor:
            lines.extend(["", "### 本周贡献与拖累"])
            if contributor:
                lines.append(
                    f"- 本周贡献较多的是 {contributor.get('asset_name', contributor.get('asset_code'))}，"
                    f"本周浮盈变化 {_money_text(contributor.get('weekly_floating_pnl_change'))}。"
                )
            if detractor:
                lines.append(
                    f"- 本周拖累较明显的是 {detractor.get('asset_name', detractor.get('asset_code'))}，"
                    f"本周浮盈变化 {_money_text(detractor.get('weekly_floating_pnl_change'))}。"
                )

        _append_market_context(lines, snapshot)
        lines.extend(
            [
                "",
                "### 下周观察",
                "- 先看组合里哪些资产在本周持续贡献，哪些只是单日波动带来的表面改善。",
                "- 下周继续按既定节奏复盘，避免把一次周报当成新的短线信号。",
                "- 如果某项资产的累计盈亏与预期配置目标明显偏离，再考虑是否需要手动校准影子持仓。",
                "",
                "数据仅用于影子记账复盘，不构成具体买卖建议。",
            ]
        )
        return "\n".join(lines)


def _append_market_context(lines: list[str], snapshot: dict[str, Any]) -> None:
    market_items = ((snapshot.get("market_context") or {}).get("popular_investments") or [])
    ok_items = [
        item
        for item in market_items
        if item.get("status") in {"ok", "historical_snapshot"}
    ]
    if not ok_items:
        return

    lines.extend(
        [
            "",
            "### 热门投资方式观察",
            "| 方向 | 代表 | 日涨跌 | 报价日期 | 状态 |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    for item in ok_items[:8]:
        status = "历史快照" if item.get("status") == "historical_snapshot" else "最新行情"
        lines.append(
            f"| {item.get('category')} | {item.get('name')} | "
            f"{_pct_text(item.get('change_pct'))} | {item.get('quote_date') or '暂无'} | {status} |"
        )


def _system_prompt(report_kind: str) -> str:
    if report_kind == "weekly":
        return WEEKLY_SYSTEM_PROMPT
    return DAILY_SYSTEM_PROMPT


def _snapshot_validation_error(snapshot: dict[str, Any]) -> str | None:
    """Reject corrupted source labels before they can reach any report path."""

    corrupted_name_count = 0
    for item in snapshot.get("positions") or []:
        asset_name = str(item.get("asset_name", "")).strip()
        if "?" in asset_name or "\ufffd" in asset_name:
            corrupted_name_count += 1

    if corrupted_name_count:
        return f"found {corrupted_name_count} corrupted asset name(s)"
    return None


def _report_validation_error(report: str, snapshot: dict[str, Any]) -> str | None:
    """Return a safe validation error when AI output alters source facts."""

    errors: list[str] = []
    positions = snapshot.get("positions") or []
    expected_names = {
        str(item.get("asset_name", "")).strip()
        for item in positions
        if str(item.get("asset_name", "")).strip()
    }
    missing_name_count = sum(name not in report for name in expected_names)
    if missing_name_count:
        errors.append(f"missing {missing_name_count} exact asset name(s)")

    mismatched_code_line_count = 0
    report_lines = report.splitlines()
    for item in positions:
        asset_code = str(item.get("asset_code", "")).strip()
        asset_name = str(item.get("asset_name", "")).strip()
        if not asset_code or not asset_name:
            continue
        mismatched_code_line_count += sum(
            asset_code in line and asset_name not in line for line in report_lines
        )
    if mismatched_code_line_count:
        errors.append(
            f"contains {mismatched_code_line_count} asset code line(s) without exact name"
        )

    if "名称脱敏" in report or "已脱敏" in report:
        errors.append("contains forbidden redaction placeholder")

    mislabeled_valuation_count = 0
    for item in positions:
        valuation_price = item.get("valuation_price", item.get("price"))
        average_cost = item.get("average_cost")
        if valuation_price is None or average_cost is None:
            continue
        if abs(float(valuation_price) - float(average_cost)) < 0.005:
            continue

        valuation_texts = {
            f"{float(valuation_price):.2f}",
            f"{float(valuation_price):.1f}",
        }
        average_cost_texts = {
            f"{float(average_cost):.2f}",
            f"{float(average_cost):.1f}",
        }
        for line in report_lines:
            if any(value in line for value in average_cost_texts):
                continue
            for value in valuation_texts:
                escaped_value = re.escape(value)
                cost_before_value = re.search(
                    rf"(?:平均成本|成本价|持仓成本对应(?:的)?价格|成本对应(?:的)?价格)"
                    rf"[^0-9\n]{{0,12}}{escaped_value}",
                    line,
                )
                cost_after_value = re.search(
                    rf"{escaped_value}[^。；;\n]{{0,12}}(?:平均成本|成本价)",
                    line,
                )
                if cost_before_value or cost_after_value:
                    mislabeled_valuation_count += 1
                    break
    if mislabeled_valuation_count:
        errors.append(
            f"labels valuation price as average cost on {mislabeled_valuation_count} line(s)"
        )

    market_context = snapshot.get("market_context") or {}
    if market_context.get("is_sufficient") is False and "有效行情样本不足" not in report:
        errors.append("does not disclose insufficient market quote coverage")
    historical_items = [
        item
        for item in market_context.get("popular_investments") or []
        if item.get("status") == "historical_snapshot"
    ]
    if historical_items and "历史快照" not in report:
        errors.append("does not disclose historical market snapshot fallback")
    missing_historical_date_count = sum(
        str(item.get("quote_date") or "") not in report for item in historical_items
    )
    if missing_historical_date_count:
        errors.append(
            f"missing {missing_historical_date_count} historical quote date(s)"
        )

    return "; ".join(errors) if errors else None


def _money_text(value: Any) -> str:
    if value is None:
        return "暂无"
    return f"{float(value):+.2f}"


def _pct_text(value: Any) -> str:
    if value is None:
        return "暂无"
    return f"{float(value):+.2f}%"


def _build_retry_session(retries: int) -> requests.Session:
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        backoff_factor=0.3,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

