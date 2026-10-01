"""Template selection + rendering (+ optional LLM personalization)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Company, Contact, Sector, Template, Tier

_env = Environment(undefined=StrictUndefined, autoescape=False, trim_blocks=True, lstrip_blocks=True)

# Sectors that share one template family.
TEMPLATE_FAMILY = {
    Sector.AUTOMOTIVE_DEALER: "automotive_dealer",
    Sector.AUTOMOTIVE_SERVICES: "automotive_dealer",
    Sector.CONSTRUCTION: "construction",
    Sector.CONSTRUCTION_DEVELOPER: "construction",
    Sector.CONSTRUCTION_MATERIALS: "construction",
    Sector.OTHER: "construction",
}


def load_pitch() -> dict:
    if settings.pitch_file.exists():
        return yaml.safe_load(settings.pitch_file.read_text(encoding="utf-8")) or {}
    return {}


def pitch_for(sector: Sector, tier: Tier) -> dict:
    data = load_pitch()
    merged = dict(data.get("default", {}))
    family = TEMPLATE_FAMILY.get(sector, "construction")
    tier_key = tier.value if tier != Tier.UNKNOWN else "mid"
    merged.update((data.get(family) or {}).get(tier_key) or {})
    return merged


def parse_template_file(path: Path) -> dict:
    """Markdown with YAML front matter: --- subject: ... --- body."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    if not m:
        raise ValueError(f"{path}: missing front matter")
    meta = yaml.safe_load(m.group(1)) or {}
    meta["body"] = m.group(2).strip() + "\n"
    return meta


def seed_templates(session: Session, templates_dir: Path | None = None) -> int:
    """Load templates/<family>/<tier>/step<N>.md into the templates table (upsert)."""
    base = templates_dir or settings.templates_dir
    count = 0
    for path in sorted(base.glob("*/*/step*.md")):
        family, tier_s = path.parent.parent.name, path.parent.name
        step = int(re.search(r"step(\d+)", path.stem).group(1))
        meta = parse_template_file(path)
        sector = Sector(family)
        tier = Tier(tier_s)
        lang = meta.get("language", "es")
        existing = session.scalar(select(Template).where(Template.sector == sector, Template.tier == tier, Template.step == step, Template.language == lang))
        if existing:
            existing.subject, existing.body, existing.name = meta["subject"], meta["body"], meta.get("name", path.stem)
        else:
            session.add(Template(name=meta.get("name", f"{family}/{tier_s}/{path.stem}"), sector=sector, tier=tier, step=step,
                                 language=lang, subject=meta["subject"], body=meta["body"]))
        count += 1
    session.flush()
    return count


def pick_template(session: Session, sector: Sector, tier: Tier, step: int, language: str = "es") -> Template | None:
    family = Sector(TEMPLATE_FAMILY.get(sector, "construction"))
    tier_order = [tier] + [t for t in (Tier.MID, Tier.HIGH, Tier.LOW) if t != tier]
    for t in tier_order:
        if t == Tier.UNKNOWN:
            continue
        tpl = session.scalar(select(Template).where(Template.sector == family, Template.tier == t, Template.step == step,
                                                    Template.language == language, Template.active.is_(True)))
        if tpl:
            return tpl
    return None


@dataclass
class Draft:
    subject: str
    body: str
    template_id: int | None


def build_context(company: Company, contact: Contact, step: int, hook: str = "") -> dict:
    pitch = pitch_for(company.sector, company.tier)
    first = contact.first_name or ""
    greeting = f"Hola {first}," if first else "Hola,"
    return {
        "company": company,
        "contact": contact,
        "first_name": first,
        "greeting": greeting,
        "company_name": company.name,
        "city": company.city or "",
        "sector": company.sector.value,
        "tier": company.tier.value,
        "step": step,
        "hook": hook,
        "pitch": pitch,
        "sender": {
            "name": settings.sender_full_name,
            "title": settings.sender_title,
            "phone": settings.sender_phone,
            "linkedin": settings.sender_linkedin,
            "website": settings.company_website,
            "address": settings.company_postal_address,
        },
    }


def render(template: Template, ctx: dict) -> Draft:
    subject = _env.from_string(template.subject).render(**ctx).strip()
    body = _env.from_string(template.body).render(**ctx).strip() + "\n"
    return Draft(subject=subject, body=body, template_id=template.id)


def draft_for(session: Session, company: Company, contact: Contact, step: int, personalize: bool = False,
              previous_subject: str | None = None) -> Draft:
    tpl = pick_template(session, company.sector, company.tier, step, contact.language or "es")
    if tpl is None:
        raise LookupError(f"no template for {company.sector.value}/{company.tier.value} step {step}")
    hook = ""
    if personalize and step == 1 and company.description:
        from .llm import personalize_hook
        hook = personalize_hook(company.name, company.description, contact.language or "es")
    ctx = build_context(company, contact, step, hook)
    d = render(tpl, ctx)
    if step > 1 and previous_subject:
        base = re.sub(r"^(re:\s*)+", "", previous_subject, flags=re.I)
        d.subject = f"Re: {base}"
    return d
