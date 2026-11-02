# Third-party notices

OpenMove is proprietary software (see [LICENSE](LICENSE) and [LEGAL.md](LEGAL.md)). It may include or depend on the open-source components listed below. Each remains under its own licence, which OpenMove's licence does not change.

## Components

OpenMove's source uses the Python standard library only; it imports no third-party package (a test enforces this). A dependency is added here, with its name, version, licence and source, only after its licence is checked to allow use inside proprietary software.

When OpenMove is packed into a standalone program (`tools/build/build_exe.py`), the program contains the CPython runtime and the parts of the Python standard library it uses, packed by PyInstaller. Those are distributed under their own licences, listed below. Their licence texts must accompany a distributed program.

| Component | Version | Licence | Source |
|---|---|---|---|
| CPython runtime and standard library (inside the standalone program only) | the version used for the build | Python Software Foundation License | https://www.python.org/ |
| PyInstaller bootloader (inside the standalone program only; the build tool itself is not distributed) | the version used for the build | GPL-2.0-or-later with the PyInstaller bootloader exception, which permits use in programs under any licence | https://pyinstaller.org/ |

## Trademarks

* "OpenStack" is a registered trademark of the OpenInfra Foundation. "Cinder", "Keystone", "Nova" and "Glance" are OpenStack project names. OpenMove is an independent tool and is not affiliated with, endorsed by or sponsored by the OpenInfra Foundation or the OpenStack project.

## Data

OpenMove ships no third-party data. At run time it reads the user's own OpenStack cloud with the user's own credentials. That data stays on the machine that runs OpenMove and is not part of this repository.
