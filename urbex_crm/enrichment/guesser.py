"""Email pattern guessing with MX (and optional SMTP) verification.

Used as the last resort when Apollo/Hunter don't return an address for a known
person. Also used to infer the company's pattern from one known address.
"""
from __future__ import annotations

import re
import smtplib
import socket
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

PATTERNS: list[tuple[str, str]] = [
    ("first.last", "{first}.{last}"),
    ("first", "{first}"),
    ("flast", "{f}{last}"),
    ("f.last", "{f}.{last}"),
    ("firstlast", "{first}{last}"),
    ("first_last", "{first}_{last}"),
    ("last.first", "{last}.{first}"),
    ("firstl", "{first}{l}"),
    ("first.l", "{first}.{l}"),
    ("last", "{last}"),
    ("lastf", "{last}{f}"),
    ("first-last", "{first}-{last}"),
]

GENERIC_LOCALPARTS = ["info", "ventas", "comercial", "direccion", "gerencia", "administracion", "contacto", "hola", "sales"]


def slug(part: str) -> str:
    part = unicodedata.normalize("NFKD", part).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", part.lower())


def split_name(full_name: str) -> tuple[str, str]:
    """'Juan Carlos García López' -> ('juancarlos'?) no: keep first token and last meaningful token.

    Spanish names often carry two surnames; the corporate convention is almost
    always first-name + first-surname, so we take tokens [0] and [1] when there
    are 3+, else [0] and [-1]. Particles (de, del, la) are skipped.
    """
    tokens = [t for t in re.split(r"\s+", full_name.strip()) if t]
    particles = {"de", "del", "la", "las", "los", "y", "van", "von", "di", "da"}
    tokens = [t for t in tokens if t.lower() not in particles]
    if not tokens:
        return "", ""
    if len(tokens) == 1:
        return slug(tokens[0]), ""
    first = tokens[0]
    last = tokens[1] if len(tokens) >= 3 else tokens[-1]
    return slug(first), slug(last)


def candidates(full_name: str, domain: str, preferred_pattern: str | None = None) -> list[tuple[str, str]]:
    """Return [(pattern_name, email)] ordered by likelihood."""
    first, last = split_name(full_name)
    domain = domain.lower().strip().lstrip("@")
    if not first:
        return []
    fmt = {"first": first, "last": last, "f": first[:1], "l": last[:1]}
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    ordered = PATTERNS
    if preferred_pattern:
        ordered = [p for p in PATTERNS if p[0] == preferred_pattern] + [p for p in PATTERNS if p[0] != preferred_pattern]
    for name, pattern in ordered:
        if not last and ("last" in pattern or "{l}" in pattern):
            continue
        local = pattern.format(**fmt)
        if not local:
            continue
        email = f"{local}@{domain}"
        if email not in seen:
            seen.add(email)
            out.append((name, email))
    return out


def infer_pattern(known_email: str, full_name: str) -> str | None:
    """Given one known address and its owner's name, return the pattern name used."""
    local = known_email.split("@")[0].lower()
    for name, email in candidates(full_name, "x.com"):
        if email.split("@")[0] == local:
            return name
    return None


@lru_cache(maxsize=512)
def mx_hosts(domain: str) -> tuple[str, ...]:
    try:
        import dns.resolver
    except ImportError:  # pragma: no cover
        return ()
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        return tuple(str(r.exchange).rstrip(".") for r in sorted(answers, key=lambda r: r.preference))
    except Exception:
        return ()


def has_mx(domain: str) -> bool:
    return bool(mx_hosts(domain))


@dataclass
class SmtpCheck:
    deliverable: bool | None  # None = inconclusive (catch-all / blocked)
    detail: str


def smtp_check(email: str, helo_domain: str = "example.com", mail_from: str = "verify@example.com") -> SmtpCheck:
    """RCPT TO probe. Many providers (Google, Microsoft) accept everything or block
    port 25 from residential/cloud IPs, so treat None as 'unknown', not 'bad'."""
    domain = email.split("@")[-1]
    hosts = mx_hosts(domain)
    if not hosts:
        return SmtpCheck(False, "no MX")
    try:
        with smtplib.SMTP(hosts[0], 25, timeout=10) as s:
            s.helo(helo_domain)
            s.mail(mail_from)
            code, msg = s.rcpt(email)
            if code == 250:
                code_bogus, _ = s.rcpt(f"zz{email.split('@')[0]}x9q@{domain}")
                if code_bogus == 250:
                    return SmtpCheck(None, "catch-all domain")
                return SmtpCheck(True, "accepted")
            if code in (550, 551, 553):
                return SmtpCheck(False, f"rejected {code}")
            return SmtpCheck(None, f"{code} {msg!r}")
    except (socket.timeout, OSError, smtplib.SMTPException) as e:
        return SmtpCheck(None, f"smtp error: {e}")
