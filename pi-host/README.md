# pi-host — independent local pi RPC host

P is a Python-standard-library-only process/transport mechanism. Copy this
whole directory anywhere and run it without agentd, a workspace, persona assets,
`AGENTD_DIR` or `PYTHONPATH`. The platform repository is only its distribution
container; there is no runtime dependency on another module.

## Responsibility

P starts one `pi --mode rpc`, owns its stdin/stdout/stderr PIPEs, drains output
without an observer, serves one replaceable Unix-socket client, replays a bounded
raw event ring plus live native UI requests, and reports the actual child wait
result. It never restarts pi, replays inbound commands, prompts a model on its
own, interprets task reports or participates in scheduling/registration/mailboxes.

The caller explicitly supplies paths, cwd, executable, `-e` pairs and the child
environment. P does not discover the caller's machine/workspace configuration or
scrub agent identity policy. The inherited real `RUND_LEASE_FD`, if supplied by a
runtime, is propagated as an actual fd, not synthesized. All host I/O and local
Linux/Darwin process identities are implemented inside this directory.

## Launch

```sh
python3 pi_host.py /absolute/private/declaration.json
```

The declaration has exactly these keys:

```json
{
  "sessionFile": "/absolute/private/history.jsonl",
  "cwd": "/absolute/existing/directory",
  "sessionName": "example",
  "piBinary": "/absolute/bin/pi",
  "socketPath": "/absolute/private/s",
  "extensionArgv": [],
  "env": {"HOME": "/absolute/private/home", "PATH": "/usr/bin:/bin"}
}
```

The directory and all configuration are caller-owned. Keep the socket path short
for platform AF_UNIX limits. Each explicit invocation starts one child; no
transparent recovery loop exists. `host_started` identifies P and pi only after
pi has been spawned; `child_wait` carries the real return code. The host exits
with pi's exit code (a signal maps to 128+signal). P crashing cannot manufacture
pi's wait/returncode. Stderr is drained/flushed promptly and a bounded tail stays
in memory. A slow observer is dropped after a bounded send timeout rather than
blocking pi indefinitely.

## Socket and acceptance

P creates no cross-process file locks. Use one agreed endpoint per session.
Existing live-owner/unknown-endpoint checks and bind errors are conservative,
not a lease, a winner election or simultaneous-start uniqueness guarantee.
Metadata writes use unique temporary files and atomic replacement. Cleanup only
unlinks the inode P actually bound; it does not delete a new owner's endpoint.

After launching P, the W caller connects to the fixed endpoint, checks kernel
peer PID/uid plus start identity against THIS invocation's Popen, and sends a
unique-id `get_state`. Only its matching successful response accepts startup.
No request is sent to a mismatched peer. This belongs to the launching caller,
not a pre-start arbitration protocol inside P. macOS signals use accepted
check-identity-then-kill semantics, not atomic process handles; see
[LOCAL-PROCESS.md](LOCAL-PROCESS.md).

## Boundary and migration

Agent task/resident policy is now [agentd's adapter](../agentd/RPC-ADAPTER.md).
It imports this generic `Transport` and adds agent-only file locks, identity
hooks, prompt delivery, receiver handshake, persona injection, convergence and
report/error gates. Those files/tests/assets do not remain in P. The dependency
is **agentd → P**, never the reverse; old P paths are removed, not forwarding
shims.

`rpc_dialogs.py` is the native pi RPC in-memory UI baseline. It was moved from
A with its sole implementation; A now consumes P. This is not persistent task
wait detection. `host_io.py` independently implements only the tiny local owner
record operations needed by P; it is not a copy of agentd/proto.py.

## Tests

```sh
python3 -B test_standalone.py
python3 -B test_local_process.py
python3 -B test_generic.py  # Linux-only pidfd/orphan scenarios
```

The standalone suite copies only P into a fresh unrelated directory, poisons
agent discovery, forbids A module imports/reads, and proves a real socket peer,
matching `get_state`, real exit 7 and zero file locks. Native process facts cover
Linux and Darwin (with an explicit Linux-only oracle skip on Mac). Mutable test
roots are isolated and retained. Agent lifecycle/persona tests live in agentd.
These are candidate tests, not production, browser/model/receiver acceptance or
permission to reopen separately suspended experiments.
