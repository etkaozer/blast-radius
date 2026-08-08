"""Printing the results must not be able to fail after producing them.

`analyze`, `doctor` and `writeback` all print ✓ / ✗ / · status symbols. On a
console whose code page cannot represent them — a Turkish Windows at cp1254 —
`click.echo` raised

    UnicodeEncodeError: 'charmap' codec can't encode character '\\u2713'

*after* every check had run and passed. Click turns that into a bare `Aborted!`
with no traceback, so it reads as a failure of the work rather than of the last
line of reporting it, which is how it stayed hidden.

These tests drive `core.cli._text` directly rather than through a subprocess:
the encoding of a pytest-captured stream is not the encoding of a Windows
console, so a round trip through the CLI would prove nothing on this machine.
"""

from __future__ import annotations

import io
import sys

import pytest

from core import cli

SYMBOLS = "✓ read and write paths verified · ✗ failed → out.json"


class NarrowStream(io.StringIO):
    """A stream that reports an encoding unable to carry the status symbols."""

    encoding = "cp1254"

    def reconfigure(self, **kwargs: object) -> None:
        msg = "underlying stream cannot be reconfigured"
        raise ValueError(msg)


class WideStream(io.StringIO):
    """A stream that can carry anything, as a real UTF-8 console does."""

    encoding = "utf-8"


def test_symbols_survive_a_utf8_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing is transliterated when the stream can carry it."""
    monkeypatch.setattr(sys, "stdout", WideStream())

    assert cli._text(SYMBOLS) == SYMBOLS


def test_symbols_degrade_to_ascii_when_the_stream_cannot_carry_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command reports its results instead of dying while printing them."""
    monkeypatch.setattr(sys, "stdout", NarrowStream())

    rendered = cli._text(SYMBOLS)

    assert rendered.encode("cp1254"), "the whole point: this must not raise"
    assert "✓" not in rendered
    assert "OK" in rendered
    assert "XX" in rendered
    assert "read and write paths verified" in rendered, "the message itself survives"


def test_a_name_the_console_cannot_encode_does_not_abort_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catalog text is not ASCII either. A mangled name beats a traceback."""
    monkeypatch.setattr(sys, "stdout", NarrowStream())

    rendered = cli._text("✓ dataset 顧客 written")

    assert rendered.encode("cp1254"), "must not raise"
    assert "dataset" in rendered
    assert "written" in rendered


def test_configuring_output_survives_a_stream_that_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream that cannot be reconfigured must not abort startup.

    That is the case the ASCII fallback exists for, so raising here would take
    out every command on exactly the machines the fallback is meant to rescue.
    """
    monkeypatch.setattr(sys, "stdout", NarrowStream())
    monkeypatch.setattr(sys, "stderr", NarrowStream())

    cli._configure_output()


def test_configure_output_switches_a_reconfigurable_stream_to_utf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Where reconfiguration works, the symbols print as themselves."""
    seen: list[dict[str, object]] = []

    class Reconfigurable(io.StringIO):
        encoding = "cp1254"

        def reconfigure(self, **kwargs: object) -> None:
            seen.append(kwargs)

    monkeypatch.setattr(sys, "stdout", Reconfigurable())
    monkeypatch.setattr(sys, "stderr", Reconfigurable())

    cli._configure_output()

    assert seen == [
        {"encoding": "utf-8", "errors": "replace"},
        {"encoding": "utf-8", "errors": "replace"},
    ]
