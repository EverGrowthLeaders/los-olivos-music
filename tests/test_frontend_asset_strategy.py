from pathlib import Path


def test_asset_strategy_frontend_controls_are_wired():
    html = Path("webui/static/index.html").read_text(encoding="utf-8")

    assert "Asset Strategy" in html
    assert "reuseFields" in html
    assert "extensionFields" in html
    assert "gateFields" in html
    assert "loadAssetStrategy()" in html
    assert "saveAssetStrategy()" in html
    assert "estimateAssetStrategy()" in html
    assert "strategyScope" in html
    assert "strategyCategoryKey" in html
    assert "/api/asset-strategy/estimate" in html
    assert "Mayor riesgo de repetición percibida" in html
