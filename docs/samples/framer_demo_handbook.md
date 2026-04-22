# Internal handbook dump

This file is a deliberate blob: four unrelated topics concatenated into one markdown file,
separated only by `##` headings. It simulates the kind of export you often get from a
wiki, a Notion dump, or a "paste all our docs into one file" handoff. The DocFramer
tutorial uses this file to show why you want each `##` section treated as its own logical
document instead of letting the chunker shred one giant blob.

## Onboarding

Your first week at Northwind is deliberately slow. Day one is laptop pickup, SSO setup,
and a one-on-one with your manager to walk the onboarding checklist. You will not be
expected to ship anything in your first week and you will not be paged. If you feel
pressure to, push back — it means the checklist is wrong, not you.

Access provisioning runs through the Okta request workflow. The checklist your manager
hands you lists every group you need: `eng-all`, `oncall-readonly`, your team's
GitHub org membership, Datadog, Snowflake read access. Each request needs manager
approval; most auto-approve within an hour but cloud roles take one business day because
they require a second approver in security.

Your first real PR should be a docs change, a typo fix, or a small test addition. The
goal is to close the loop on the full developer workflow — branch, PR, CI run, review,
merge, deploy — not to prove anything. Your team lead will pick something appropriate
and walk you through it. Most new hires do this in the first three days.

By end of week one you should have working local dev, access to every system you need,
and a PR merged to main. If any of those are still open, raise it in your first 1:1
rather than silently churning. We budget two weeks for full ramp on a team and we would
rather you flag a blocker than try to route around it alone.

## Code review expectations

Code review at Northwind has a 24-hour SLA for first response during business days. That
means a comment, an approval, or an explicit "I'm picking this up tomorrow morning" —
not silence. If your reviewer hasn't responded in 24 hours, ping them directly in
Slack; if that doesn't work, reassign. The goal is to keep PRs moving, not to be polite.

Reviewers are responsible for three things: correctness, maintainability, and test
coverage. "Nits" are labeled as such so authors can ignore them without guilt. If a
reviewer blocks a PR on something that isn't one of those three, the author can escalate
to the tech lead. We do not block on style preferences; that's what the linter is for.

Authors are responsible for keeping PRs small. A PR over 400 lines gets pushback
automatically — not because 400 is magic, but because past that size reviewers stop
giving a careful read. Split by refactor vs. behavior change, by subsystem, or by
vertical slice. If you genuinely can't split, write a CHANGES.md walking the reviewer
through the reading order.

Approvals are per-PR, not per-commit. Once you have one approval you can merge; we don't
require two. Emergency hotfixes can merge with zero approvals if the author also files
an incident ticket — this has happened about four times in two years and is always
retrospectively reviewed.

## Incident response

We use three SEV levels. SEV1 is customer-facing outage or data loss — page immediately,
all hands on deck, bridge call within five minutes. SEV2 is degradation affecting some
customers or an internal-only outage — page the on-call engineer, bridge within fifteen
minutes. SEV3 is a bug that's impacting no one right now but could if unaddressed — file
a ticket, don't page.

PagerDuty is the single source of truth for who's on-call. On-call rotations are weekly,
handed off Wednesday at 10am Pacific. Handoff requires a 10-minute sync call covering
anything in-flight — don't just update the status in PagerDuty and walk away. If the
outgoing on-call is sick on handoff day, the backup handles the sync.

Every SEV1 and SEV2 requires a post-mortem within five business days. Post-mortems are
blameless: we investigate what the system allowed to happen, not who typed the wrong
thing. The template asks for timeline, contributing factors, impact, and follow-up
actions. Follow-up actions are tracked as ordinary tickets and reviewed in the next
engineering all-hands.

The on-call engineer is explicitly allowed — encouraged — to declare a SEV prematurely.
If you think something might be a SEV2, call it one and start the bridge. The cost of a
false-alarm bridge is 20 minutes of one engineer's time. The cost of a missed real
incident is much higher.

## Benefits and PTO

PTO at Northwind is unlimited in policy and about 20 days per year in practice. The
minimum we actually require is 10 working days off per year, separate from sick leave.
If you haven't taken time off by October, your manager will schedule a meeting to talk
about it. We are not interested in unused-PTO trophies.

Health insurance kicks in on day one with no waiting period. The default plan is a PPO
through United Healthcare with no employee premium for individual coverage and a 50%
subsidy for dependents. An HSA-eligible high-deductible plan is available as an
alternative; the company contributes $1,200 per year to the HSA if you pick it.

Parental leave is 16 weeks at full pay, available to any parent regardless of gender or
how the child joined the family. There is no minimum tenure requirement. You can split
the 16 weeks across the first 12 months — take four weeks now, another four when your
partner returns to work, and the rest spread across the year.

The 401(k) vests immediately with a 4% company match. There is no employee contribution
required to get the match, though obviously you should contribute at least enough to
capture it. Open enrollment is in November; outside of that, you can adjust contribution
percentages in Fidelity anytime.

Remote work is the default. The San Francisco and New York offices exist for people who
want them; neither is required. The company pays for a desk at a coworking space if
there's one within 30 miles of your home and you'd rather work there than at home. The
only hard requirement is four hours of overlap with US Pacific time during business days.
