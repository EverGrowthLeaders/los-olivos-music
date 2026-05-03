from yt_music_factory.asset_strategy import estimate_cost, strategy_preset


def test_cost_estimator_base_sixty_minutes():
    payload = estimate_cost(strategy_preset("standard"), target_minutes=60, clip_minutes=3, price_per_generation=0.08, thumbnail_price=0.134)

    assert payload["base_clips"] == 20
    assert payload["base_total_cost"] == 1.734


def test_cost_estimator_standard_roughly_halves_new_clips():
    payload = estimate_cost(strategy_preset("standard"), target_minutes=60, clip_minutes=3, price_per_generation=0.08, thumbnail_price=0.134)

    assert payload["expected_new_clips"] in {7, 8}
    assert payload["expected_reused_clips"] >= 3
    assert payload["expected_extended_clips"] >= 4
    assert payload["expected_savings_percent"] > 45


def test_cost_estimator_warns_for_aggressive_profile():
    payload = estimate_cost(strategy_preset("aggressive"), target_minutes=60)

    assert any("Mayor riesgo" in warning for warning in payload["warnings"])
