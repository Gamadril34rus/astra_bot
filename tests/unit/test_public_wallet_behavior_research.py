from scripts.public_wallet_behavior_research import classify_fill, select_cohort, summarize_fills


def _row(address: str, pnl: float, roi: float, volume: float, value: float) -> dict:
    return {
        "ethAddress": address,
        "accountValue": str(value),
        "windowPerformances": [
            ["week", {"pnl": str(pnl), "roi": str(roi), "vlm": str(volume)}],
            ["month", {"pnl": str(pnl), "roi": str(roi), "vlm": str(volume)}],
        ],
    }


def test_cohort_selection_includes_low_roi_stratum() -> None:
    rows = [
        _row(f"0x{i:040x}", pnl=float(i), roi=float(i) / 100, volume=float(i), value=float(i + 1))
        for i in range(1, 31)
    ]
    cohort = select_cohort(rows, limit=10)
    assert len(cohort) == 10
    assert any("low_month_roi" in row["cohort_strata"] for row in cohort)


def test_fill_classification_uses_exchange_direction_fields() -> None:
    assert classify_fill({"dir": "Open Long", "side": "B", "sz": "1", "startPosition": "0"}) == "open"
    assert classify_fill({"dir": "Close Long", "side": "A", "sz": "1", "startPosition": "1"}) == "close"
    assert classify_fill({"side": "B", "sz": "0.5", "startPosition": "1"}) == "add"
    assert classify_fill({"dir": "Open Long", "side": "B", "sz": "0.5", "startPosition": "1"}) == "add"
    assert classify_fill({"side": "A", "sz": "0.5", "startPosition": "1"}) == "close"
    assert classify_fill({"side": "A", "sz": "1.5", "startPosition": "1"}) == "reverse"


def test_summary_reports_fee_and_taker_share_without_claiming_full_pnl() -> None:
    result = summarize_fills([
        {"dir": "Open Long", "coin": "BTC", "crossed": True, "fee": "0.1", "closedPnl": "0"},
        {"dir": "Close Long", "coin": "BTC", "crossed": False, "fee": "0.2", "closedPnl": "3"},
    ])
    assert result["actions"] == {"close": 1, "open": 1}
    assert result["taker_fill_share"] == 0.5
    assert result["reported_fill_fees"] == 0.30000000000000004
    assert "Funding" in result["pnl_scope_warning"]
