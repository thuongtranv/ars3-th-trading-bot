"""Alert delivery: one Telegram message per run, listing every state change."""
import pandas as pd

from common.notify import send_telegram

TITLES = {
    "STRESS_ON": "🚨 Cụm căng thẳng BẬT",
    "STRESS_OFF": "✅ Cụm căng thẳng TẮT",
    "RECOVERY_ON": "🟢 Xác nhận hồi phục BẬT",
    "RECOVERY_OFF": "⚪ Xác nhận hồi phục TẮT",
    "TOP_ZONE_ENTER": "⚠️ Vào vùng đỉnh theo đồng hồ halving",
    "REGIME_ABOVE_TREND": "📈 Chế độ: trên xu hướng",
    "REGIME_BELOW_TREND": "📉 Chế độ: dưới xu hướng",
    "REGIME_MIXED": "↔️ Chế độ: hỗn hợp",
}


def _short(names):
    return ", ".join(n.split(".")[-1] for n in names) or "-"


def format_message(verdict, asof, halving_days, status):
    lines = [TITLES.get(e, e) for e in verdict.events] + [f"Ngày dữ liệu: {asof.date()} (UTC)"]
    lines += [
        f"Căng thẳng: {len(verdict.stress_hits)}/{verdict.stress_available} cờ ({_short(verdict.stress_hits)})"
        f" | thiếu dữ liệu: {_short(verdict.stress_missing)}",
        f"Chế độ: {verdict.state.regime} ({verdict.regime_hits}/{verdict.regime_available})"
        f" | Hồi phục: {verdict.recovery_hits}/{verdict.recovery_available}",
    ]
    if not pd.isna(halving_days):
        lines.append(f"Ngày thứ {int(halving_days)} sau halving")
    down = [n for n, s in status.items() if s.startswith("missing")]
    if down:
        lines.append(f"Nguồn không lấy được: {', '.join(down)}")
    return "\n".join(lines)


def send(text, shadow):
    """True when delivered. While `shadow` is on, messages carry a prefix so they are never mistaken for live."""
    return send_telegram(text, prefix="[SHADOW] " if shadow else "")
