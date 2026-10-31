# Connecting a new customer computer (operator guide)

Prerequisite: the customer has authorized the work in writing (who, when, scope). You are the owner or an administrator of their client record.

1. **Dashboard -> Clients & sites**: add the client (owner only) and its sites (office, branch...).
2. **On the customer PC** (a normal Windows user is enough; everything is visible, nothing is hidden):
   - copy `cit-worker.exe` to a folder you choose and compare its SHA-256 with the value you recorded;
   - on the same LAN nothing else is needed; over the Internet join the private network (Tailscale/WireGuard) first - *the Internet path is not tested yet*;
   - run `cit-worker.exe --data-dir <folder>\data run --bind <the PC's private or overlay address>`; it prints a **fingerprint**;
   - run `cit-worker.exe --data-dir <folder>\data authorize --days 30`, then `... enroll` to get a one-time 8-digit code.
3. **Dashboard -> AI Worker Stations -> Add worker**: name, address (an overlay DNS name works), then compare the fingerprint **group by group** with the PC's screen and enter the code.
4. **Clients & sites**: assign the machine to the client and a site. The plan limits the number of machines.
5. **Devices**: open the machine and press *Collect now* for its inventory. **Monitoring**: add threshold rules and an e-mail policy.
6. **Start at logon (no administrator needed)**: put a `.cmd` launcher in the user's Startup folder (see `docs/WORKER-ALWAYS-ON.md`). A Windows Service needs an administrator and is **not automated**.
7. **People**: *Technicians & roles* -> invite administrators, technicians, viewers or customers (single-use invitation tokens, shown once).
8. **Offboarding**: *Client settings -> Offboard* revokes access and Worker credentials and starts the retention clock. On any Worker that was offline run `cit-worker.exe revoke-all` (and `wipe` if the machine leaves the contract). After the retention period the owner can purge.

What the customer can rely on: only approved operations run, changes need a second person's approval, every action is in the client's audit trail, and they can stop it at any time by closing the Worker window or running `cit-worker.exe lock`.
