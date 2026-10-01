# Email Playbook

## Principles

- Spanish, plain text, under 120 words, one question as the CTA.
- Name the bucket's pain in the first two lines. Never describe Urbex before
  the pain.
- One sequence = 4 touches over ~14 days. After step 4 the contact is `completed`
  and never re-enrolled automatically.
- Every first-touch carries: who you are, why them, one proof point, one CTA,
  opt-out line ("responde *baja*"), postal address.
- Follow-ups reply in the same thread (`Re:` + same subject). The last one is a
  polite breakup; breakups get the most replies.

## Angles per bucket

| Bucket | Pain to open with | Proof / angle | CTA |
|---|---|---|---|
| automotive_dealer / **high** (premium brands) | Premium buyers compare dealers on experience, not price; leads from portals are low intent | Qualified traffic → appointments for sales and after-sales | 15-min call |
| automotive_dealer / **mid** (mainstream) | Lots of portal traffic, little showroom conversion | Convert web/portal leads into showroom visits | 15-min call |
| automotive_dealer / **low** (used, multi-brand, Dacia) | Rotate stock fast with thin margins and tiny team | Faster stock rotation without more ad spend | 15-min call or WhatsApp |
| construction / **high** (≥ 20M EUR) | Pipeline depends on the network and public tenders | Systematic conversations with developers and corporate clients | 15-min call |
| construction / **mid** | Two or three clients make up most revenue; word of mouth | Predictable new-client flow | 15-min call |
| construction / **low** (reformas, small builders) | Quotes that never close; time wasted | Clients that actually close | WhatsApp / call |

Edit the copy in `config/pitch.yaml` (pain, one_liner, proof, cta) and the
structure in `templates/<family>/<tier>/stepN.md`. Re-run `urbex init-db` after
editing templates (it upserts).

## Who to email (≥ 3 per company)

Priority order (`seniority_rank`): CEO / Consejero Delegado / Director General /
Gerente / Propietario (1) → Director (2) → Director Comercial / Jefe de Ventas /
Marketing (3) → Operaciones / Compras / Financiero (4) → generic mailboxes (8).
The `/draft` command enrolls the top 3 by rank. Space the three contacts from
one company across days if you want to look less automated: approve them on
different days from `/queue`.

## Sending rules

- Window 09:00–18:00 Europe/Madrid, weekdays only, random 0–45 min jitter.
- `DAILY_SEND_CAP=40`, `MIN_SECONDS_BETWEEN_SENDS=90`.
- Warm-up: 10/day for a week, 20/day the second week, then 40.
- Mailbox: a Google Workspace user on the Urbex domain (not @gmail.com).
  SPF, DKIM and DMARC (`p=none` to start) configured on the domain.
- Stop conditions, checked every 10 minutes: reply → `replied`; mailer-daemon
  in thread → `bounced` + suppressed; reply containing "baja" → unsubscribed +
  suppressed.

## Legal (Spain / EU)

- LSSI art. 21 forbids unsolicited commercial email **unless** there is a prior
  relationship or, for B2B role addresses, a legitimate-interest basis; GDPR
  art. 6(1)(f) + recital 47 cover direct marketing as legitimate interest.
  Keep it B2B, relevant to the recipient's role, identify yourself, include a
  postal address and a working opt-out, and honour opt-outs immediately.
- Never email personal addresses (gmail.com, hotmail.com) found by scraping.
- Keep the `suppressions` table forever; it is the evidence you honoured opt-outs.
