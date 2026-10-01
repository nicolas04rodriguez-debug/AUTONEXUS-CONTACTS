"""CSV import and company upsert + classification."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import buckets
from .models import Company, CompanyStatus, Contact, EmailStatus, Sector, Tier, log_event

COLUMN_ALIASES = {
    "name": ["name", "company", "empresa", "nombre", "company_name", "organization"],
    "domain": ["domain", "dominio", "website", "web", "url", "sitio"],
    "sector": ["sector", "industry", "industria", "tipo"],
    "tier": ["tier", "nivel", "segmento"],
    "city": ["city", "ciudad", "localidad"],
    "region": ["region", "provincia", "comunidad", "state"],
    "country": ["country", "pais", "país"],
    "brands": ["brands", "marcas", "marca"],
    "employee_count": ["employees", "employee_count", "empleados", "headcount", "# employees"],
    "est_revenue_eur": ["revenue", "est_revenue_eur", "facturacion", "facturación", "ingresos", "annual revenue"],
    "description": ["description", "descripcion", "descripción", "notes", "notas", "about"],
    "contact_name": ["contact_name", "contacto", "person", "full_name", "nombre_contacto"],
    "contact_first": ["first_name", "first name", "nombre_pila"],
    "contact_last": ["last_name", "last name", "apellidos", "apellido"],
    "contact_title": ["title", "cargo", "position", "puesto"],
    "contact_email": ["email", "correo", "e-mail", "mail"],
    "contact_linkedin": ["linkedin", "linkedin_url", "person linkedin url"],
}


def normalize_domain(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    if not v:
        return None
    if "://" not in v:
        v = "http://" + v
    host = urlparse(v).netloc or urlparse(v).path
    host = host.split("/")[0].split("@")[-1]
    host = re.sub(r"^www\.", "", host)
    return host or None


def _num(v: str | None) -> float | None:
    if not v:
        return None
    s = re.sub(r"[^\d.,kKmM]", "", v)
    mult = 1.0
    if s[-1:].lower() == "k":
        mult, s = 1e3, s[:-1]
    elif s[-1:].lower() == "m":
        mult, s = 1e6, s[:-1]
    s = s.replace(".", "").replace(",", ".") if s.count(",") == 1 and s.count(".") > 0 else s.replace(",", "")
    try:
        return float(s) * mult
    except ValueError:
        return None


def _get(row: dict, key: str) -> str | None:
    lowered = {k.strip().lower(): v for k, v in row.items() if k}
    for alias in COLUMN_ALIASES[key]:
        if alias in lowered and str(lowered[alias]).strip():
            return str(lowered[alias]).strip()
    return None


@dataclass
class UpsertResult:
    company: Company
    created: bool


def upsert_company(session: Session, name: str, domain: str | None = None, source: str = "manual",
                   sector: str | None = None, tier: str | None = None, classify: bool = True, **fields) -> UpsertResult:
    domain = normalize_domain(domain)
    company = None
    if domain:
        company = session.scalar(select(Company).where(Company.domain == domain))
    if company is None:
        company = session.scalar(select(Company).where(Company.name == name.strip()))
    created = company is None
    if created:
        company = Company(name=name.strip(), domain=domain, source=source, status=CompanyStatus.NEW,
                          sector=Sector.OTHER, tier=Tier.UNKNOWN, tier_confidence=0.0)
        session.add(company)
    elif domain and not company.domain:
        company.domain = domain
    for k, v in fields.items():
        if v not in (None, "") and hasattr(company, k):
            setattr(company, k, v)
    if company.website is None and domain:
        company.website = f"https://{domain}"
    if sector:
        try:
            company.sector = Sector(sector.strip().lower())
        except ValueError:
            pass
    if tier:
        try:
            company.tier = Tier(tier.strip().lower())
        except ValueError:
            pass
    if classify and (company.sector == Sector.OTHER or company.tier == Tier.UNKNOWN):
        c = buckets.classify(company.name, company.description, company.website, company.brands, company.employee_count, company.est_revenue_eur)
        if company.sector == Sector.OTHER:
            company.sector, company.sub_sector = c.sector, c.sub_sector
        if company.tier == Tier.UNKNOWN:
            company.tier, company.tier_confidence, company.tier_reason = c.tier, c.confidence, c.reason
    session.flush()
    log_event(session, "company", company.id, "created" if created else "updated", {"source": source})
    return UpsertResult(company, created)


def import_csv(session: Session, text: str, source: str = "csv") -> dict:
    reader = csv.DictReader(io.StringIO(text))
    created = updated = contacts = 0
    for row in reader:
        name = _get(row, "name")
        if not name:
            continue
        res = upsert_company(
            session, name, _get(row, "domain"), source=source, sector=_get(row, "sector"), tier=_get(row, "tier"),
            city=_get(row, "city"), region=_get(row, "region"), country=_get(row, "country"), brands=_get(row, "brands"),
            employee_count=int(_num(_get(row, "employee_count")) or 0) or None,
            est_revenue_eur=_num(_get(row, "est_revenue_eur")), description=_get(row, "description"),
        )
        created += res.created
        updated += not res.created
        email = _get(row, "contact_email")
        first, last = _get(row, "contact_first"), _get(row, "contact_last")
        if not first and _get(row, "contact_name"):
            parts = _get(row, "contact_name").split()
            first, last = parts[0], " ".join(parts[1:]) or None
        if email or first:
            if email and session.scalar(select(Contact).where(Contact.email == email.lower())):
                continue
            from .enrichment.finder import role_from_title
            role, rank = role_from_title(_get(row, "contact_title"))
            session.add(Contact(company_id=res.company.id, first_name=first, last_name=last, title=_get(row, "contact_title"),
                                role=role, seniority_rank=rank, email=email.lower() if email else None,
                                email_status=EmailStatus.LIKELY if email else EmailStatus.UNKNOWN,
                                email_source=source if email else None, email_confidence=0.6 if email else 0.0,
                                linkedin_url=_get(row, "contact_linkedin")))
            contacts += 1
    session.flush()
    return {"created": created, "updated": updated, "contacts": contacts}
