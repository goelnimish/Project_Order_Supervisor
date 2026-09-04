"""Deterministic Stage 1 wake classification."""

from enum import StrEnum

from app.temporal.models import EventType


class WakePolicyOutcome(StrEnum):
    """The workflow action selected for an incoming event."""

    SUPPRESS = "suppress"
    WAKE = "wake"
    COMPLETE = "complete"


ROUTINE_EVENTS = frozenset(
    {
        EventType.ORDER_CREATED,
        EventType.PAYMENT_CONFIRMED,
        EventType.SHIPMENT_CREATED,
    }
)

IMPORTANT_EVENTS = frozenset(
    {
        EventType.PAYMENT_FAILED,
        EventType.SHIPMENT_DELAYED,
        EventType.REFUND_REQUESTED,
        EventType.CUSTOMER_MESSAGE_RECEIVED,
        EventType.NO_UPDATE_FOR_N_HOURS,
        EventType.UNKNOWN,
    }
)


def classify_event(event_type: EventType) -> WakePolicyOutcome:
    """Classify every supported event without network or database access."""

    if event_type is EventType.DELIVERED:
        return WakePolicyOutcome.COMPLETE
    if event_type in ROUTINE_EVENTS:
        return WakePolicyOutcome.SUPPRESS
    if event_type in IMPORTANT_EVENTS:
        return WakePolicyOutcome.WAKE
    return WakePolicyOutcome.WAKE
