CLAUDE IT TECHNICIAN - WORKER (manual mode)

Different program from CITAgent: the worker has its own commands. It keeps a stable identity (so the fingerprint does not change
on every start), the technician PC enrolls ONCE with a one-time code, and access can be revoked at any time.

Order (all on THIS PC, nothing is installed, no startup task, no firewall change):
  1. install-worker.bat   copies cit-worker.exe to C:\CITWork\worker and verifies its SHA-256 (never overwrites)
  2. run-worker.bat       asks for this PC's private LAN IPv4 address, then runs the worker in a visible window (port 8777)
                          Windows Firewall may ask: tick PRIVATE networks only (your decision).
                          The window shows: Fingerprint, "Secret store: OS-backed" (must say OS-backed), Authorized, Locked.
  3. enroll-worker.bat    (second window) prints a one-time 8-digit code. Give the technician the code AND the fingerprint.
  4. lock-worker.bat      stop everything now / revoke the technician PC.  Also: create a file named STOP in C:\CITWork\worker\data.

Data (identity, key protected by Windows DPAPI, database, per-client workspaces) lives in C:\CITWork\worker\data.
The old agent's files in C:\CITWork are not touched. The worker confines each client to C:\CITWork\worker\data\workspaces\<client>.
Claude Code and Windows repair tools are NOT used unless the technician creates such a task and you allow it.
