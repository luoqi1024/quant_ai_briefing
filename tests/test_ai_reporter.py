from src.ai_reporter import AIReporter
from src.config import Settings


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class SuccessfulSession:
    def post(self, *args, **kwargs):
        self.last_payload = kwargs["json"]
        return Response({"choices": [{"message": {"content": "生成后的日报"}}]})


class FailingSession:
    def post(self, *args, **kwargs):
        raise RuntimeError("api unavailable")


def test_ai_reporter_falls_back_when_unconfigured():
    reporter = AIReporter(settings=Settings(), session=FailingSession())

    report = reporter.generate_report({"run_date": "2026-05-07", "totals_by_currency": {}})

    assert "投资日报" in report
    assert "暂无持仓" in report


def test_ai_reporter_fallback_includes_position_detail_table():
    reporter = AIReporter(settings=Settings(), session=FailingSession())

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
        }
    )

    assert "持仓明细" in report
    assert "测试基金" in report
    assert "1100.00" in report
    assert "+16.26" in report
    assert "+100.00" in report
    assert "+10.00%" in report


def test_ai_reporter_falls_back_when_api_fails():
    settings = Settings(
        xiaomi_ai_api_key="key",
        xiaomi_ai_url="https://example.invalid/chat",
        xiaomi_ai_model="model",
    )
    reporter = AIReporter(settings=settings, session=FailingSession())

    report = reporter.generate_report(
        {
            "run_date": "2026-05-07",
            "totals_by_currency": {
                "USD": {"cost": 500.0, "market_value": 525.0, "floating_pnl": 25.0}
            },
        }
    )

    assert "USD" in report
    assert "25.00" in report


def test_ai_reporter_sends_richer_prompt_and_market_context():
    settings = Settings(
        xiaomi_ai_api_key="key",
        xiaomi_ai_url="https://example.invalid/chat",
        xiaomi_ai_model="model",
    )
    session = SuccessfulSession()
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
                "popular_investments": [
                    {
                        "name": "纳指科技",
                        "category": "美股科技",
                        "change_pct": 0.8,
                        "status": "ok",
                    }
                ]
            },
        }
    )

    system_prompt = session.last_payload["messages"][0]["content"]
    user_payload = session.last_payload["messages"][1]["content"]

    assert report == "生成后的日报"
    assert "700 到 1100" in system_prompt
    assert "逐项持仓明细表" in system_prompt
    assert "今日盈利/亏损金额 daily_pnl" in system_prompt
    assert "累计盈利/亏损 floating_pnl" in system_prompt
    assert "纳指科技" in user_payload
    assert "daily_pnl" in user_payload
