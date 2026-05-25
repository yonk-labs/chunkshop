#!/usr/bin/env python3
"""Generate a 1000-record bakeoff corpus across 5 source shapes + 50 gold queries.

Seeded for reproducibility. Each shape gets 200 markdown docs; 10 docs per shape
carry a unique "anchor" fact that a corresponding gold query asks about. The
other 190 docs per shape are topical filler drawn from sentence templates so
chunkers face realistic disambiguation work.

Usage:
    python scripts/gen_bakeoff_corpus.py --out skill-output/bakeoff/synthetic
"""
from __future__ import annotations

import argparse
import random
import re
from pathlib import Path
from textwrap import wrap

import yaml

SEED = 42
N_PER_SHAPE = 200
SHAPES = ["handbook", "sku", "release", "faq", "chat"]

# --- Shared pools ------------------------------------------------------------

COMPANIES = ["Northwind", "Acme", "Globex", "Initech", "Hooli", "Cyberdyne",
             "Wayland", "Tyrell", "Soylent", "Stark Industries"]
TEAMS = ["Platform", "Search", "Data Pipeline", "Frontend", "Security",
         "Observability", "Billing", "Identity", "Storage", "Edge"]
PEOPLE = ["Alice Chen", "Bob Patel", "Carla Reyes", "Dan Ostrowski",
          "Elena Bauer", "Felix Tanaka", "Grace Park", "Henrik Olsen",
          "Isabel Cruz", "Jamal Washington", "Kira Volkov", "Luis Mendoza"]
SERVICES = ["telemetry-hub", "vault-sync", "helio-stream", "ledger-core",
            "cosmos-router", "atlas-index", "quasar-cache", "nebula-queue",
            "aurora-search", "polaris-auth"]
PROJECTS = ["Aurora", "Beacon", "Catalyst", "Delphi", "Everest",
            "Fjord", "Granite", "Halcyon", "Ionic", "Juno"]
REGIONS = ["us-east-1", "us-west-2", "eu-central-1", "ap-northeast-1",
           "sa-east-1", "ap-southeast-2"]
SEVERITIES = ["sev0", "sev1", "sev2", "sev3"]
QUARTERS = ["Q1 2024", "Q2 2024", "Q3 2024", "Q4 2024",
            "Q1 2025", "Q2 2025", "Q3 2025"]


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# --- Anchor facts: (doc_idx_within_shape, anchor_paragraph, gold_query) ------
# Each anchor is a distinctive sentence containing identifiers/numbers that
# don't appear elsewhere in the corpus. Gold queries map to the doc_id where
# the anchor was planted.

ANCHORS = {
    "handbook": [
        (7,  "The Aurora Initiative reduced average p95 query latency by 38 percent during Q3 2024 across all production regions.",
              "how much did the Aurora Initiative reduce query latency"),
        (23, "Production migrations at Hooli require two reviewer approvals from the domain owner group before any DDL is applied.",
              "how many reviewer approvals are needed on production migrations at Hooli"),
        (44, "The Globex on-call rotation hands off every Tuesday at 14:00 UTC and uses a strict twelve-hour shadow window.",
              "when does the Globex on-call rotation hand off"),
        (61, "Initech's blameless post-mortem template requires a contributing-factors table with at least three rows before review.",
              "what does Initech's blameless post-mortem template require"),
        (88, "The Cyberdyne secrets-rotation policy mandates rotation every 47 days for production credentials, validated by automated tooling.",
              "how often does Cyberdyne require production secrets to be rotated"),
        (102, "Wayland's release-train cuts a stable branch on the first Monday of each month at 09:00 local Seattle time.",
              "when does Wayland cut its monthly release-train stable branch"),
        (134, "The Tyrell platform team owns deployment pipelines end-to-end including the bespoke argo-rollouts canary controller.",
              "which team at Tyrell owns the canary controller"),
        (157, "Soylent stores customer PII exclusively in the eu-central-1 region with field-level envelope encryption keyed per tenant.",
              "where does Soylent store customer PII"),
        (172, "The Stark Industries staging environment bakes new builds for a minimum of 18 hours before promotion to production.",
              "how long does the Stark Industries staging environment bake builds"),
        (189, "Acme's principle-of-least-privilege audit runs every 90 days and is owned by the Identity team's compliance squad.",
              "how often does Acme audit principle-of-least-privilege"),
    ],
    "sku": [
        (4,   "SKU NWX-7741 ships exclusively to the European market and uses helium-cooled bearings rated for 24,000 RPM operation.",
              "which SKU ships exclusively to the European market with helium-cooled bearings"),
        (19,  "SKU GLX-3320 is the only catalog item priced at $1,847.99 and includes a five-year on-site service contract.",
              "which SKU is priced at $1,847.99 and includes a five-year on-site service contract"),
        (37,  "SKU HOO-5512 weighs 14.3 kilograms and is the heaviest item in the portable equipment category.",
              "which SKU is the heaviest portable equipment item at 14.3 kilograms"),
        (52,  "SKU CYB-9001 contains a tritium-illuminated dial and is rated waterproof to 300 meters depth.",
              "which SKU has a tritium dial rated waterproof to 300 meters"),
        (76,  "SKU WAY-2204 is bundled with twelve replacement filter cartridges and the proprietary calibration jig.",
              "which SKU comes with twelve replacement filter cartridges"),
        (98,  "SKU TYR-6680 is the limited-edition obsidian variant restricted to 500 units worldwide.",
              "which limited-edition obsidian SKU is restricted to 500 units"),
        (115, "SKU SOY-4413 uses food-grade titanium and is the only catalog item certified for direct contact with consumables.",
              "which SKU uses food-grade titanium for direct contact with consumables"),
        (138, "SKU STK-7799 ships with the integrated lithium-iron-phosphate battery pack providing 96 hours of autonomous operation.",
              "which SKU has a lithium-iron-phosphate pack with 96 hours autonomous operation"),
        (164, "SKU ACM-1100 is the only entry priced under $19.95 and is positioned as the developer-evaluation tier.",
              "which SKU is priced under $19.95 as the developer-evaluation tier"),
        (181, "SKU INI-5050 includes lifetime firmware updates and the bonded-warranty extension covering accidental damage.",
              "which SKU includes lifetime firmware updates and accidental-damage warranty"),
    ],
    "release": [
        (3,   "Release 4.7.2 introduced the streaming JSON parser that reduced peak memory during ingest by 62 percent on multi-gigabyte payloads.",
              "which release introduced the streaming JSON parser that cut peak memory by 62 percent"),
        (17,  "Release 4.9.0 deprecated the legacy XML-RPC endpoint and set a hard removal deadline of 2026-08-15.",
              "which release deprecated XML-RPC with a 2026-08-15 removal deadline"),
        (34,  "Release 5.0.0 added native ARM64 builds for the macOS Apple Silicon platform and dropped 32-bit Linux entirely.",
              "which release added native ARM64 macOS builds and dropped 32-bit Linux"),
        (49,  "Release 5.1.3 patched the timezone-conversion bug in the Pacific/Apia locale that surfaced after the December 2011 date-line shift.",
              "which release patched the Pacific/Apia timezone-conversion bug"),
        (71,  "Release 5.2.0 raised the maximum payload size from 16 megabytes to 128 megabytes after the customer-funded benchmark pass.",
              "which release raised the maximum payload size to 128 megabytes"),
        (86,  "Release 5.3.1 fixed the connection-pool exhaustion under sustained 50,000 concurrent connections with the long-keepalive workload.",
              "which release fixed connection-pool exhaustion at 50000 concurrent connections"),
        (107, "Release 5.4.0 added the experimental WebTransport listener behind the WT_EXPERIMENTAL feature flag and gated to internal users only.",
              "which release added the experimental WebTransport listener behind a feature flag"),
        (129, "Release 5.5.2 changed default TLS minimum to 1.3 and emits a structured warning when 1.2 is forcibly enabled via legacy flag.",
              "which release changed default TLS minimum to 1.3"),
        (153, "Release 5.6.0 introduced the per-tenant rate-limit bucket sized at 7,200 requests per minute by default.",
              "which release introduced the per-tenant 7200-request-per-minute rate limit"),
        (188, "Release 5.7.4 shipped the OpenTelemetry exporter that emits the bespoke billing.usage span attribute required for FY26 reporting.",
              "which release shipped the OpenTelemetry billing.usage span attribute"),
    ],
    "faq": [
        (6,   "Q: How do I reset my MFA device? A: Submit form IT-887 with photo ID; turnaround is one business day during US Pacific working hours.",
              "how do I reset my MFA device"),
        (22,  "Q: What is the data-retention period for raw application logs? A: 14 days hot storage, 90 days cold storage, then permanent deletion.",
              "what is the retention period for raw application logs"),
        (38,  "Q: Can I bring my personal laptop to the office? A: No — only company-issued devices are permitted on the corporate wifi network.",
              "can I bring my personal laptop to the office wifi"),
        (54,  "Q: Where do I file an expense report over $5,000? A: Use Concur Workflow Tier-3 with VP-level approval; standard reports use Tier-1.",
              "where do I file an expense report over $5000"),
        (79,  "Q: How long does the new-hire equipment package take to arrive? A: Five business days from offer-letter signing, shipped via FedEx Priority.",
              "how long does new-hire equipment take to arrive"),
        (95,  "Q: What is the company's sabbatical policy? A: Four weeks paid sabbatical is granted after seven continuous years of full-time employment.",
              "what is the company sabbatical policy"),
        (113, "Q: Can contractors access the production database? A: No — contractors are limited to staging and dev environments without exception.",
              "can contractors access the production database"),
        (141, "Q: How do I request a budget increase for cloud spend? A: Submit RFC-Form-22 through the FinOps portal with twelve months of usage trend data.",
              "how do I request a budget increase for cloud spend"),
        (162, "Q: What antivirus is required on personal Macs used for company work? A: Personal Macs are not authorized; this question itself is out of policy scope.",
              "what antivirus is required on personal Macs"),
        (185, "Q: How do I report a phishing email? A: Forward as attachment to phish-report@company.example and delete from your inbox immediately.",
              "how do I report a phishing email"),
    ],
    "chat": [
        (9,   "alice: the Aurora rollout in eu-central-1 is paused\nbob: which canary stage?\nalice: stage-3, the 25% traffic step\nbob: open INC-44182 and page the platform on-call\nalice: filed, paging now",
              "which canary stage was the Aurora rollout paused at"),
        (24,  "carla: hotfix for the timezone bug is on branch fix/tz-apia-2298\ndan: tests?\ncarla: regression test added in test_locale_apia.py\ndan: ship it after lunch",
              "what branch holds the Apia timezone hotfix"),
        (42,  "elena: customer ACME-CORP wants their data residency moved to ap-northeast-1\nfelix: that's a six-week migration project minimum\nelena: they've signed the SOW for $148,000",
              "what is the SOW value for the ACME-CORP residency migration"),
        (57,  "grace: vault-sync is throwing 503s in us-west-2 only\nhenrik: started 14 minutes ago?\ngrace: yes, correlates with the etcd snapshot job\nhenrik: rolling back etcd, ETA 6 minutes",
              "which region is vault-sync throwing 503s in"),
        (74,  "isabel: the Quasar cache hit rate dropped to 71 percent overnight\njamal: was the bloom-filter size changed?\nisabel: it was, from 32MB to 16MB in last night's deploy\njamal: revert and we'll size it back up after review",
              "what bloom filter size change caused the Quasar cache hit rate drop"),
        (91,  "kira: the new tenant onboarding form fails for company names with apostrophes\nluis: classic escaping bug\nkira: reproduces with O'Brien Industries\nluis: I'll patch the validator tonight",
              "which company name reproduces the apostrophe onboarding bug"),
        (108, "alice: did we ever decide on the rate-limit value for free-tier API keys?\nbob: 200 requests per hour was the final number\nalice: per IP or per key?\nbob: per key, IPs aren't tracked for free tier",
              "what is the rate limit for free-tier API keys"),
        (137, "carla: the Beacon retry-loop is wedging the queue at 9pm Pacific every night\ndan: cron job collision?\ncarla: yes, with the nightly snapshot at 21:00\ndan: I'll move snapshot to 23:30",
              "what is wedging the queue at 9pm Pacific every night"),
        (159, "elena: the new IAM role POLARIS_AUTH_READER was created without the resource scope\nfelix: oh that's open to all secrets\nelena: I've revoked it and re-issued with explicit ARN list\nfelix: backfill the audit log",
              "what was missing from the POLARIS_AUTH_READER IAM role"),
        (183, "grace: tax form deadline reminder — W-9 collection closes Friday at 5pm Eastern\nhenrik: which vendors are still outstanding?\ngrace: 14 vendors, list in the shared sheet\nhenrik: I'll chase them this afternoon",
              "when does the W-9 collection deadline close"),
    ],
}

# Sanity: every shape gets exactly 10 anchors, indices in range
for shape, items in ANCHORS.items():
    assert len(items) == 10, f"{shape} has {len(items)} anchors, expected 10"
    for idx, _, _ in items:
        assert 0 <= idx < N_PER_SHAPE, f"{shape} idx {idx} out of range"


# --- Sentence templates per shape -------------------------------------------

HANDBOOK_TPL = [
    "The {team} team owns the {service} service end-to-end including incident response and capacity planning.",
    "Code review on the {service} repository requires {n} approvals from the {team} group before merge.",
    "Production deploys to {service} in {region} happen on a rolling weekly cadence under {person}'s ownership.",
    "Test coverage targets are set at {n0} percent for new modules and {n1} percent for legacy paths in {service}.",
    "On-call escalation for {service} pages the {team} primary first, secondary after seven minutes.",
    "The {company} security baseline mandates {sev} response for credential leaks in any environment.",
    "Architecture decision records for {service} are stored under docs/adr/{slug} and reviewed quarterly.",
    "Synthetic monitoring for {service} runs every {n} seconds from three geographically separated probes.",
    "The {project} project tracks its OKRs against the {company} platform org's {quarter} planning cycle.",
    "Runbook ownership for {service} sits with the {team} team's on-call engineer of record.",
    "Performance regression budgets for {service} are gated at {n} percent on the p95 read latency dashboard.",
    "Capacity headroom for {service} is maintained at {n} percent of peak observed traffic plus a safety margin.",
    "Cross-region replication for {service} flows from {region} to two follower regions with bounded staleness.",
    "Feature flag changes in {service} require a controlled rollout starting at {n} percent of canary traffic.",
    "Database schema migrations for {service} are reviewed by the {team} team and require a backout plan.",
]

SKU_TPL = [
    "Catalog entry {sku} is part of the {category} family and ships from the {region} fulfillment center.",
    "Item {sku} retails at ${price} with bulk pricing available above 50-unit orders through the {team} sales group.",
    "The {sku} unit measures {dim_cm} centimeters and weighs {weight_kg} kilograms in the standard configuration.",
    "{sku} is stocked in {region} warehouses with a standard lead time of {n} business days for replenishment.",
    "Material composition of {sku} is {material} with a {finish} finish certified to {standard} specifications.",
    "Standard warranty for {sku} is {n} months covering manufacturing defects and shipping damage.",
    "The {sku} bundle includes the base unit plus the {accessory} accessory and a {n}-year support contract.",
    "Spare parts for {sku} are stocked under part number SP-{sku} with same-day shipping inside {region}.",
    "Compatible accessories for {sku} include the {accessory} adapter and the {accessory} extender pack.",
    "Quality-control sampling on {sku} runs at one-in-{n} units with full teardown inspection on flagged batches.",
]

RELEASE_TPL = [
    "Release {ver} addresses {n} customer-reported bugs filed against the {service} component this quarter.",
    "Changelog entry: improved error messaging in {service} when {region} replicas exceed lag thresholds.",
    "Performance: {service} p99 latency improved by {n} percent on the synthetic benchmark suite in {ver}.",
    "Deprecated in {ver}: the legacy {service} configuration key 'enable_v1_protocol'; migration guide in docs.",
    "Security: {ver} addresses CVE-2025-{cve} affecting {service} when configured with the deprecated auth path.",
    "Build: {ver} bumps the Go toolchain to {gov} and the Rust toolchain to {rsv} across all components.",
    "Dependency: {ver} updates the {dep} library to version {n}.{n2}.{n3} for compatibility with {region} TLS certs.",
    "Documentation: {ver} adds a new quickstart guide for the {service} component with {region}-specific examples.",
    "Infrastructure: {ver} no longer ships the bundled {dep} binary; users must install the official upstream package.",
    "Tooling: {ver} introduces a new CLI subcommand 'doctor' that diagnoses common {service} configuration mistakes.",
]

FAQ_FILLER_TPL = [
    "Q: How do I access the {service} dashboard? A: Use SSO at https://{service}.internal.example and authenticate with your corporate account.",
    "Q: Where is the runbook for {service} incidents? A: In the wiki under operations/{service}/runbook with index entries by severity.",
    "Q: Who owns the {service} budget? A: The {team} team in collaboration with the FinOps group at the {company} level.",
    "Q: What is the SLA for {service}? A: 99.9 percent monthly uptime measured by the synthetic-prober suite in {region}.",
    "Q: How do I escalate a {service} outage? A: Page the on-call engineer through the rotation tool; cc the {team} channel.",
    "Q: Can I deploy to {service} on a Friday? A: Only for security or revenue-impacting fixes with director-level approval.",
    "Q: What metrics matter for {service}? A: P95 latency, error rate, and saturation against the {region} capacity headroom.",
    "Q: Where is the data dictionary for {service}? A: Stored under docs/data/{slug}/dictionary.md with column-level descriptions.",
]

CHAT_FILLER_TPL = [
    "{p1}: is {service} healthy in {region}?\n{p2}: green on the dashboard\n{p1}: thanks, closing the ticket",
    "{p1}: PR for {service} ready for review\n{p2}: looking now\n{p2}: lgtm, ship it",
    "{p1}: anyone seeing slow queries on {service}?\n{p2}: no, but the {region} replica was rebooted overnight\n{p1}: ok will check the lag dashboard",
    "{p1}: deploy of {service} {ver} kicking off\n{p2}: canary first?\n{p1}: yes, 5 percent for 30 minutes\n{p2}: ack",
    "{p1}: oncall hand-off — {service} is stable\n{p2}: any open issues?\n{p1}: nothing active, watch the {region} dashboard\n{p2}: got it",
    "{p1}: planning for {project} kickoff\n{p2}: scoping doc done?\n{p1}: draft is in the shared drive\n{p2}: I'll review by EOD",
    "{p1}: alert: {service} error rate above 1 percent in {region}\n{p2}: looking\n{p2}: caused by the failed config push, rolling back",
]


def _fill(tpl: str, rng: random.Random) -> str:
    sku = f"{rng.choice(['NWX','GLX','HOO','CYB','WAY','TYR','SOY','STK','ACM','INI'])}-{rng.randint(1000,9999)}"
    out = tpl.format(
        team=rng.choice(TEAMS),
        service=rng.choice(SERVICES),
        n=rng.randint(2, 95),
        n0=rng.randint(60, 90),
        n1=rng.randint(40, 70),
        n2=rng.randint(0, 99),
        n3=rng.randint(0, 99),
        person=rng.choice(PEOPLE),
        company=rng.choice(COMPANIES),
        sev=rng.choice(SEVERITIES),
        slug=slug(rng.choice(SERVICES)),
        project=rng.choice(PROJECTS),
        quarter=rng.choice(QUARTERS),
        region=rng.choice(REGIONS),
        sku=sku,
        category=rng.choice(["consumer", "industrial", "scientific", "medical", "aerospace"]),
        price=f"{rng.randint(20, 9999)}.{rng.randint(0,99):02d}",
        dim_cm=rng.randint(5, 250),
        weight_kg=round(rng.uniform(0.1, 20.0), 1),
        material=rng.choice(["aluminum", "stainless steel", "carbon fiber", "polycarbonate", "titanium"]),
        finish=rng.choice(["matte", "anodized", "powder-coated", "brushed", "polished"]),
        standard=rng.choice(["MIL-STD-810H", "ISO-9001:2015", "IP-67", "ASTM-D-7264", "EN-60068"]),
        accessory=rng.choice(["pivot-arm", "edge-guard", "carbon-cradle", "induction-coil", "anti-static-mat"]),
        ver=f"5.{rng.randint(0,7)}.{rng.randint(0,9)}",
        cve=rng.randint(10000, 39999),
        gov=f"1.{rng.randint(22,24)}.{rng.randint(0,5)}",
        rsv=f"1.{rng.randint(75,84)}.0",
        dep=rng.choice(["protobuf", "grpc", "openssl", "zstd", "snappy"]),
        p1=rng.choice(PEOPLE).split()[0].lower(),
        p2=rng.choice(PEOPLE).split()[0].lower(),
    )
    return out


# --- Shape generators -------------------------------------------------------

def gen_handbook(idx: int, rng: random.Random, anchor: str | None) -> str:
    company = rng.choice(COMPANIES)
    title = f"{company} Engineering Handbook — Chapter {idx + 1}: {rng.choice(['Operations', 'Architecture', 'Process', 'Security', 'Tooling'])}"
    body = [f"# {title}", ""]
    n_sections = rng.randint(4, 7)
    section_names = rng.sample(
        ["Overview", "Ownership", "Deployment", "Incident Response",
         "Capacity Planning", "Observability", "Security Posture",
         "Change Management", "On-Call", "Tooling"], n_sections)
    anchor_section = rng.randint(0, n_sections - 1) if anchor else -1
    for s_idx, name in enumerate(section_names):
        body.append(f"## {name}")
        body.append("")
        n_para = rng.randint(3, 6)
        anchor_para = rng.randint(0, n_para - 1) if s_idx == anchor_section else -1
        for p_idx in range(n_para):
            sentences = [_fill(rng.choice(HANDBOOK_TPL), rng) for _ in range(rng.randint(3, 6))]
            if p_idx == anchor_para:
                sentences.insert(rng.randint(0, len(sentences)), anchor)
            body.append(" ".join(sentences))
            body.append("")
    return "\n".join(body)


def gen_sku(idx: int, rng: random.Random, anchor: str | None) -> str:
    sku = f"{rng.choice(['NWX','GLX','HOO','CYB','WAY','TYR','SOY','STK','ACM','INI'])}-{rng.randint(1000,9999)}"
    body = [
        f"# Product: {sku}",
        "",
        f"**Category:** {rng.choice(['consumer','industrial','scientific','medical','aerospace'])}  ",
        f"**Region:** {rng.choice(REGIONS)}  ",
        f"**Status:** {rng.choice(['active', 'limited', 'discontinued-soon'])}  ",
        "",
        "## Description",
        "",
    ]
    n_sentences = rng.randint(4, 8)
    sentences = [_fill(rng.choice(SKU_TPL), rng) for _ in range(n_sentences)]
    if anchor:
        sentences.insert(rng.randint(0, len(sentences)), anchor)
    body.append(" ".join(sentences))
    body.extend(["", "## Specifications", ""])
    n_spec = rng.randint(3, 5)
    for _ in range(n_spec):
        body.append(f"- {_fill(rng.choice(SKU_TPL), rng)}")
    return "\n".join(body)


def gen_release(idx: int, rng: random.Random, anchor: str | None) -> str:
    ver = f"5.{rng.randint(0,7)}.{rng.randint(0,9)}"
    date = f"2025-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}"
    body = [
        f"# Release Notes — {ver}",
        "",
        f"**Date:** {date}  ",
        f"**Train:** {rng.choice(['stable', 'beta', 'edge'])}  ",
        "",
        "## Summary",
        "",
    ]
    n_intro = rng.randint(2, 4)
    intro = [_fill(rng.choice(RELEASE_TPL), rng) for _ in range(n_intro)]
    if anchor:
        intro.insert(rng.randint(0, len(intro)), anchor)
    body.append(" ".join(intro))
    body.extend(["", "## Changes", ""])
    n_items = rng.randint(5, 12)
    for _ in range(n_items):
        body.append(f"- {_fill(rng.choice(RELEASE_TPL), rng)}")
    return "\n".join(body)


def gen_faq(idx: int, rng: random.Random, anchor: str | None) -> str:
    topic = rng.choice(["IT Support", "HR Policy", "Engineering", "Finance",
                         "Security", "Onboarding", "Travel", "Procurement"])
    body = [f"# FAQ — {topic} (entry {idx + 1})", ""]
    n_pairs = rng.randint(3, 5)
    pairs = [_fill(rng.choice(FAQ_FILLER_TPL), rng) for _ in range(n_pairs)]
    if anchor:
        pairs.insert(rng.randint(0, len(pairs)), anchor)
    for p in pairs:
        body.append(p)
        body.append("")
    return "\n".join(body)


def gen_chat(idx: int, rng: random.Random, anchor: str | None) -> str:
    body = [
        f"# Chat Session — {rng.choice(['#platform','#oncall','#release','#security','#data'])} ({idx + 1})",
        "",
        f"**Date:** 2025-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}  ",
        f"**Participants:** {rng.choice(PEOPLE)}, {rng.choice(PEOPLE)}",
        "",
        "## Transcript",
        "",
        "```",
    ]
    n_threads = rng.randint(2, 4)
    threads = [_fill(rng.choice(CHAT_FILLER_TPL), rng) for _ in range(n_threads)]
    if anchor:
        threads.insert(rng.randint(0, len(threads)), anchor)
    for t in threads:
        body.append(t)
        body.append("")
    body.append("```")
    return "\n".join(body)


GEN = {
    "handbook": gen_handbook,
    "sku": gen_sku,
    "release": gen_release,
    "faq": gen_faq,
    "chat": gen_chat,
}


# --- Main --------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="skill-output/bakeoff/synthetic-corpus",
                    help="Output directory (default: skill-output/bakeoff/synthetic-corpus)")
    args = ap.parse_args()

    out_root = Path(args.out).resolve()
    corpus_dir = out_root / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(SEED)
    gold = []

    for shape in SHAPES:
        anchor_map = {idx: (text, query) for idx, text, query in ANCHORS[shape]}
        for i in range(N_PER_SHAPE):
            doc_id = f"{shape}-{i:04d}"
            anchor_pair = anchor_map.get(i)
            anchor_text = anchor_pair[0] if anchor_pair else None
            content = GEN[shape](i, rng, anchor_text)
            (corpus_dir / f"{doc_id}.md").write_text(content, encoding="utf-8")
            if anchor_pair:
                gold.append({"query": anchor_pair[1], "gold_doc_id": doc_id})

    gold_path = out_root / "gold.yaml"
    gold_path.write_text(yaml.safe_dump(gold, sort_keys=False, allow_unicode=True), encoding="utf-8")

    print(f"Wrote {len(SHAPES) * N_PER_SHAPE} docs to {corpus_dir}")
    print(f"Wrote {len(gold)} gold queries to {gold_path}")
    by_shape = {s: 0 for s in SHAPES}
    for g in gold:
        by_shape[g["gold_doc_id"].split("-")[0]] += 1
    print(f"Gold breakdown: {by_shape}")


if __name__ == "__main__":
    main()
