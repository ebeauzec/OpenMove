# Network destinations

Requirement R2: OpenMove talks to OpenStack and nothing else, and every destination is written down. This page is that list. A test (`tests/test_network.py`) fails when code opens a network connection somewhere this page does not name, so adding a destination is a documented decision, never an accident.

## What OpenMove connects to

| Destination | Why | Where in the code | When |
|---|---|---|---|
| The identity (Keystone) address the operator types in Connect | Log in, and read the service catalogue | `connectors/httpclient.py` (the only HTTP client) | Every real session |
| The block storage (Cinder) endpoint from that catalogue | Read volumes, snapshots, types, pools and services; send `os-retype` (the only change to a volume) | `connectors/httpclient.py`, changes only through `engine/actions.py` | Every real session; changes only in a run |
| The compute (Nova) endpoint from that catalogue | Read instances and hypervisors; lock and unlock an instance around a move | the same | Every real session; changes only in a run |
| The OpenStack lab on this machine (identity, Horizon) | Lab mode only: find the lab's address, check that its dashboard answers | `lab/__init__.py` | `--lab` only |

Nothing else is contacted. The operator's machine never connects to a Cinder host, a compute host, a database or an array in this version (those accesses belong to the staged method, which is not built).

## What listens

| Listener | Detail |
|---|---|
| The local interface | `127.0.0.1:8765` by default (`--host`, `--port`). Every call needs the token printed at start. The container image listens on all addresses inside the container; publish its port on `127.0.0.1` (`Dockerfile`). |

## What OpenMove never does

* No telemetry, no update check, no licence call-home, no external lookup of any kind.
* No font, script, style sheet or image is loaded by the pages from outside; the pages carry a strict content security policy (`tests/test_self_contained.py`).
* No package is installed or downloaded at run time: the program uses the Python standard library only (`tests/test_self_contained.py`).
* The password is sent once, to the identity address the operator typed, and kept in memory for the session. It is never written to disk, to the journal, to a report or to a response.

## TLS

Certificates are verified by default. The operator can give a CA file for a private CA, or turn verification off; turning it off is shown on the Platform step and stated in every report.

## Where OpenMove writes

Only its own data folder (the journal and the evidence folder per volume), the reports it hands to the browser to save, and, in lab mode, the lab's data folder. See `INSTALL.md` for the paths.
