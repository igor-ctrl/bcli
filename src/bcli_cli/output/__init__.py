"""Output formatting for CLI."""

from bcli_cli.output._display import print_context_banner
from bcli_cli.output._formatters import detect_default_format, format_output

__all__ = ["detect_default_format", "format_output", "print_context_banner"]
