# SPDX-License-Identifier: Apache-2.0
"""Multi-language traceback parser tests (E5-T1, DET-2)."""

from __future__ import annotations

from wakey.fingerprints.parsers import parse_java, parse_javascript, parse_php, parse_traceback

JAVA8 = (
    'Exception in thread "main" java.lang.NullPointerException: order was null\n'
    "\tat com.acme.billing.RefundService.charge(RefundService.java:87)\n"
    "\tat com.acme.billing.Api.handle(Api.java:42)\n"
    "\tat java.base/jdk.internal.reflect.DirectMethod.invoke(DirectMethod.java:1)\n"
)
JAVA_CAUSED_BY = (
    JAVA8
    + "Caused by: java.io.IOException: connection refused\n"
    + "\tat com.acme.db.Pool.connect(Pool.java:19)\n"
)
JAVASCRIPT = (
    "TypeError: Cannot read properties of undefined (reading 'id')\n"
    "    at charge (app/billing.js:87:15)\n"
    "    at handler (app/api.js:42:9)\n"
)
PHP5 = (
    "Fatal error: Call to undefined method Order::refund() in /var/www/billing.php on line 22\n"
    "Stack trace:\n"
    "#0 /var/www/billing.php(22): Order->refund()\n"
    "#1 /var/www/api.php(9): Billing->process(2)\n"
)


def test_java8_traceback() -> None:
    parsed = parse_java(JAVA8)
    assert parsed is not None
    root, frames = parsed
    assert "NullPointerException" in root
    assert frames[0].path == "com.acme.billing.RefundService.RefundService.java"
    assert frames[0].line == 87
    assert frames[0].function == "charge"


def test_java_caused_by_becomes_root() -> None:
    parsed = parse_java(JAVA_CAUSED_BY)
    assert parsed is not None
    assert parsed[0].startswith("Caused by: java.io.IOException")
    assert parsed[1][-1].path == "com.acme.db.Pool.Pool.java"


def test_javascript_traceback() -> None:
    parsed = parse_javascript(JAVASCRIPT)
    assert parsed is not None
    message, frames = parsed
    assert message.startswith("TypeError: Cannot read properties")
    assert frames[0].path == "app/billing.js"
    assert frames[0].line == 87


def test_php5_traceback() -> None:
    parsed = parse_php(PHP5)
    assert parsed is not None
    message, frames = parsed
    assert message.startswith("Fatal error")
    assert frames[0].path == "/var/www/billing.php"
    assert frames[0].line == 22


def test_dispatcher_routes_each_language_and_rejects_plain() -> None:
    assert parse_traceback(JAVA8) is not None
    assert parse_traceback(JAVASCRIPT) is not None
    assert parse_traceback(PHP5) is not None
    assert parse_traceback("just a plain error message") is None
