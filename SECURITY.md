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
