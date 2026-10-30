# Secure Internet access — design and test procedure

**Status: NOT OPERATIONAL. NOT TESTED.** Nothing in this repository has been run over the Internet. This page is the design plus the exact test to run once you supply the accounts.

## Design
Use a private overlay network (Tailscale, or self-hosted WireGuard/Headscale) so the Worker control port is never reachable from the public Internet.

```
Main PC (controller) ── WireGuard tunnel (overlay 100.x / MagicDNS name) ── Worker ── approved client systems
```
* The Worker binds only to its overlay (or LAN) address — never `0.0.0.0` on a public interface. The controller **refuses public addresses** (`netpolicy.assert_private_address`; hostnames are resolved first, so a MagicDNS name works and a public DNS name is rejected). Tested: `test_tenancy.py::test_path_classification`, existing network-policy tests.
* No fixed IPs in code: workers/devices are stored by address *or name*; the dashboard shows how each is reached (`lan`, `overlay`, `loopback`, `link-local`, `blocked`) from `GET /orgs/{id}/fleet`.
* Layered security on top of the tunnel (so a compromised tailnet node is not enough): pinned TLS fingerprint, per-Worker revocable credential, per-client authorization window, per-technician scoped tokens, audit.
* Per-client isolation on the network: use one tailnet tag per client (`tag:client-acme`) and ACLs so a client's machines can reach only their Worker, and the technician PC can reach all Workers. Separate Windows firewall script (`cit-worker firewall-script`) is generated, never applied automatically.
* Alternative if the client cannot install a VPN: an **outbound-only relay** (Worker dials out to a relay you host over TLS; no inbound port at the client). **PLANNED**, not built.

## What you need to provide (blocked on you)
1. A Tailscale (or WireGuard) account of yours; install on the Main PC and on the Worker and sign in. This changes network configuration, so it needs your explicit go-ahead on each machine.
2. A second network to test from (e.g. phone hotspot for the Main PC) — otherwise it is still just the LAN.

## Test to run (then record the result here)
1. Both machines show up in `tailscale status`; note the Worker's name.
2. On the Worker restart the launcher with `--bind <its overlay address>` (edit the Startup-folder launcher; keep the old line commented).
3. Controller: re-pair the Worker using its overlay name; compare fingerprint.
4. Switch the Main PC to the phone hotspot. Run `python tools/worker_check.py` — expect the same 13 checks.
5. Confirm from the hotspot that the Worker's LAN address and port are **not** reachable and that port 8777 is not open on the router.
6. Pass criterion: all checks pass over the overlay AND step 5 shows no public exposure. Until then this feature stays NOT OPERATIONAL.
