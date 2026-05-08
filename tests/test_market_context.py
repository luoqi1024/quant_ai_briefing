from src.market_context import WatchAsset, build_market_context


def test_build_market_context_formats_popular_investments():
    watchlist = (
        WatchAsset("纳指科技", "QQQ", "US", "美股科技"),
        WatchAsset("比特币", "BTCUSDT", "CRYPTO", "数字资产"),
        WatchAsset("缺失项", "MISSING", "US", "测试"),
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
