from src.market_context import WatchAsset, build_market_context


def test_build_market_context_formats_popular_investments():
    watchlist = (
        WatchAsset("纳指科技", "QQQ", "US", "美股科技"),
        WatchAsset("比特币", "BTCUSDT", "CRYPTO", "数字资产"),
        WatchAsset("缺失项目", "MISSING", "US", "测试"),
    )

    def provider(asset_code, market_type, run_date):
        if asset_code == "MISSING":
            return None
        return {
            "asset_code": asset_code,
            "market_type": market_type,
            "price": 100.0,
            "change_pct": 1.2,
            "quote_date": run_date,
            "source": "mock",
        }

    context = build_market_context(
        "2026-05-07",
        quote_provider=provider,
        watchlist=watchlist,
    )

    assert context["run_date"] == "2026-05-07"
    assert context["available_count"] == 2
    assert context["missing_count"] == 1
    assert context["total_count"] == 3
    assert context["is_sufficient"] is False
    assert context["popular_investments"][0] == {
        "name": "纳指科技",
        "category": "美股科技",
        "asset_code": "QQQ",
        "market_type": "US",
        "price": 100.0,
        "change_pct": 1.2,
        "quote_date": "2026-05-07",
        "source": "mock",
        "status": "ok",
    }
    assert context["popular_investments"][2]["status"] == "missing_quote"


def test_build_market_context_marks_complete_small_watchlist_as_sufficient():
    watchlist = (
        WatchAsset("纳指科技", "QQQ", "US", "美股科技"),
        WatchAsset("标普500", "SPY", "US", "美股宽基"),
    )

    context = build_market_context(
        "2026-05-07",
        quote_provider=lambda asset_code, market_type, run_date: {
            "price": 100.0,
            "quote_date": run_date,
            "source": "mock",
        },
        watchlist=watchlist,
    )

    assert context["available_count"] == 2
    assert context["missing_count"] == 0
    assert context["is_sufficient"] is True


def test_build_market_context_without_provider_reports_no_coverage():
    watchlist = (WatchAsset("纳指科技", "QQQ", "US", "美股科技"),)

    context = build_market_context(
        "2026-05-07",
        quote_provider=None,
        watchlist=watchlist,
    )

    assert context == {
        "run_date": "2026-05-07",
        "popular_investments": [],
        "available_count": 0,
        "missing_count": 1,
        "total_count": 1,
        "is_sufficient": False,
    }

