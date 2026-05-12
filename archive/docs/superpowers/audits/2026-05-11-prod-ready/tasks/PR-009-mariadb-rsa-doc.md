# PR-009 — Document Marvin Attack mitigation in MariaDB engine doc

**Priority:** P3
**Effort:** XS (~15 min)
**Dependencies:** none
**GAP-IDs:** GAP-008

## Problem

`cargo audit` flags **RUSTSEC-2023-0071** — `rsa 0.9.10` Marvin Attack timing sidechannel. Affects chunkshop's MariaDB code path transitively via `sqlx-mysql`. No upstream fix is available. The risk is narrow but real for users connecting to MariaDB across untrusted networks with RSA-based auth.

## Solution

Add a security note to `docs/engines/mariadb.md` covering the attack precondition and recommended mitigation.

### Suggested addition

Insert a new section in `docs/engines/mariadb.md`, just before "When to use MariaDB":

```markdown
## Security note: transitive `rsa` Marvin Attack CVE

chunkshop's MariaDB code path uses `sqlx-mysql` (Rust) and `pymysql` (Python).
`sqlx-mysql` pulls in the `rsa` crate transitively for RSA-based MariaDB auth
plugins. As of v0.4.0, `rsa 0.9.10` carries RUSTSEC-2023-0071 (Marvin Attack:
potential key recovery through timing sidechannels). **No upstream fix is
available.**

**Risk:** An adversary on the network path between chunkshop and MariaDB,
with the ability to observe many TLS / auth handshakes, could recover RSA
private key bits over time. Requires (a) MariaDB configured to use an
RSA-based auth plugin (`caching_sha2_password` or `sha256_password`), AND
(b) network position to observe handshake timing.

**Mitigation for users on untrusted networks:**

1. Prefer `mysql_native_password` auth, which doesn't use RSA:
   ```sql
   ALTER USER 'chunkshop_user'@'%' IDENTIFIED WITH mysql_native_password BY '...';
   ```
2. Terminate TLS outside chunkshop (e.g., at a sidecar / proxy) so the
   handshake timing is in a sealed context.
3. Watch the `rsa` crate for a Marvin-resistant release; chunkshop will bump
   as soon as one ships.

**This risk does NOT apply to:**
- Local MariaDB connections (loopback).
- MariaDB in a trusted VPC with no adversarial network position.
- Connections using `mysql_native_password` (RSA path is not exercised).
```

## Acceptance Criteria

- [ ] `docs/engines/mariadb.md` has a "Security note: transitive `rsa` Marvin Attack CVE" section.
- [ ] The section appears before "When to use MariaDB" so users evaluating fit see it.
- [ ] The section names the RUSTSEC ID and the precondition.

## Risk if Skipped

A user deploying chunkshop into an architecture where the precondition applies (rare but possible) has no signal in chunkshop's docs telling them so. They'd find it only by running `cargo audit` themselves.
