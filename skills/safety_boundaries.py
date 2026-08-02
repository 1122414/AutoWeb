"""Deterministic safety boundaries that skills cannot override."""

from __future__ import annotations


GLOBAL_SAFE_STOP_TERMS = (
    "提交订单",
    "立即支付",
    "确认支付",
    "确认付款",
    "确认购票",
    "提交申请",
    "投递简历",
    "立即沟通",
    "预约看房",
    "提交预约",
    "place order",
    "pay now",
    "confirm purchase",
)


def irreversible_target(text: str) -> str | None:
    """Return the first irreversible-action marker found in *text*."""

    normalized = " ".join(str(text or "").lower().split())
    return next(
        (term for term in GLOBAL_SAFE_STOP_TERMS if term.lower() in normalized),
        None,
    )
