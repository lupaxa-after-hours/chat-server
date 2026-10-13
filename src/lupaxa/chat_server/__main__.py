"""Allow ``python -m lupaxa.chat_server`` to run the CLI."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
