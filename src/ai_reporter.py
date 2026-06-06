"""AI report generation with OpenAI-compatible chat completions."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import Settings, load_settings


logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是一位理性、克制但有人情味的个人投资复盘助手。"
    "请根据用户提供的个人账户快照和热门资产观察池，生成一份 Markdown 投资日报。"
    "目标长度 700 到 1100 个中文字符，语气像熟悉用户投资习惯的朋友：温和、具体、不过度煽动。"
    "报告必须包含：1）今日账户总览；2）逐项持仓明细表；3）主要持仓贡献和拖累；"
    "4）热门投资方式涨跌表；5）结合观察池给出市场评价；6）给出 2 到 4 条纪律性建议或明日观察点。"
    "逐项持仓明细表必须逐一列出 positions 中的每项资产，至少包含：资产名称、持仓市值、投入成本、"
    "今日涨跌幅、今日盈利/亏损金额 daily_pnl、累计盈利/亏损 floating_pnl、累计盈亏率 floating_pnl_pct。"
    "如果 daily_pnl 为空，要写'暂无'，不得自行编造；金额统一保留两位小数，涨跌幅和盈亏率保留两位小数。"
    "不要只用'纳指基金合计'替代明细，合计可以出现在解读段，但明细表必须保留每个具体资产。"
    "建议只能是复盘、风险控制、仓位纪律、定投纪律和观察提醒，不得给出确定性的买卖指令，"
    "不得承诺收益，不得声称知道未提供的新闻事实。"
    "如果缺少新闻或宏观事件数据，只能基于涨跌表现做谨慎推断，并明确使用'可能'、'倾向于'等不确定表达。"
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

    def generate_report(self, snapshot: dict[str, Any]) -> str:
        """Generate a report, optionally falling back to local Markdown."""

        if not self._configured():
            return self._handle_failure("AI settings are incomplete", snapshot)

        payload = {
            "model": self.settings.resolved_ai_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(snapshot, ensure_ascii=False),
                },
            ],
            "temperature": 0.45,
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
                return self._handle_failure("AI report API returned empty content", snapshot)
            return report
        except Exception as exc:  # noqa: BLE001 - remote APIs can fail many ways.
            return self._handle_failure(f"AI report API failed: {exc}", snapshot)

    def _configured(self) -> bool:
        return all(
            [
                self.settings.resolved_ai_api_key,
                self.settings.resolved_ai_url,
                self.settings.resolved_ai_model,
            ]
        )

    def _handle_failure(self, message: str, snapshot: dict[str, Any]) -> str:
        if self.fallback_on_failure:
            logger.warning("%s; using fallback report", message)
            return self._fallback_report(snapshot)
        raise AIReportError(message)

    def _fallback_report(self, snapshot: dict[str, Any]) -> str:
        totals = snapshot.get("totals_by_currency", {})
        if not totals:
            return f"## 投资日报\n\n{snapshot.get('run_date')} 暂无持仓或可计算盈亏。"

        run_date = snapshot.get("run_date")
        lines = ["## 投资日报", "", "### 今日总览", f"{run_date} 的账户快照如下："]
        for currency, values in totals.items():
            lines.append(
                f"- {currency}: 成本 {values['cost']:.2f}，市值 "
                f"{values['market_value']:.2f}，浮动盈亏 {values['floating_pnl']:.2f}"
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
                daily_pnl = item.get("daily_pnl")
                daily_pnl_text = "暂无" if daily_pnl is None else f"{float(daily_pnl):+.2f}"
                change_pct = item.get("change_pct")
                change_text = "暂无" if change_pct is None else f"{float(change_pct):+.2f}%"
                lines.append(
                    f"| {item.get('asset_name', item.get('asset_code'))} "
                    f"| {float(item.get('market_value', 0)):.2f} "
                    f"| {float(item.get('cost', 0)):.2f} "
                    f"| {change_text} "
                    f"| {daily_pnl_text} "
                    f"| {float(item.get('floating_pnl', 0)):+.2f} "
                    f"| {float(item.get('floating_pnl_pct', 0)):+.2f}% |"
                )

            best = max(positions, key=lambda item: item.get("floating_pnl", 0))
            worst = min(positions, key=lambda item: item.get("floating_pnl", 0))
            lines.extend(
                [
                    "",
                    "### 持仓复盘",
                    f"- 当前贡献较多的是 {best.get('asset_name', best.get('asset_code'))}，"
                    f"累计浮动盈亏 {best.get('floating_pnl', 0):+.2f}。",
                    f"- 当前拖累较明显的是 {worst.get('asset_name', worst.get('asset_code'))}，"
                    f"累计浮动盈亏 {worst.get('floating_pnl', 0):+.2f}。",
                ]
            )

        market_items = (
            (snapshot.get("market_context") or {}).get("popular_investments") or []
        )
        ok_items = [item for item in market_items if item.get("status") == "ok"]
        if ok_items:
            lines.extend(
                [
                    "",
                    "### 热门投资方式观察",
                    "| 方向 | 代表 | 日涨跌 |",
                    "| --- | --- | ---: |",
                ]
            )
            for item in ok_items[:8]:
                change_pct = item.get("change_pct")
                change_text = "暂无" if change_pct is None else f"{float(change_pct):+.2f}%"
                lines.append(
                    f"| {item.get('category')} | {item.get('name')} | {change_text} |"
                )

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
