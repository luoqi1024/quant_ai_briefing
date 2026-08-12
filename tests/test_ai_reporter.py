import pytest

from src.ai_reporter import AIReportError, AIReporter
from src.config import Settings


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class SuccessfulSession:
    def __init__(self, content="生成后的报告"):
        self.content = content

    def post(self, *args, **kwargs):
        self.last_payload = kwargs["json"]
        return Response({"choices": [{"message": {"content": self.content}}]})


class FailingSession:
    def post(self, *args, **kwargs):
        raise RuntimeError("api unavailable")


def test_ai_reporter_falls_back_when_unconfigured():
    reporter = AIReporter(
        settings=Settings(),
        session=FailingSession(),
        fallback_on_failure=True,
    )

    report = reporter.generate_report(
        {"run_date": "2026-05-07", "totals_by_currency": {}},
        report_kind="daily",
    )

    assert "投资日报" in report
    assert "暂无持仓" in report


def test_ai_reporter_weekly_fallback_handles_missing_week_data():
    reporter = AIReporter(
        settings=Settings(),
        session=FailingSession(),
        fallback_on_failure=True,
    )

    report = reporter.generate_report(
        {
            "run_date": "2026-06-21",
            "week_start": "2026-06-15",
            "week_end": "2026-06-19",
            "has_week_data": False,
        },
        report_kind="weekly",
    )

    assert "投资周报" in report
    assert "没有可用的日报快照" in report


def test_ai_reporter_fallback_includes_position_detail_table():
    reporter = AIReporter(
        settings=Settings(),
        session=FailingSession(),
        fallback_on_failure=True,
    )

    report = reporter.generate_report(
        {
            "run_date": "2026-05-07",
            "totals_by_currency": {
                "CNY": {"cost": 1000.0, "market_value": 1100.0, "floating_pnl": 100.0}
            },
            "positions": [
                {
                    "asset_name": "测试基金",
                    "cost": 1000.0,
                    "market_value": 1100.0,
                    "change_pct": 1.5,
                    "daily_pnl": 16.26,
                    "floating_pnl": 100.0,
                    "floating_pnl_pct": 10.0,
                }
            ],
        },
        report_kind="daily",
    )

    assert "持仓明细" in report
    assert "测试基金" in report
    assert "1100.00" in report
    assert "+16.26" in report
    assert "+100.00" in report
    assert "+10.00%" in report


def test_ai_reporter_raises_when_api_fails_without_fallback():
    settings = Settings(
        ai_api_key="key",
        ai_url="https://example.invalid/chat",
        ai_model="model",
    )
    reporter = AIReporter(settings=settings, session=FailingSession())

    with pytest.raises(AIReportError):
        reporter.generate_report(
            {
                "run_date": "2026-05-07",
                "totals_by_currency": {
                    "USD": {"cost": 500.0, "market_value": 525.0, "floating_pnl": 25.0}
                },
            },
            report_kind="daily",
        )


def test_ai_reporter_sends_daily_prompt_and_market_context():
    settings = Settings(
        ai_api_key="key",
        ai_url="https://example.invalid/chat",
        ai_model="model",
    )
    session = SuccessfulSession(content="纳指基金A\n有效行情样本不足")
    reporter = AIReporter(settings=settings, session=session)

    report = reporter.generate_report(
        {
            "run_date": "2026-05-07",
            "totals_by_currency": {
                "CNY": {"cost": 100.0, "market_value": 101.0, "floating_pnl": 1.0}
            },
            "positions": [
                {
                    "asset_name": "纳指基金A",
                    "cost": 100.0,
                    "market_value": 101.0,
                    "change_pct": 0.8,
                    "daily_pnl": 0.8,
                    "floating_pnl": 1.0,
                    "floating_pnl_pct": 1.0,
                }
            ],
            "market_context": {
                "available_count": 1,
                "missing_count": 8,
                "total_count": 9,
                "is_sufficient": False,
                "popular_investments": [
                    {
                        "name": "纳指科技",
                        "category": "美股科技",
                        "change_pct": 0.8,
                        "status": "ok",
                    }
                ]
            },
        },
        report_kind="daily",
    )

    system_prompt = session.last_payload["messages"][0]["content"]
    user_payload = session.last_payload["messages"][1]["content"]

    assert report == "纳指基金A\n有效行情样本不足"
    assert "投资日报" in system_prompt
    assert "daily_pnl" in system_prompt
    assert "asset_name 必须逐字原样使用" in system_prompt
    assert "average_cost 是平均成本价" in system_prompt
    assert "valuation_price 是当前估值价" in system_prompt
    assert "有效行情样本不足" in system_prompt
    assert "纳指科技" in user_payload
    assert "daily_pnl" in user_payload


def test_ai_reporter_sends_weekly_prompt():
    settings = Settings(
        ai_api_key="key",
        ai_url="https://example.invalid/chat",
        ai_model="model",
    )
    session = SuccessfulSession()
    reporter = AIReporter(settings=settings, session=session)

    report = reporter.generate_report(
        {
            "run_date": "2026-06-21",
            "week_start": "2026-06-15",
            "week_end": "2026-06-19",
            "has_week_data": True,
            "snapshot_date": "2026-06-19",
            "baseline_date": "2026-06-12",
            "totals_by_currency": {
                "CNY": {"cost": 100.0, "market_value": 101.0, "floating_pnl": 1.0}
            },
            "weekly_changes_by_currency": {
                "CNY": {"market_value_change": 5.0, "floating_pnl_change": 5.0}
            },
            "positions": [],
        },
        report_kind="weekly",
    )

    system_prompt = session.last_payload["messages"][0]["content"]

    assert report == "生成后的报告"
    assert "投资周报" in system_prompt
    assert "baseline_date" in system_prompt


def test_ai_reporter_accepts_legacy_xiaomi_settings():
    settings = Settings(
        xiaomi_ai_api_key="legacy-key",
        xiaomi_ai_url="https://example.invalid/chat",
        xiaomi_ai_model="legacy-model",
    )
    session = SuccessfulSession()
    reporter = AIReporter(settings=settings, session=session)

    report = reporter.generate_report(
        {
            "run_date": "2026-05-07",
            "totals_by_currency": {
                "CNY": {"cost": 100.0, "market_value": 101.0, "floating_pnl": 1.0}
            },
        },
        report_kind="daily",
    )

    assert report
    assert session.last_payload["model"] == "legacy-model"


def test_ai_reporter_rejects_report_that_changes_asset_name():
    settings = Settings(
        ai_api_key="key",
        ai_url="https://example.invalid/chat",
        ai_model="model",
    )
    reporter = AIReporter(
        settings=settings,
        session=SuccessfulSession(content="缩写后的基金名称"),
    )

    with pytest.raises(
        AIReportError,
        match=r"^AI report validation failed: missing 1 exact asset name",
    ):
        reporter.generate_report(
            {
                "run_date": "2026-05-07",
                "positions": [{"asset_name": "完整基金名称A"}],
            }
        )


@pytest.mark.parametrize("corrupted_name", ["????100ETF", "损坏名称\ufffd"])
def test_ai_reporter_rejects_corrupted_snapshot_name_before_api(corrupted_name):
    settings = Settings(
        ai_api_key="key",
        ai_url="https://example.invalid/chat",
        ai_model="model",
    )
    session = SuccessfulSession(content=corrupted_name)
    reporter = AIReporter(
        settings=settings,
        session=session,
        fallback_on_failure=True,
    )

    with pytest.raises(
        AIReportError,
        match=r"^Snapshot validation failed: found 1 corrupted asset name",
    ):
        reporter.generate_report(
            {
                "run_date": "2026-05-07",
                "positions": [{"asset_name": corrupted_name}],
            }
        )

    assert not hasattr(session, "last_payload")


def test_ai_reporter_rejects_redaction_placeholder():
    settings = Settings(
        ai_api_key="key",
        ai_url="https://example.invalid/chat",
        ai_model="model",
    )
    reporter = AIReporter(
        settings=settings,
        session=SuccessfulSession(content="完整基金名称A（名称脱敏）"),
    )

    with pytest.raises(AIReportError, match="forbidden redaction placeholder"):
        reporter.generate_report(
            {
                "run_date": "2026-05-07",
                "positions": [{"asset_name": "完整基金名称A"}],
            }
        )


def test_ai_reporter_rejects_valuation_price_labeled_as_cost():
    settings = Settings(
        ai_api_key="key",
        ai_url="https://example.invalid/chat",
        ai_model="model",
    )
    reporter = AIReporter(
        settings=settings,
        session=SuccessfulSession(
            content="中国银行积利金\n持仓成本对应价格约 90.00 元"
        ),
    )

    with pytest.raises(AIReportError, match="labels valuation price as average cost"):
        reporter.generate_report(
            {
                "run_date": "2026-05-07",
                "positions": [
                    {
                        "asset_name": "中国银行积利金",
                        "average_cost": 100.0,
                        "valuation_price": 90.0,
                    }
                ],
            }
        )


def test_ai_reporter_allows_cost_and_valuation_price_on_same_line():
    settings = Settings(
        ai_api_key="key",
        ai_url="https://example.invalid/chat",
        ai_model="model",
    )
    reporter = AIReporter(
        settings=settings,
        session=SuccessfulSession(
            content="中国银行积利金：平均成本 100.00 元，当前估值价 90.00 元"
        ),
    )

    report = reporter.generate_report(
        {
            "run_date": "2026-05-07",
            "positions": [
                {
                    "asset_name": "中国银行积利金",
                    "average_cost": 100.0,
                    "valuation_price": 90.0,
                }
            ],
        }
    )

    assert "平均成本 100.00" in report
    assert "当前估值价 90.00" in report


def test_ai_reporter_rejects_asset_code_without_exact_name_on_same_line():
    settings = Settings(
        ai_api_key="key",
        ai_url="https://example.invalid/chat",
        ai_model="model",
    )
    reporter = AIReporter(
        settings=settings,
        session=SuccessfulSession(
            content="摩根标普500指数A\n017641 被错误写成另一类指数"
        ),
    )

    with pytest.raises(AIReportError, match="asset code line"):
        reporter.generate_report(
            {
                "run_date": "2026-05-07",
                "positions": [
                    {"asset_code": "017641", "asset_name": "摩根标普500指数A"}
                ],
            }
        )


def test_ai_reporter_requires_insufficient_coverage_disclosure():
    settings = Settings(
        ai_api_key="key",
        ai_url="https://example.invalid/chat",
        ai_model="model",
    )
    reporter = AIReporter(
        settings=settings,
        session=SuccessfulSession(content="完整基金名称A"),
    )

    with pytest.raises(AIReportError, match="insufficient market quote coverage"):
        reporter.generate_report(
            {
                "run_date": "2026-05-07",
                "positions": [{"asset_name": "完整基金名称A"}],
                "market_context": {"is_sufficient": False},
            }
        )

