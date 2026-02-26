"""Google Cloud Pub/Sub client utilities.

Provides helpers for publishing messages and managing subscriptions
across pipeline stages (extraction, AI drafting, recompilation).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from google.cloud import pubsub_v1

from services.common.config import settings

logger = logging.getLogger(__name__)


def _get_publisher() -> pubsub_v1.PublisherClient:
    return pubsub_v1.PublisherClient()


def _get_subscriber() -> pubsub_v1.SubscriberClient:
    return pubsub_v1.SubscriberClient()


def _topic_path(topic_name: str) -> str:
    publisher = _get_publisher()
    return publisher.topic_path(settings.gcp_project_id, topic_name)


def _subscription_path(subscription_name: str) -> str:
    subscriber = _get_subscriber()
    return subscriber.subscription_path(settings.gcp_project_id, subscription_name)


def publish_message(topic_name: str, data: dict[str, Any]) -> str:
    """Publish a JSON message to a Pub/Sub topic. Returns the message ID."""
    publisher = _get_publisher()
    topic = _topic_path(topic_name)
    message_bytes = json.dumps(data).encode("utf-8")

    future = publisher.publish(topic, message_bytes)
    # HIGH-4.8: Add timeout to prevent indefinite hang on publish
    message_id = future.result(timeout=30)
    logger.info("Published message %s to %s", message_id, topic_name)
    return message_id


def publish_document_event(topic_name: str, document_id: str, **extra: Any) -> str:
    """Convenience: publish a document processing event."""
    data = {"document_id": document_id, **extra}
    return publish_message(topic_name, data)


def subscribe(
    subscription_name: str,
    callback: Callable[[dict[str, Any]], None],
    *,
    max_messages: int = 10,
    ack_deadline: int = 60,
) -> pubsub_v1.subscriber.futures.StreamingPullFuture:
    """Start a streaming pull subscription.

    The callback receives the parsed JSON payload. Messages are
    automatically acknowledged on successful callback completion
    and nacked on exception.
    """
    subscriber = _get_subscriber()
    subscription = _subscription_path(subscription_name)

    def _wrapped_callback(message: pubsub_v1.subscriber.message.Message) -> None:
        try:
            payload = json.loads(message.data.decode("utf-8"))
            logger.info(
                "Received message %s on %s", message.message_id, subscription_name
            )
            # MEDIUM-4.16: ack() is called AFTER callback() returns so any
            # exception raised by the caller (including durable write failures)
            # triggers nack() and causes redelivery. Callers MUST raise on
            # durable write failure — returning normally signals success and
            # will cause the message to be acknowledged.
            callback(payload)
            message.ack()
        except Exception:
            logger.exception(
                "Error processing message %s on %s",
                message.message_id,
                subscription_name,
            )
            message.nack()

    flow_control = pubsub_v1.types.FlowControl(max_messages=max_messages)
    future = subscriber.subscribe(
        subscription,
        callback=_wrapped_callback,
        flow_control=flow_control,
    )
    logger.info("Subscribed to %s", subscription_name)
    return future


def parse_pubsub_push(body: dict[str, Any]) -> dict[str, Any]:
    """Parse a Pub/Sub push message body (for Cloud Run HTTP endpoints).

    Cloud Run receives Pub/Sub messages as HTTP POST with a specific envelope format:
    {"message": {"data": "<base64-encoded JSON>", "messageId": "...", ...}}

    Raises:
        ValueError: if the envelope is malformed (missing "message" key, missing
                    "data" field, invalid base64, or non-JSON payload). Callers
                    should map this to HTTP 400 so Pub/Sub does not retry.
    """
    import base64

    # MEDIUM-4.23: Validate message structure before processing — raise ValueError
    # for malformed messages so callers can return HTTP 400 (no retry) instead of
    # crashing with an unhandled exception that maps to HTTP 500 (triggers retry).
    if not isinstance(body, dict):
        raise ValueError(f"Pub/Sub push body must be a JSON object, got {type(body).__name__}")

    message = body.get("message")
    if message is None:
        raise ValueError("Pub/Sub push envelope missing required 'message' key")
    if not isinstance(message, dict):
        raise ValueError(
            f"Pub/Sub push 'message' must be a JSON object, got {type(message).__name__}"
        )

    data_b64 = message.get("data")
    if not data_b64:
        raise ValueError("Pub/Sub push message missing required 'data' field")

    try:
        data_bytes = base64.b64decode(data_b64)
    except Exception as exc:
        raise ValueError(f"Pub/Sub push 'data' field is not valid base64: {exc}") from exc

    try:
        return json.loads(data_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Pub/Sub push message payload is not valid JSON after base64 decode: {exc}"
        ) from exc
