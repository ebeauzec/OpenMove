# Network destinations

Requirement R2: OpenMove talks to OpenStack and nothing else, and every destination is written down. This page is that list. A test (`tests/test_network.py`) fails when code opens a network connection somewhere this page does not name, so adding a destination is a documented decision, never an accident.

## What OpenMove connects to

| Destination | Why | Where in the code | When |
|---|---|---|---|
| The identity (Keystone) address the operator types in Connect | Log in, and read the service catalogue | `connectors/httpclient.py` (the only HTTP client) | Every real session |
| The block storage (Cinder) endpoint from that catalogue | Read volumes, snapshots, types, pools and services; send `os-retype` (the only change to a volume) | `connectors/httpclient.py`, changes only through `engine/actions.py` | Every real session; changes only in a run |
| The compute (Nova) endpoint from that catalogue | Read instances and hypervisors; lock and unlock an instance around a move | the same | Every real session; changes only in a run |
| A migration host and a Cinder host a person registered on the Hosts tab (class C, the staged method) | Run the data mover and the Cinder helper; collect diagnostics for a support bundle. Commands only, no data stream: the volume data travels between the arrays and the migration host, never through OpenMove. | `connectors/ssh.py`: the system's own OpenSSH client (`ssh`, `ssh-keygen`, `ssh-keyscan`), keys only, host key pinned by a person | Only when hosts are registered; changes only in a staged run (`MOVER.md`) |
| A new host that a person is setting up (address and port typed on the Hosts tab), once | Read its host key; log in with the administrator password, prepare it and set up its storage paths. The only place where a password is used for SSH; it is held in memory and in the environment of the `ssh` process, never stored | `connectors/ssh.py` (`RootLogin`), `bootstrap.py` | Only when a person presses **Set up the host** |
| The two WSL distros of the lab on this machine (`wsl.exe`, then SSH as above) | Lab mode only: start them, install sshd and the data mover's needs, read their address, and register them as the lab's migration host and Cinder host | `lab/hosts.py` (runs `wsl.exe`; no network connection of its own) | `--lab` only |
| This machine only (no destination) | The installation self-check: reads the program's own files and runs `ssh -V` to read the OpenSSH client's version | `selfcheck.py` (runs the local `ssh`; opens no connection) | `--self-check`, and on request |
| The OpenStack lab on this machine (identity, Horizon) | Lab mode only: find the lab's address, check that its dashboard answers | `lab/__init__.py` | `--lab` only |

Nothing else is contacted. OpenMove never connects to a database, and never to an array's management address: the migration host, which the person registered, reaches the arrays' storage. A Cinder host or migration host is reached only after a person registered it and pinned its host key. No password is ever used: SSH logs in with OpenMove's own key pair, in the data folder.

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
