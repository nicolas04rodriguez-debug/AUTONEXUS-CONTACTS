"""Hunter.io client: domain search (pattern + known emails) and email finder."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..config import settings

BASE = "https://api.hunter.io/v2"


@dataclass
class HunterEmail:
    email: str
    first_name: str | None
    last_name: str | None
    position: str | None
    confidence: int
    linkedin: str | None


@dataclass
class DomainSearch:
    pattern: str | None  # e.g. "{first}.{last}"
    emails: list[HunterEmail]


class HunterClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.hunter_api_key
        if not self.api_key:
            raise RuntimeError("HUNTER_API_KEY not set")
        self._http = httpx.Client(timeout=30)

    def domain_search(self, domain: str, limit: int = 20) -> DomainSearch:
        r = self._http.get(f"{BASE}/domain-search", params={"domain": domain, "limit": limit, "type": "personal", "api_key": self.api_key})
        r.raise_for_status()
        data = r.json().get("data", {})
        emails = [
            HunterEmail(
                email=e["value"], first_name=e.get("first_name"), last_name=e.get("last_name"),
                position=e.get("position"), confidence=int(e.get("confidence") or 0), linkedin=e.get("linkedin"),
            )
            for e in data.get("emails", [])
        ]
        return DomainSearch(pattern=data.get("pattern"), emails=emails)

    def email_finder(self, domain: str, first_name: str, last_name: str) -> HunterEmail | None:
        r = self._http.get(f"{BASE}/email-finder", params={"domain": domain, "first_name": first_name, "last_name": last_name, "api_key": self.api_key})
        r.raise_for_status()
        d = r.json().get("data") or {}
        if not d.get("email"):
            return None
        return HunterEmail(d["email"], d.get("first_name"), d.get("last_name"), d.get("position"), int(d.get("score") or 0), d.get("linkedin_url"))

    def verify(self, email: str) -> str:
        """Returns deliverable | risky | undeliverable | unknown."""
        r = self._http.get(f"{BASE}/email-verifier", params={"email": email, "api_key": self.api_key})
        r.raise_for_status()
        return (r.json().get("data") or {}).get("result", "unknown")


HUNTER_PATTERN_TO_LOCAL = {
    "{first}.{last}": "first.last", "{first}": "first", "{f}{last}": "flast", "{f}.{last}": "f.last",
    "{first}{last}": "firstlast", "{first}_{last}": "first_last", "{last}.{first}": "last.first",
    "{first}{l}": "firstl", "{first}.{l}": "first.l", "{last}": "last", "{last}{f}": "lastf", "{first}-{last}": "first-last",
}
