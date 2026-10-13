"""Errors raised by config loading and the server."""

from __future__ import annotations


class ChatError(Exception):
    """Base error for the chat server."""


class ConfigError(ChatError):
    """The config file is missing or invalid."""


class ServerError(ChatError):
    """The server could not bind or accept a client."""
