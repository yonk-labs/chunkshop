# Incident Response

## Paging
The on-call rotation is maintained in the scheduling tool. Pages escalate
after fifteen minutes of no acknowledgement; secondary on-call takes over
after thirty. Never silence a page without handing it off explicitly.

## Severity Levels
Sev-1 is customer-visible data loss or total outage. Sev-2 is partial
degradation affecting multiple customers. Sev-3 is single-tenant or
cosmetic. Sev-4 is documentation-only follow-up.

## Post-mortems
Every Sev-1 and Sev-2 requires a written post-mortem within five business
days. The template lives in the handbook; blameless framing is mandatory.
