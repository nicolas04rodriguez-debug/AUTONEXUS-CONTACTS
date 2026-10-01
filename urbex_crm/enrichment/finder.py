"""Orchestrates email discovery until a company has >= MIN_CONTACTS_PER_COMPANY.

Waterfall (cheapest and most reliable first):
  1. Apollo people search by domain + decision-maker titles (free to search).
  2. Apollo email reveal for the top N (costs credits).
  3. Hunter domain search: known addresses + the company's pattern.
  4. Pattern guess for people we know by name but have no email for, scored by
     the inferred pattern + MX check (+ optional SMTP probe).
  5. Generic mailboxes (ventas@, gerencia@, info@) only as the final filler so
     the company still reaches the 3-email floor.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Company, CompanyStatus, Contact, EmailStatus, log_event
from . import guesser

log = logging.getLogger(__name__)

ROLE_KEYWORDS = [
    ("ceo", 1, ["ceo", "chief executive", "consejero delegado", "director general", "general manager", "managing director", "gerente general"]),
    ("owner", 1, ["owner", "propietario", "founder", "fundador", "socio", "administrador"]),
    ("gm", 2, ["gerente", "director", "manager", "head"]),
    ("sales", 3, ["comercial", "ventas", "sales"]),
    ("marketing", 3, ["marketing", "comunicacion", "comunicación"]),
    ("ops", 4, ["operaciones", "operations", "compras", "purchasing", "coo"]),
    ("finance", 4, ["financiero", "finance", "cfo"]),
]


def role_from_title(title: str | None) -> tuple[str, int]:
    t = (title or "").lower()
    for role, rank, words in ROLE_KEYWORDS:
        if any(w in t for w in words):
            return role, rank
    return "other", 9


@dataclass
class FoundContact:
    first_name: str
    last_name: str
    title: str
    email: str | None
    email_status: EmailStatus
    source: str
    confidence: float
    linkedin_url: str | None = None
    role: str = "other"
    seniority_rank: int = 9
    note: str | None = None


@dataclass
class FindResult:
    company_id: int
    added: list[FoundContact] = field(default_factory=list)
    pattern: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.added)


def _upsert_contact(session: Session, company: Company, fc: FoundContact) -> bool:
    if fc.email:
        existing = session.scalar(select(Contact).where(Contact.email == fc.email.lower()))
        if existing:
            return False
    contact = Contact(
        company_id=company.id,
        first_name=fc.first_name or None,
        last_name=fc.last_name or None,
        title=fc.title or None,
        role=fc.role,
        seniority_rank=fc.seniority_rank,
        email=fc.email.lower() if fc.email else None,
        email_status=fc.email_status,
        email_source=fc.source,
        email_confidence=fc.confidence,
        linkedin_url=fc.linkedin_url,
        notes=fc.note,
    )
    session.add(contact)
    log_event(session, "contact", None, "contact_found", {"company_id": company.id, "email": fc.email, "source": fc.source})
    return True


def _emails_on_file(session: Session, company_id: int) -> int:
    return len([c for c in session.scalars(select(Contact).where(Contact.company_id == company_id)) if c.email])


def find_contacts(session: Session, company: Company, target: int | None = None,
                  use_apollo: bool = True, use_hunter: bool = True, use_guess: bool = True,
                  max_reveals: int = 4) -> FindResult:
    target = target or settings.min_contacts_per_company
    result = FindResult(company_id=company.id)
    domain = (company.domain or "").lower().strip()
    if not domain:
        result.errors.append("company has no domain")
        return result

    named_people: list[FoundContact] = []  # people without email yet (for guessing)
    pattern: str | None = None

    # 1-2. Apollo ------------------------------------------------------------
    if use_apollo and settings.apollo_api_key:
        try:
            from .apollo import ApolloClient
            ap = ApolloClient()
            people = ap.search_people(domain)
            people.sort(key=lambda p: role_from_title(p.title)[1])
            reveals = 0
            for p in people:
                role, rank = role_from_title(p.title)
                email, status, conf = p.email, EmailStatus.LIKELY, 0.7
                if not email and reveals < max_reveals and _emails_on_file(session, company.id) + len(result.added) < target:
                    reveals += 1
                    try:
                        revealed = ap.reveal_email(apollo_id=p.apollo_id)
                        if revealed and revealed.email:
                            email, status = revealed.email, EmailStatus.VERIFIED if revealed.email_status == "verified" else EmailStatus.LIKELY
                            conf = 0.9 if status == EmailStatus.VERIFIED else 0.7
                    except Exception as e:  # noqa: BLE001
                        result.errors.append(f"apollo reveal: {e}")
                fc = FoundContact(p.first_name, p.last_name, p.title, email, status if email else EmailStatus.UNKNOWN,
                                  "apollo", conf if email else 0.0, p.linkedin_url, role, rank)
                if email:
                    if _upsert_contact(session, company, fc):
                        result.added.append(fc)
                    if pattern is None:
                        pattern = guesser.infer_pattern(email, f"{p.first_name} {p.last_name}")
                else:
                    named_people.append(fc)
        except Exception as e:  # noqa: BLE001
            result.errors.append(f"apollo: {e}")
            log.warning("apollo failed for %s: %s", domain, e)

    # 3. Hunter ----------------------------------------------------------------
    if use_hunter and settings.hunter_api_key and _emails_on_file(session, company.id) + len(result.added) < target:
        try:
            from .hunter import HUNTER_PATTERN_TO_LOCAL, HunterClient
            hc = HunterClient()
            ds = hc.domain_search(domain)
            if ds.pattern and pattern is None:
                pattern = HUNTER_PATTERN_TO_LOCAL.get(ds.pattern)
            ranked = sorted(ds.emails, key=lambda e: (role_from_title(e.position)[1], -e.confidence))
            for e in ranked:
                role, rank = role_from_title(e.position)
                status = EmailStatus.VERIFIED if e.confidence >= 90 else EmailStatus.LIKELY
                fc = FoundContact(e.first_name or "", e.last_name or "", e.position or "", e.email, status, "hunter", e.confidence / 100, e.linkedin, role, rank)
                if _upsert_contact(session, company, fc):
                    result.added.append(fc)
            # Finder for named people from Apollo without email
            for np_ in list(named_people):
                if _emails_on_file(session, company.id) + len(result.added) >= target:
                    break
                if np_.first_name and np_.last_name:
                    found = hc.email_finder(domain, np_.first_name, np_.last_name)
                    if found and found.email:
                        np_.email, np_.source, np_.confidence = found.email, "hunter", found.confidence / 100
                        np_.email_status = EmailStatus.VERIFIED if found.confidence >= 90 else EmailStatus.LIKELY
                        if _upsert_contact(session, company, np_):
                            result.added.append(np_)
                        named_people.remove(np_)
        except Exception as e:  # noqa: BLE001
            result.errors.append(f"hunter: {e}")
            log.warning("hunter failed for %s: %s", domain, e)

    # 4. Pattern guess for named people ---------------------------------------
    if use_guess and named_people and _emails_on_file(session, company.id) + len(result.added) < target:
        mx_ok = guesser.has_mx(domain)
        for np_ in named_people:
            if _emails_on_file(session, company.id) + len(result.added) >= target:
                break
            cands = guesser.candidates(f"{np_.first_name} {np_.last_name}", domain, preferred_pattern=pattern)
            if not cands:
                continue
            chosen_name, chosen = cands[0]
            status, conf, note = EmailStatus.GUESSED, 0.3, f"pattern {chosen_name}; MX {'ok' if mx_ok else 'missing'}"
            if pattern and chosen_name == pattern:
                status, conf = EmailStatus.LIKELY, 0.6
            if settings.smtp_verify_guesses and mx_ok:
                chk = guesser.smtp_check(chosen)
                if chk.deliverable is True:
                    status, conf = EmailStatus.VERIFIED, 0.85
                elif chk.deliverable is False:
                    continue
                note += f"; smtp {chk.detail}"
            if not mx_ok:
                continue
            np_.email, np_.email_status, np_.source, np_.confidence, np_.note = chosen, status, "guess", conf, note
            if _upsert_contact(session, company, np_):
                result.added.append(np_)

    # 5. Generic mailboxes as a floor ------------------------------------------
    if _emails_on_file(session, company.id) + len(result.added) < target and guesser.has_mx(domain):
        for local in guesser.GENERIC_LOCALPARTS:
            if _emails_on_file(session, company.id) + len(result.added) >= target:
                break
            fc = FoundContact("", "", f"{local}@ mailbox", f"{local}@{domain}", EmailStatus.GUESSED, "generic", 0.2, None, "generic", 8, "generic mailbox")
            if _upsert_contact(session, company, fc):
                result.added.append(fc)

    result.pattern = pattern
    session.flush()
    if _emails_on_file(session, company.id) >= target and company.status == CompanyStatus.NEW:
        company.status = CompanyStatus.ENRICHED
    log_event(session, "company", company.id, "find_contacts", {"added": result.count, "pattern": pattern, "errors": result.errors})
    return result
