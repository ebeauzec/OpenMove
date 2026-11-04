# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The SSH connector (docs/MOVER.md, docs/NETWORK.md): how OpenMove reaches a migration host and a Cinder host.

The client is the system's own OpenSSH (`ssh`, `ssh-keygen`, `ssh-keyscan`), started with an argument list and never through a shell. Why:
it is on Windows 10 and later, macOS and every Linux; it needs no package, so OpenMove stays standard-library only; it is the client
administrators already trust and audit; and it brings key authentication, host-key checking, agents and jump hosts for free.

Rules that are not options:
  * keys only (BatchMode) for every host that is registered: OpenMove never stores a password. The one exception is the setup of a new host
    (`RootLogin`, docs/DEPLOYMENT.md): a person types the host's administrator password on the Hosts tab, OpenMove uses it for that setup only
    (to install its own key and the setup), keeps it only in memory and in the environment of the ssh process, and never writes it anywhere;
  * the host key is pinned in OpenMove's own known_hosts file after a person confirmed its fingerprint (StrictHostKeyChecking=yes); a
    changed key refuses the connection;
  * every call has a timeout; a call that was cut off after it was sent is reported as an unknown outcome, never retried here;
  * a remote command is built from a list of arguments, each quoted for the remote shell.

A `Local` transport runs the same commands on the machine OpenMove runs on (OpenMove installed on the migration host itself, and the tests).
"""
import base64
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

CONNECT_TIMEOUT = 10
MOVER_DIR = "resources/mover"
TRACE = None            # set by diag.py: called with one dict per command run, so a failure on a dark site can be read afterwards
TRACE_LIMIT = 65536     # characters of output kept per command


class SshError(Exception):
    """A remote call failed. outcome_unknown is true when the command may have run (cut off after it was sent)."""

    def __init__(self, message, outcome_unknown=False, rc=None):
        super().__init__(message)
        self.outcome_unknown, self.rc = outcome_unknown, rc


def resource(name):
    return (Path(__file__).resolve().parent.parent / MOVER_DIR / name).read_bytes()


def sha8(data):
    return hashlib.sha256(data).hexdigest()[:12]


def tool(name):
    """The path of an OpenSSH program, or a clear error."""
    p = shutil.which(name)
    if not p:
        raise SshError("the program %r was not found. Install the OpenSSH client (Windows: Settings > Optional features > OpenSSH Client; "
                       "Linux: openssh-client)" % name)
    return p


SECRET_KEYS = ("password", "secret", "chap_secret", "token")


def scrub_args(args):
    """The arguments of a mover verb as they may be traced: a secret is never written to the call log."""
    if not isinstance(args, dict):
        return args
    return {k: ("***" if k in SECRET_KEYS else v) for k, v in args.items()}


def trace(entry):
    if TRACE:
        try:
            TRACE(entry)
        except Exception:                      # tracing must never break a call
            pass


def run_local(argv, stdin=None, timeout=60, env=None, cwd=None, detach=False):
    """Run a program with an argument list. detach: no controlling terminal (ssh must then ask for a password through SSH_ASKPASS, not the console)."""
    started = time.time()
    try:
        p = subprocess.run(argv, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, env=env, cwd=cwd, shell=False,
                           start_new_session=bool(detach and os.name != "nt"))
    except subprocess.TimeoutExpired as exc:
        trace({"kind": "process", "argv": [str(a) for a in argv], "error": "timeout after %d s" % timeout, "ms": int((time.time() - started) * 1000),
               "stdout": (exc.stdout or b"")[:TRACE_LIMIT].decode("utf-8", "replace"), "stderr": (exc.stderr or b"")[:TRACE_LIMIT].decode("utf-8", "replace")})
        raise SshError("%s did not finish within %d s" % (Path(argv[0]).name, timeout), outcome_unknown=True) from None
    except OSError as exc:
        trace({"kind": "process", "argv": [str(a) for a in argv], "error": "could not start: %s" % exc})
        raise SshError("could not start %s: %s" % (argv[0], exc)) from None
    trace({"kind": "process", "argv": [str(a) for a in argv], "rc": p.returncode, "ms": int((time.time() - started) * 1000),
           "stdin_bytes": len(stdin or b""), "stdout": p.stdout[:TRACE_LIMIT].decode("utf-8", "replace"), "stderr": p.stderr[:TRACE_LIMIT].decode("utf-8", "replace")})
    return p.returncode, p.stdout, p.stderr


# ---- keys and host keys -------------------------------------------------------------------------------------------------------------
def ssh_dir(home):
    d = Path(home) / "ssh"
    d.mkdir(parents=True, exist_ok=True)
    return d


def key_path(home):
    return ssh_dir(home) / "id_ed25519"


def ensure_key(home):
    """OpenMove's own key pair, made once. The private key stays in OpenMove's data folder; only the public key is ever shown."""
    k = key_path(home)
    if not k.exists():
        rc, out, err = run_local([tool("ssh-keygen"), "-q", "-t", "ed25519", "-N", "", "-C", "openmove", "-f", str(k)], timeout=60)
        if rc != 0:
            raise SshError("ssh-keygen failed: %s" % err.decode("utf-8", "replace").strip()[:300])
    protect_key(k)
    return public_key(home)


def protect_key(path):
    """Only the owner may read the private key. ssh refuses a key that others can read ("UNPROTECTED PRIVATE KEY FILE"). On Windows a file takes the
    permissions of its folder, which usually lets every user read it, so the inherited rights are removed and the current user is given full control."""
    path = str(path)
    if os.name != "nt":
        os.chmod(path, 0o600)
        return
    who = "%s\\%s" % (os.environ.get("USERDOMAIN") or ".", os.environ.get("USERNAME") or "")
    if who.endswith("\\"):
        return
    try:
        subprocess.run(["icacls", path, "/inheritance:r", "/grant:r", "%s:F" % who], capture_output=True, timeout=30, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass


def public_key(home):
    p = Path(str(key_path(home)) + ".pub")
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def known_hosts(home):
    return ssh_dir(home) / "known_hosts"


def scan_host_key(address, port=22):
    """The host key the machine offers now: [{'type', 'fingerprint', 'line'}]. Nothing is trusted yet.

    The key is read by letting ssh connect once with a throwaway known_hosts file (the connection stops at authentication). ssh-keyscan is not
    used: some builds (the one in Windows, for example) cannot agree a key exchange with a current sshd."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        kh, cfg = Path(d) / "known_hosts", Path(d) / "empty_config"
        cfg.write_text("", encoding="utf-8")
        run_local([tool("ssh"), "-p", str(int(port)), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=%s" % kh,
                   "-o", "ConnectTimeout=%d" % CONNECT_TIMEOUT, "-o", "PasswordAuthentication=no", "-o", "PreferredAuthentications=none",
                   "-o", "LogLevel=ERROR", "-o", "IdentitiesOnly=yes", "-F", str(cfg), "%s@%s" % ("openmove-probe", address), "exit"], timeout=40)
        lines = [ln.strip() for ln in kh.read_text(encoding="utf-8").splitlines() if ln.strip()] if kh.exists() else []
    if not lines:
        raise SshError("no host key from %s:%s (is sshd running, and is the port open?)" % (address, port))
    keys = []
    for ln in lines:
        rc, fp, _ = run_local([tool("ssh-keygen"), "-l", "-f", "-"], stdin=(ln + "\n").encode(), timeout=20)
        parts = fp.decode("utf-8", "replace").split()
        keys.append({"type": ln.split()[1], "fingerprint": parts[1] if len(parts) > 1 else "", "line": ln})
    return keys


def pin_host_key(home, line):
    """Add a host key line to OpenMove's known_hosts (after a person confirmed the fingerprint)."""
    kh = known_hosts(home)
    have = kh.read_text(encoding="utf-8").splitlines() if kh.exists() else []
    if line not in have:
        kh.write_text("\n".join(have + [line]) + "\n", encoding="utf-8")


def is_pinned(home, address, port):
    kh = known_hosts(home)
    if not kh.exists():
        return False
    tag = address if int(port) == 22 else "[%s]:%d" % (address, int(port))
    return any(ln.split(" ", 1)[0].split(",")[0] == tag for ln in kh.read_text(encoding="utf-8").splitlines() if ln.strip())


# ---- a host ---------------------------------------------------------------------------------------------------------------------------
class Host:
    """One machine OpenMove runs commands on. spec comes from the host registry (hosts.py)."""

    def __init__(self, spec, home):
        self.spec, self.home = dict(spec), Path(home)
        self.name = spec.get("name", "")
        self.local = spec.get("transport") == "local"
        self._mover_path = None

    # ---- the transport -----------------------------------------------------------------------------------------------
    def ssh_argv(self):
        s = self.spec
        argv = [tool("ssh"), "-p", str(int(s.get("port") or 22)), "-i", str(key_path(self.home)),
                "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
                "-o", "UserKnownHostsFile=%s" % known_hosts(self.home), "-o", "ConnectTimeout=%d" % CONNECT_TIMEOUT,
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "-o", "LogLevel=ERROR", "-o", "PasswordAuthentication=no",
                "%s@%s" % (s.get("user") or "root", s["address"])]
        if s.get("jump"):
            argv[1:1] = ["-J", s["jump"]]
        return argv

    def exec(self, argv, stdin=None, timeout=60, env=None):
        """Run a command on the host. argv is a list; returns (rc, stdout, stderr) as text. Raises SshError when the connection fails."""
        if self.local:
            full = dict(os.environ, **(env or {}))
            rc, out, err = run_local(list(argv), stdin, timeout, full)
        else:
            parts = list(argv)
            if env:
                parts = ["env"] + ["%s=%s" % (k, v) for k, v in env.items()] + parts
            remote = " ".join(shlex.quote(a) for a in parts)
            rc, out, err = run_local(self.ssh_argv() + [remote], stdin, timeout)
            if rc == 255:                                        # ssh itself failed
                msg = err.decode("utf-8", "replace").strip()
                low = msg.lower()
                cut = ("closed by remote" in low or "broken pipe" in low or "connection reset" in low) and "connect to host" not in low
                raise SshError("ssh to %s failed: %s" % (self.name or self.spec.get("address"), msg[:300]), outcome_unknown=cut)
        return rc, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    def sudo(self, argv):
        return (["sudo", "-n"] + list(argv)) if self.spec.get("sudo") else list(argv)

    # ---- the mover -----------------------------------------------------------------------------------------------------
    def mover_home(self):
        if self.spec.get("mover_dir"):
            return self.spec["mover_dir"]
        if self.local:
            return str(self.home / "mover")
        rc, out, err = self.exec(["sh", "-c", 'printf %s "$HOME"'], timeout=30)
        if rc != 0 or not out.strip():
            raise SshError("could not find the home folder on %s" % self.name)
        return out.strip() + "/.openmove-mover"

    def install_mover(self):
        """Send the data mover to the host (its own folder) and check that the file there has the hash of the file here."""
        data = resource("mover.py")
        full, home = hashlib.sha256(data).hexdigest(), self.mover_home()
        path = "%s/mover-%s.py" % (home, full[:12])
        if self.local:
            Path(home).mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(data)
        else:
            q = shlex.quote
            script = "mkdir -p %s && cat > %s.tmp && mv %s.tmp %s && chmod 700 %s" % (q(home), q(path), q(path), q(path), q(path))
            rc, out, err = self.exec(["sh", "-c", script], stdin=data, timeout=60)
            if rc != 0:
                raise SshError("could not place the data mover on %s: %s" % (self.name, err.strip()[:300]))
            rc, out, err = self.exec(["sha256sum", path], timeout=30)
            if rc != 0 or not out.startswith(full):
                raise SshError("the data mover file on %s does not have the expected hash" % self.name)
        self._mover_path = path
        return {"path": path, "sha256": full}

    def mover(self, verb, args=None, timeout=120):
        """Run one data-mover verb on the host. Returns the answer's dict; raises SshError when the verb refused or the host did not answer."""
        if not self._mover_path:
            self.install_mover()
        py = self.spec.get("mover_python") or "python3"
        home = self.mover_home()
        if self.spec.get("sudo") and not self.local:
            cmd, env = ["sudo", "-n", "env", "OPENMOVE_MOVER_HOME=" + home, py, self._mover_path, verb], None
        else:
            cmd, env = [py, self._mover_path, verb], {"OPENMOVE_MOVER_HOME": home}
        rc, out, err = self.exec(cmd, stdin=json.dumps(args or {}).encode(), timeout=timeout, env=env)
        try:
            res = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            trace({"kind": "mover", "host": self.name, "verb": verb, "args": scrub_args(args), "rc": rc, "error": "no answer", "stdout": out[:TRACE_LIMIT], "stderr": err[:TRACE_LIMIT]})
            raise SshError("the data mover on %s gave no answer (rc %s): %s" % (self.name, rc, (err or out).strip()[:2000]), rc=rc) from None
        trace({"kind": "mover", "host": self.name, "verb": verb, "args": scrub_args(args), "rc": rc, "answer": res})
        if not res.get("ok"):
            raise SshError("the data mover on %s refused %s: %s" % (self.name, verb, res.get("error")), rc=rc)
        return res

    def remove_mover(self):
        """Take the data mover and its folder off the host (nothing is left behind)."""
        res = self.mover("cleanup", {"all": True})
        self._mover_path = None
        return res

    # ---- the Cinder helper ------------------------------------------------------------------------------------------------
    def cinder(self, verb, args=None, timeout=120):
        """Run one Cinder-helper verb through the interpreter that Cinder uses. The helper is sent on standard input each time."""
        py = self.spec.get("python") or "python3"
        payload = base64.b64encode(json.dumps(args or {}).encode()).decode()
        cmd = self.sudo([py, "-", verb, payload])
        if self.spec.get("cinder_user"):
            cmd = ["sudo", "-n", "-u", self.spec["cinder_user"], py, "-", verb, payload]
        rc, out, err = self.exec(cmd, stdin=resource("cinder_helper.py"), timeout=timeout)
        try:
            res = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            trace({"kind": "cinder", "host": self.name, "verb": verb, "args": scrub_args(args), "rc": rc, "error": "no answer", "stdout": out[:TRACE_LIMIT], "stderr": err[-TRACE_LIMIT:]})
            raise SshError("the Cinder helper on %s gave no answer (rc %s): %s" % (self.name, rc, (err or out).strip()[-2000:]), rc=rc) from None
        trace({"kind": "cinder", "host": self.name, "verb": verb, "args": scrub_args(args), "rc": rc, "answer": res})
        if not res.get("ok"):
            raise SshError("the Cinder helper on %s refused %s: %s" % (self.name, verb, res.get("error")), rc=rc)
        return res


# ---- the one-time login with a password (setup of a new host) -------------------------------------------------------------------------
PASSWORD_ENV = "OPENMOVE_BOOT_PW"


def askpass_helper(folder):
    """A tiny program that prints the password from the environment, for ssh to call (SSH_ASKPASS). The password is never in a file or an argument."""
    folder = Path(folder)
    if os.name == "nt":
        p = folder / "askpass.cmd"
        if getattr(sys, "frozen", False):
            body = '@"%s" --askpass\r\n' % sys.executable
        else:
            body = '@"%s" -I -c "import os,sys;sys.stdout.write(os.environ[\'%s\']+\'\\n\')"\r\n' % (sys.executable, PASSWORD_ENV)
        p.write_bytes(body.encode("utf-8"))
    else:
        p = folder / "askpass.sh"
        p.write_text("#!/bin/sh\nprintf '%%s\\n' \"$%s\"\n" % PASSWORD_ENV, encoding="utf-8")
        p.chmod(0o700)
    return p


class RootLogin:
    """A login with a password, for setting up a host that OpenMove has no key on yet. Same host-key rule as everything else: the key must be pinned.

    The password lives in this object and, while a command runs, in the environment of the ssh process and its askpass helper. It is not in an argument list,
    not in the trace, not in a file, not in the journal. After the setup the host is reached with OpenMove's key and this object is dropped.
    """

    def __init__(self, home, address, port=22, user="root", password="", jump=""):
        self.home, self.address, self.port, self.user, self.jump = Path(home), address, int(port or 22), user or "root", jump
        self._password = password
        self.name = "%s@%s" % (self.user, address)

    def argv(self, config):
        a = [tool("ssh"), "-p", str(self.port), "-o", "BatchMode=no", "-o", "PubkeyAuthentication=no", "-o", "IdentitiesOnly=yes",
             "-o", "PreferredAuthentications=password,keyboard-interactive", "-o", "NumberOfPasswordPrompts=1",
             "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=%s" % known_hosts(self.home), "-o", "ConnectTimeout=%d" % CONNECT_TIMEOUT,
             "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "-o", "LogLevel=ERROR", "-F", str(config), "%s@%s" % (self.user, self.address)]
        if self.jump:
            a[1:1] = ["-J", self.jump]
        return a

    def exec(self, argv, stdin=None, timeout=60):
        """Run one command; returns (rc, stdout, stderr) as text. Raises SshError when the login or the connection fails."""
        import tempfile
        parts = list(argv)
        remote = " ".join(shlex.quote(x) for x in parts)
        with tempfile.TemporaryDirectory(prefix="openmove-askpass-") as d:
            helper = askpass_helper(d)
            config = Path(d) / "empty_config"                        # the user's own ssh config is not read: the login must behave the same everywhere
            config.write_text("", encoding="utf-8")
            env = dict(os.environ, SSH_ASKPASS=str(helper), SSH_ASKPASS_REQUIRE="force", DISPLAY=os.environ.get("DISPLAY") or "openmove:0")
            env[PASSWORD_ENV] = self._password
            rc, out, err = run_local(self.argv(config) + [remote], stdin if stdin is not None else b"", timeout, env=env, detach=True)
        if rc == 255:
            msg = err.decode("utf-8", "replace").strip()
            low = msg.lower()
            if "permission denied" in low:
                raise SshError("%s did not accept the password for %s (ssh said: %s). The account may be locked, or sshd may not allow password login for it "
                               "(PermitRootLogin or PasswordAuthentication in sshd_config): use an account that may, or the setup script by hand"
                               % (self.address, self.user, msg[:300]))
            cut = ("closed by remote" in low or "broken pipe" in low or "connection reset" in low) and "connect to host" not in low
            raise SshError("ssh to %s failed: %s" % (self.name, msg[:300]), outcome_unknown=cut)
        return rc, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def describe(host_spec):
    """Safe to show: never the key path contents."""
    keep = ("name", "role", "transport", "address", "port", "user", "sudo", "python", "cinder_user", "mover_dir", "jump", "notes", "arrays", "storage")
    return {k: host_spec[k] for k in keep if k in host_spec}


def platform_notes():
    return {"python": sys.version.split()[0], "ssh": shutil.which("ssh"), "ssh-keygen": shutil.which("ssh-keygen"), "ssh-keyscan": shutil.which("ssh-keyscan")}
