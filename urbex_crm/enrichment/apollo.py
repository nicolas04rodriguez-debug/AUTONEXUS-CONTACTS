"""Apollo.io REST client (people search + email reveal).

Docs: https://docs.apollo.io/reference . Requires APOLLO_API_KEY. The /people/match
call consumes credits per revealed email, so the finder only calls it for the
top-ranked people.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..config import settings

BASE = "https://api.apollo.io/api/v1"

DECISION_MAKER_TITLES = [
    "CEO", "Chief Executive Officer", "Director General", "Gerente", "Gerente General",
    "Propietario", "Owner", "Founder", "Fundador", "Administrador", "Consejero Delegado",
    "Managing Director", "Director Comercial", "Sales Director", "Jefe de Ventas",
    "Director de Marketing", "Marketing Manager", "Director de Operaciones", "COO",
    "Director Financiero", "CFO", "Director de Compras", "Purchasing Manager",
]


@dataclass
class ApolloPerson:
    apollo_id: str
    first_name: str
    last_name: str
    title: str
    email: str | None
    email_status: str | None   # verified / guessed / unavailable / None
    linkedin_url: str | None
    seniority: str | None


class ApolloClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.apollo_api_key
        if not self.api_key:
            raise RuntimeError("APOLLO_API_KEY not set")
        self._http = httpx.Client(
            timeout=30,
            headers={"Content-Type": "application/json", "Cache-Control": "no-cache", "x-api-key": self.api_key},
        )

    def search_people(self, domain: str, titles: list[str] | None = None, per_page: int = 10) -> list[ApolloPerson]:
        payload = {
            "q_organization_domains_list": [domain],
            "person_titles": titles or DECISION_MAKER_TITLES,
            "page": 1,
            "per_page": per_page,
        }
        r = self._http.post(f"{BASE}/mixed_people/search", json=payload)
        r.raise_for_status()
        out = []
        for p in r.json().get("people", []):
            out.append(
                ApolloPerson(
                    apollo_id=p.get("id", ""),
                    first_name=p.get("first_name") or "",
                    last_name=p.get("last_name") or "",
                    title=p.get("title") or "",
                    email=p.get("email") if p.get("email") and "not_unlocked" not in p.get("email", "") else None,
                    email_status=p.get("email_status"),
                    linkedin_url=p.get("linkedin_url"),
                    seniority=p.get("seniority"),
                )
            )
        return out

    def reveal_email(self, apollo_id: str | None = None, first_name: str | None = None,
                     last_name: str | None = None, domain: str | None = None) -> ApolloPerson | None:
        """people/match: costs credits; returns the person with email when Apollo has it."""
        payload: dict = {"reveal_personal_emails": False}
        if apollo_id:
            payload["id"] = apollo_id
        else:
            payload.update({"first_name": first_name, "last_name": last_name, "domain": domain})
        r = self._http.post(f"{BASE}/people/match", json=payload)
        r.raise_for_status()
        p = r.json().get("person")
        if not p:
            return None
        return ApolloPerson(
            apollo_id=p.get("id", ""),
            first_name=p.get("first_name") or "",
            last_name=p.get("last_name") or "",
            title=p.get("title") or "",
            email=p.get("email"),
            email_status=p.get("email_status"),
            linkedin_url=p.get("linkedin_url"),
            seniority=p.get("seniority"),
        )

    def enrich_organization(self, domain: str) -> dict:
        r = self._http.get(f"{BASE}/organizations/enrich", params={"domain": domain})
        r.raise_for_status()
        return r.json().get("organization") or {}
