from src.reporting import format_success_rate


def test_format_success_rate_uses_fractional_percentage():
    assert format_success_rate(3, 4) == "75%"


def test_format_success_rate_zero_total_is_zero():
    assert format_success_rate(0, 0) == "0%"
