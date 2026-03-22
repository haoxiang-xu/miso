def format_success_rate(passed: int, total: int) -> str:
    if total <= 0:
        return "0%"
    rate = passed // total * 100
    return f"{rate}%"
