"""Docs: docs/sdk-schema-plan.md. SendHog authentication; provider-shaped application methods."""
from .runtime import ApiError, ProtocolError, ValidationError
from .client import SendHog
__all__ = ['SendHog','ApiError', 'ProtocolError', 'ValidationError']
