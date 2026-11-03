# Third-party notices

OpenMove is proprietary software (see [LICENSE](LICENSE) and [LEGAL.md](LEGAL.md)). It may include or depend on the open-source components listed below. Each remains under its own licence, which OpenMove's licence does not change.

## Components

OpenMove's source uses the Python standard library only; it imports no third-party package (a test enforces this). A dependency is added here, with its name, version, licence and source, only after its licence is checked to allow use inside proprietary software.

When OpenMove is packed into a standalone program (`tools/build/build_exe.py`), the program contains the CPython runtime and the parts of the Python standard library it uses, packed by PyInstaller. Those are distributed under their own licences, listed below. Their licence texts must accompany a distributed program.

| Component | Version | Licence | Source |
|---|---|---|---|
| CPython runtime and standard library (inside the standalone program only) | the version used for the build | Python Software Foundation License | https://www.python.org/ |
| PyInstaller bootloader (inside the standalone program only; the build tool itself is not distributed) | the version used for the build | GPL-2.0-or-later with the PyInstaller bootloader exception, which permits use in programs under any licence | https://pyinstaller.org/ |
| open-iscsi (`iscsiadm`, `iscsid`) and multipath-tools (`multipath`, `multipathd`), installed by `configure_storage.sh` on a migration host from the host's own package repository; used as tools, not linked and not copied into OpenMove | the host's | GPL and LGPL (see each package) | https://github.com/open-iscsi/open-iscsi , http://christophe.varoqui.free.fr/ |
| OpenSSH client (`ssh`, `ssh-keygen`, `ssh-keyscan`), used as a **tool**, not linked and not copied into OpenMove; the container image installs it from the distribution's own package repository | the system's | BSD-style (OpenBSD) licences | https://www.openssh.com/ |
| Host utilities on a migration host (LVM2, util-linux, coreutils), run by the data mover on that host; installed by the host setup script from the host's own repository, never shipped by OpenMove | the host's | GPL-2.0 and LGPL-2.1 | the host's distribution |

## Trademarks

* "OpenStack" is a registered trademark of the OpenInfra Foundation. "Cinder", "Keystone", "Nova" and "Glance" are OpenStack project names. OpenMove is an independent tool and is not affiliated with, endorsed by or sponsored by the OpenInfra Foundation or the OpenStack project.

## Data

OpenMove ships no third-party data. At run time it reads the user's own OpenStack cloud with the user's own credentials. That data stays on the machine that runs OpenMove and is not part of this repository.
