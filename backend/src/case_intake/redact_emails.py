from __future__ import annotations

import re
from typing import Any


# Coverage notes:
# 1) Basic mailbox: alice@example.com
# 2) Plus tags: alice+casework@example.co.uk
# 3) Dot local-part: first.last@example.gov.uk
# 4) Apostrophe local-part: o'hara@example.ie
# 5) RFC special chars in local-part: customer/department=shipping@example.com
# 6) Quoted local-part: "john..doe"@example.com
# 7) Escaped chars in quoted local-part: "a\"b"@example.com
# 8) Deep subdomains: a.b.c@sub.mail.example.com
# 9) Punycode domains: user@xn--bcher-kva.example
# 10) Unicode domains: user@bücher.de
# 11) Domain literal IPv4: user@[192.168.1.10]
# 12) Domain literal IPv6: user@[IPv6:2001:db8::1]
# 13) Internal single-label host: user@mailserver
# 14) Localhost: user@localhost
#
# Not covered (by design): obfuscated forms like "john dot doe at example dot com".
_EMAIL_PATTERN = re.compile(
    r"""
    (?<![\w!#$%&'*+/=?^_{|}~.-])
    (
      (?:
        [A-Z0-9!#$%&'*+/=?^_{|}~-]+
        (?:\.[A-Z0-9!#$%&'*+/=?^_{|}~-]+)*
        |
        "(?:[^"\\\r\n]|\\.)+"
      )
      @
      (?:
        (?:
          [A-Z0-9\u00A1-\uFFFF]
          (?:[A-Z0-9\u00A1-\uFFFF-]{0,61}[A-Z0-9\u00A1-\uFFFF])?
          \.
        )+
        (?:[A-Z\u00A1-\uFFFF]{2,63}|XN--[A-Z0-9-]{2,59})
        |
        [A-Z0-9\u00A1-\uFFFF]
        (?:[A-Z0-9\u00A1-\uFFFF-]{0,61}[A-Z0-9\u00A1-\uFFFF])
        |
        \[
          (?:
            IPv6:[0-9A-F:.]+
            |
            (?:25[0-5]|2[0-4]\d|1?\d?\d)
            (?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}
          )
        \]
        |
        localhost
      )
    )
    (?![\w.-])
    """,
    flags=re.IGNORECASE | re.UNICODE | re.VERBOSE,
)
_REPLACEMENT = "-REDACTED-"


def redact_email_addresses(text: str, replacement: str = _REPLACEMENT) -> str:
    """Redact email addresses in a single text string."""
    if not isinstance(text, str) or not text:
        return text
    return _EMAIL_PATTERN.sub(replacement, text)


def redact_email_addresses_in_data(data: Any, replacement: str = _REPLACEMENT) -> Any:
    """
    Recursively redact email addresses in nested structures.
    Supports str, dict, list, tuple, set. Leaves other types unchanged.
    """
    if isinstance(data, str):
        return redact_email_addresses(data, replacement)

    if isinstance(data, dict):
        return {
            key: redact_email_addresses_in_data(value, replacement)
            for key, value in data.items()
        }

    if isinstance(data, list):
        return [redact_email_addresses_in_data(item, replacement) for item in data]

    if isinstance(data, tuple):
        return tuple(redact_email_addresses_in_data(item, replacement) for item in data)

    if isinstance(data, set):
        return {redact_email_addresses_in_data(item, replacement) for item in data}

    return data


def redact_emails(payload: Any) -> Any:
    """Pipeline layer entrypoint."""
    return redact_email_addresses_in_data(payload, replacement=_REPLACEMENT)


def run_email_redaction_self_test() -> None:
    """
    Runs a simple redaction test using all email types listed in the comments above
    plus one negative-control obfuscated example.
    """
    test_cases = [
        ("Basic mailbox", "alice@example.com", True),
        ("Plus tag", "alice+casework@example.co.uk", True),
        ("Dot local-part", "first.last@example.gov.uk", True),
        ("Apostrophe local-part", "o'hara@example.ie", True),
        ("RFC specials local-part", "customer/department=shipping@example.com", True),
        ("Quoted local-part", "\"john..doe\"@example.com", True),
        ("Escaped quoted local-part", "\"a\\\"b\"@example.com", True),
        ("Deep subdomain", "a.b.c@sub.mail.example.com", True),
        ("Punycode domain", "user@xn--bcher-kva.example", True),
        ("Unicode domain", "user@bücher.de", True),
        ("IPv4 domain literal", "user@[192.168.1.10]", True),
        ("IPv6 domain literal", "user@[IPv6:2001:db8::1]", True),
        ("Internal host", "user@mailserver", True),
        ("Localhost", "user@localhost", True),
        ("Obfuscated form (not covered by design)", "john dot doe at example dot com", False),
    ]

    print("=== Email Redaction Self-Test ===")
    print()

    before_text = " | ".join(case[1] for case in test_cases)
    after_text = redact_email_addresses(before_text)

    print("Before redaction:")
    print(before_text)
    print()
    print("After redaction:")
    print(after_text)
    print()
    print("--- Per-case report ---")

    passed = 0
    total = len(test_cases)

    for label, email_value, should_redact in test_cases:
        sample = f"Contact: {email_value}"
        redacted = redact_email_addresses(sample)
        was_redacted = _REPLACEMENT in redacted
        ok = was_redacted == should_redact
        status = "PASS" if ok else "FAIL"

        if ok:
            passed += 1

        print(
            f"[{status}] {label}\n"
            f"  Input : {sample}\n"
            f"  Output: {redacted}\n"
            f"  Expected redaction: {should_redact}, Observed: {was_redacted}\n"
        )

    print(f"Summary: {passed}/{total} checks passed.")


if __name__ == "__main__":
    run_email_redaction_self_test()