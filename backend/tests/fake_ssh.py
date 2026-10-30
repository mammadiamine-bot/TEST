#!/usr/bin/env python3
"""TEST DOUBLE for the `ssh` binary: drops ssh options, then runs the remote command string through `sh -c`,
exactly like an SSH server's login shell would. Lets us prove quoting survives a shell round-trip."""
import subprocess, sys
a = sys.argv[1:]
i = 0
while i < len(a):
    if a[i] in ("-p", "-i", "-o"): i += 2
    else: break
host, rest = a[i], a[i + 1:]
if rest and rest[0] == "--": rest = rest[1:]
sys.exit(subprocess.run(["sh", "-c", " ".join(rest)], stdin=sys.stdin).returncode)
