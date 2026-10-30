# Security notes

**Status: not audited, not production-ready.** See PROJECT_STATUS.md for what is untested.

## Threat model (summary)
| Threat | Control | Tested |
|---|---|---|
| Unauthorized device access | pairing code + owner authorization record + consented time-boxed session; revoke | yes (mock) |
| Brute-forcing pairing/consent codes | 6-digit, expiry, 5-attempt throttle | yes |
| Command injection via AI/UI | allowlist, param regex/int ranges, base64 transport, console grammar | yes (unit) |
| Over-broad AI power | Claude has no tools; plans from allowlist; approvals | yes |
| Silent destructive change | high-risk per-action confirmation; approval hash; audit | yes |
| False "fixed" claims | post-repair verification | yes |
| Exposure to the Internet | private-address policy; API binds 127.0.0.1; bearer token | yes (policy/token) |
| MITM on SSH | pinned host key, `StrictHostKeyChecking=yes`, key-only, BatchMode | argv tested; not against a real host |
| Credential theft at rest | DPAPI; **refuses to store off Windows** | refusal tested; DPAPI untested |
| Log tampering | SHA-256 hash chain (tamper-*evident* only; an attacker who rewrites the whole DB can rebuild it) | yes |
| Data leakage to cloud AI | redaction + minimal findings; offline queue stores ids/severity only | yes |

## Known gaps
No client agent (consent is attested by the technician for real devices) · no per-user accounts · WinRM has no key pinning · no signing certificate · no external pentest · PowerShell scripts unreviewed on real Windows · dependency pinning/SBOM absent.

## Operating guidance
Only connect devices whose owner authorized you. Never forward ports for SSH/WinRM; use LAN, Ethernet or Tailscale. Use a dedicated low-privilege Windows account; grant admin only where a repair needs it.

## M2 additions
| Threat | Control | Tested |
|---|---|---|
| Agent impersonation / MITM | self-signed cert per run; fingerprint read from the client's screen and pinned on every connection | yes |
| Rogue controller on the LAN | pairing code (8 digits, single use, 10 min, lockout after 5) + session token (random 256-bit) | yes |
| Agent reachable from the Internet | refuses non-private source addresses; controller refuses public targets (except opt-in key-only SSH to your own VPS) | yes |
| Owner loses control | `d`+Enter / Ctrl+C / close window; session expiry; controller revoke and emergency stop | yes |
| Escape from project folder | realpath confinement incl. symlinks; no directory deletion; size caps | yes |
| Shell injection | argv-only execution; server args validated + shlex-quoted through ssh | yes |
| Personal credentials leaking to clients | no credential field in the agent protocol; agent rejects unknown fields; remote Claude uses the client's own login; generic exec env is scrubbed of KEY/TOKEN/SECRET/PASSWORD | yes |
| Secrets in logs | argv redaction (passwords/tokens/API keys) in audit and task events | yes |
| Cross-client leakage | tasks/projects bound to one device (403 otherwise); separate workspace + backup folders per device/project | yes |
| Destructive/production change | high risk = per-action confirmation; production = typed project/server name; deletions in a push upgrade it to high | yes |
| Prompt injection via project files | Bash off by default, approvals before push, backups, verification — **mitigation, not elimination** | partly |
Not covered: OS-level sandbox for Central Claude, agent code signing (needs a certificate), endpoint-security (EDR) interplay — if security software blocks the agent, do not bypass it.
