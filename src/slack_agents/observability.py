"""OpenTelemetry observability: tracing for LLM calls and agent loops.

Uses the OpenTelemetry SDK with OTLP/HTTP exporters so traces can go to any
backend. Span attribute names are configured declaratively in config.yaml —
the code has no backend-specific knowledge.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
from contextlib import contextmanager
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from slack_agents.config import ObservabilityConfig

logger = logging.getLogger(__name__)

# Module-level state — set by initialize()
_tracer = None  # opentelemetry.trace.Tracer | None
_provider = None  # opentelemetry.sdk.trace.TracerProvider | None
_attr_map: dict[str, list[str]] = {}  # semantic key → list of OTEL attribute names


def initialize(config: ObservabilityConfig) -> None:
    """Set up OTEL TracerProvider with one exporter per endpoint."""
    global _tracer, _provider, _attr_map

    if not config.endpoints:
        return

    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("opentelemetry-sdk not installed, observability disabled")
        return

    resource = Resource.create({"service.name": "slack-agents"})
    tp = TracerProvider(resource=resource)

    for ep in config.endpoints:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            headers = {h.key: h.value for h in ep.headers}
            if ep.basic_auth:
                token = base64.b64encode(
                    f"{ep.basic_auth.user}:{ep.basic_auth.password}".encode()
                ).decode()
                headers["Authorization"] = f"Basic {token}"
            exporter = OTLPSpanExporter(endpoint=ep.endpoint, headers=headers)
            tp.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("OTEL exporter added: %s", ep.endpoint)
        except Exception:
            logger.exception("Failed to add OTEL exporter for %s", ep.endpoint)

    _provider = tp
    _tracer = tp.get_tracer("slack-agents")

    merged: dict[str, list[str]] = {}
    for ep in config.endpoints:
        for semantic_key, otel_attr in ep.attributes.items():
            merged.setdefault(semantic_key, [])
            if otel_attr not in merged[semantic_key]:
                merged[semantic_key].append(otel_attr)
    _attr_map = merged
    logger.info("OpenTelemetry observability enabled with %d endpoint(s)", len(config.endpoints))


def observe(
    name: str | None = None,
    as_type: str | None = None,
    capture_input: bool | None = None,
    capture_output: bool | None = None,
) -> Callable:
    """Decorator that creates an OTEL span around a function call."""

    def decorator(func: Callable) -> Callable:
        span_name = name or func.__qualname__

        if inspect.isasyncgenfunction(func):

            @wraps(func)
            async def async_gen_wrapper(*args, **kw):
                if _tracer is None:
                    async for item in func(*args, **kw):
                        yield item
                    return
                with _start_span(span_name, as_type):
                    async for item in func(*args, **kw):
                        yield item

            return async_gen_wrapper

        if asyncio.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args, **kw):
                if _tracer is None:
                    return await func(*args, **kw)
                with _start_span(span_name, as_type):
                    return await func(*args, **kw)

            return async_wrapper

        @wraps(func)
        def sync_wrapper(*args, **kw):
            if _tracer is None:
                return func(*args, **kw)
            with _start_span(span_name, as_type):
                return func(*args, **kw)

        return sync_wrapper

    return decorator


@contextmanager
def _start_span(span_name: str, as_type: str | None):
    """Context manager that starts an OTEL span."""
    with _tracer.start_as_current_span(span_name) as span:
        if as_type:
            set_span_attrs(observation_type=as_type)
        yield span


def _current_span():
    if _tracer is None:
        return None
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span and span.is_recording():
            return span
    except Exception:
        pass
    return None


def set_span_attrs(**kwargs: Any) -> None:
    """Set attributes on the current span using the configured attribute mapping."""
    span = _current_span()
    if span is None:
        return
    try:
        for key, value in kwargs.items():
            if value is None:
                continue
            otel_attrs = _attr_map.get(key)
            if not otel_attrs:
                continue
            attr_value = json.dumps(value) if isinstance(value, (dict, list)) else value
            for otel_attr in otel_attrs:
                span.set_attribute(otel_attr, attr_value)
    except Exception:
        logger.debug("Failed to set span attributes", exc_info=True)


def flush_trace() -> None:
    """Flush pending OTEL spans. No-op if not configured."""
    if _provider is not None:
        try:
            _provider.force_flush()
        except Exception:
            logger.debug("Failed to flush OTEL spans", exc_info=True)
