# SPDX-License-Identifier: Apache-2.0
"""Multi-language traceback parsers (E5-T1, DET-2).

Each parser returns ``(root_error_line, frames)`` or ``None`` if the text
is not its format. ``parse_traceback`` dispatches on detection so callers
never need to know the language. Line numbers stay in the frames —
fingerprint stability tolerates them via the related-fingerprint pass (P2).
"""

from __future__ import annotations

import re

from wakey.core.models import TraceFrame

JavaFrame = re.compile(r"at\s+([\w$.]+)\.([\w$<>]+)\(([\w.$]+):?(\d+)?\)")
JsFrame = re.compile(r"at\s+(?:(.+?)\s+\()?(.+?):(\d+):(\d+)\)?")
PhpFrame = re.compile(r"#\d+\s+(.+?)\((\d+)\):\s+(.+)")
_PY_FILE = re.compile(r'File "([^"]+)", line (\d+), in (\S+)')


def parse_python(text: str) -> tuple[str, tuple[TraceFrame, ...]] | None:
    if "Traceback (most recent call last):" not in text:
        return None
    frames: list[TraceFrame] = []
    for path, line, function in _PY_FILE.findall(text):
        frames.append(TraceFrame(path=path, line=int(line), function=function))
    error_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith(("File ", "Traceback", "During handling"))
    ]
    if not frames or not error_lines:
        return None
    return error_lines[-1], tuple(frames)


def parse_java(text: str) -> tuple[str, tuple[TraceFrame, ...]] | None:
    """Java: exception line first, then ``at com.x.Y.method(File.java:123)``.

    Handles Java 8-era traces: nested causes (``Caused by``) contribute all
    frames; JAR locations without line numbers become line 0.
    """
    frames: list[TraceFrame] = []
    for match in JavaFrame.finditer(text):
        package, function, file_name, line = match.groups()
        frames.append(
            TraceFrame(path=f"{package}.{file_name}", line=int(line or 0), function=function)
        )
    if not frames:
        return None
    error_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
        and not line.strip().startswith(("at ", "Caused by", "\t..."))
        and ".Exception" not in line
        and "Error:" not in line[:2]
    ]
    # root cause message: the last "Caused by" if present, else the first line
    caused_by = [line.strip() for line in text.splitlines() if line.strip().startswith("Caused by")]
    root = caused_by[-1].strip() if caused_by else error_lines[0]
    return root, tuple(frames)


def parse_javascript(text: str) -> tuple[str, tuple[TraceFrame, ...]] | None:
    """V8/Node style: message line, then ``at fn (file:line:col)``.

    Minified/bundled traces (single long line with ``at file:line:col``
    fragments) parse best-effort; sourcemap resolution is out of scope.
    """
    if "\n" not in text or ("    at " not in text and "\tat " not in text):
        return None
    frames: list[TraceFrame] = []
    for match in JsFrame.finditer(text):
        fn, path, line, _col = match.groups(default="")
        frames.append(TraceFrame(path=path, line=int(line), function=fn or "<anonymous>"))
    if not frames:
        return None
    message = next(
        (line for line in text.splitlines() if line.strip() and not line.strip().startswith("at ")),
        text.splitlines()[0],
    ).strip()
    return message, tuple(frames)


def parse_php(text: str) -> tuple[str, tuple[TraceFrame, ...]] | None:
    """PHP: ``#0 /path/file.php(22): Class->method()`` stack, message first."""
    if "#0 " not in text:
        return None
    frames: list[TraceFrame] = []
    for match in PhpFrame.finditer(text):
        path, line, call = match.groups()
        function = call.split("(")[-1].rstrip("):").strip() or call
        frames.append(TraceFrame(path=path, line=int(line), function=function))
    if not frames:
        return None
    message = next(
        (
            line.strip()
            for line in text.splitlines()
            if line.strip()
            and not line.strip().startswith("#")
            and "Stack trace:" not in line
            and "thrown in" not in line
        ),
        text.splitlines()[0],
    ).strip()
    return message, tuple(frames)


_PARSERS = (parse_python, parse_java, parse_javascript, parse_php)


def parse_traceback(text: str) -> tuple[str, tuple[TraceFrame, ...]] | None:
    """Detect the traceback language and parse it; None if plain text."""
    for parser in _PARSERS:
        parsed = parser(text)
        if parsed is not None:
            return parsed
    return None
