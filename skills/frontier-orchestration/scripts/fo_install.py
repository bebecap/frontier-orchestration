"""Interactive installer: `fo.py install`.

Steps: decision engine (TypeSafe Jev with a key, OpenJev on this machine, OpenJev on Codiv, or
none) -> providers (Claude, Codex, Antigravity; one or more, at least one signed in) ->
telemetry consent -> ledger folder -> write the config and check it.

Every step can be scripted: without a terminal the prompts read numbered answers from stdin.
Keys are never written to the config, never echoed and never passed on a command line.
"""

from __future__ import annotations

import http.client
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import fo_config
import fo_jev
import fo_lessons
import fo_providers
import fo_telemetry
import fo_tui as ui

OPENJEV_REPO = "https://github.com/razorback16/openjev"
# The upstream commit this installer was tested with (OpenJev 0.7.2). OpenJev has no release
# tags, so an unpinned clone could pick up a change nobody tested. FO_OPENJEV_REF overrides it.
OPENJEV_COMMIT = "b4339c640f5331ef75098637d1be2c787715ee88"
# Where the launcher and the Docker command below serve OpenJev. FO_OPENJEV_URL changes where the
# installer looks for, waits for and saves a local OpenJev, not where they serve (see openjev_url).
OPENJEV_URL = "http://127.0.0.1:8080"

# OpenJev's model is DiffusionGemma 26B-A4B. On Apple silicon it runs through MLX (mlx-vlm), which
# loads the whole model: a build must keep the vision tower (text-only builds fail with "Missing
# ... parameters") and use a quantization MLX knows (affine, mxfp4, nvfp4, mxfp8). Each build is
# pinned to an exact Hugging Face revision: a later upload to the same repo cannot change it.
OPENJEV_BUILDS = {
    "compact": {"label": "Compact (MXFP4, about 14 GB)", "repo": "mlx-community/diffusiongemma-26B-A4B-it-mxfp4",
                "revision": "c8982e704b68d7529678564e99ebbc08112d4e11", "ram_gb": 24, "disk_gb": 20, "cache_gb": 1,
                "hint": "smallest build that loads; peak memory about 19 GB; as good as 4-bit in fo.py eval"},
    "standard": {"label": "Standard (4-bit, about 16 GB)", "repo": "mlx-community/diffusiongemma-26B-A4B-it-4bit",
                 "revision": "a7a81407613811e8ba63af92ac0d852b809e191f", "ram_gb": 24, "disk_gb": 30, "cache_gb": 1,
                 "hint": "the build OpenJev is tested with"},
}
DEFAULT_BUILD = "compact"
# Model names a server may report. Anything else from /v1/models is server text: never stored or shown.
KNOWN_SERVED = ("openjev-latest", "openjev-0.1", "laya-1.0", "verdict-1.4", "clm-v0.1", "jevk5-0.3",
                "clef-1.0", "clef-flash-1.0")
# A Hugging Face repo id, and a revision (commit hash, branch or tag).
HF_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}")
HF_REV = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,99}")


def openjev_home() -> str:
    return os.path.join(fo_config.data_home(), "openjev")


def openjev_installed(home: str | None = None) -> bool:
    """True only after a complete install (the marker is written last)."""
    return os.path.exists(os.path.join(home or openjev_home(), ".installed"))


def openjev_url() -> str:
    """The address where the installer looks for OpenJev on this machine.

    FO_OPENJEV_URL (http:// or https://) replaces the default http://127.0.0.1:8080 for the
    check, the wait and the saved jev.base_url: for an OpenJev that you run on another address.
    The launcher and the Docker command still serve on 127.0.0.1:8080. The test suite sets it to
    a closed port, so an OpenJev that really runs on 8080 cannot change a test's prompts."""
    env = os.environ.get("FO_OPENJEV_URL", "").strip().rstrip("/")
    return env if env and not openjev_url_problem(env) else OPENJEV_URL


def openjev_url_problem(url: str):
    """None for a URL the saved config may use without jev.allow_insecure_http; else the reason.

    Never raises: a malformed value (bad brackets, a port that is not a number) is a problem too."""
    try:
        fo_jev.check_transport({"base_url": url})
    except (fo_jev.JevError, ValueError):
        return ("FO_OPENJEV_URL must be https://, or http:// to 127.0.0.1, localhost or [::1], "
                "with a valid port and no user name")
    return None


def _address_problem(value: str):
    """None for an http(s) address that parses; plain http to another machine is asked about next."""
    value = value.strip()
    if not value.lower().startswith(("http://", "https://")):
        return "Start with http:// or https://"
    try:
        fo_jev.check_transport({"base_url": value, "allow_insecure_http": True})
    except fo_jev.JevError as e:
        return str(e)
    return None


def openjev_settings(url: str, model: str) -> dict:
    """The jev config section. The default model (openjev-latest) is not written."""
    out = {"backend": "openjev", "base_url": url}
    if model and model != "openjev-latest":
        out["model"] = model
    return out


# ---------- small helpers ----------

def _run(cmd: list, timeout: int = 1800, cwd: str | None = None, env: dict | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                           env=env or dict(os.environ), stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout + p.stderr)[-2000:]
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except OSError as e:
        return 127, str(e)


def keychain_has(service: str) -> bool:
    if sys.platform != "darwin" or not os.path.exists("/usr/bin/security"):
        return False
    return _run(["/usr/bin/security", "find-generic-password", "-s", service], timeout=15)[0] == 0


def openjev_models(url: str | None = None) -> list:
    """The model names an OpenJev server at `url` serves ([] if none answers).

    Only names from KNOWN_SERVED are returned; a server that answers with others gets ["jev-latest"],
    which every OpenJev server accepts."""
    url = (url or openjev_url()).rstrip("/") + "/v1/models"
    try:
        # fo_jev's opener: no redirect is followed (a remote server could send the probe to another
        # host), and plain http or loopback never goes through a proxy.
        with fo_jev.opener(url).open(urllib.request.Request(url, headers={"User-Agent": fo_jev.USER_AGENT}),
                                     timeout=3) as r:
            if r.status != 200:
                return []
            data = json.loads(r.read(65536) or b"{}")
    except urllib.error.HTTPError as e:  # also a refused redirect
        e.close()
        return []
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
        return []
    names = [m.get("name") for m in (data.get("models") or []) if isinstance(m, dict)] if isinstance(data, dict) else []
    known = [n for n in names if n in KNOWN_SERVED]
    return known or ["jev-latest"]


def openjev_running(url: str | None = None) -> bool:
    return bool(openjev_models(url))


def served_model(names: list) -> str:
    """The model id to configure for a running server."""
    return "openjev-latest" if "openjev-latest" in names else names[0]


def machine() -> dict:
    ram = 0
    if sys.platform == "darwin":
        code, out = _run(["/usr/sbin/sysctl", "-n", "hw.memsize"], timeout=10)
        ram = int(out.strip()) // 2**30 if code == 0 and out.strip().isdigit() else 0
    elif os.path.exists("/proc/meminfo"):
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    ram = int(line.split()[1]) // 2**20
    free = shutil.disk_usage(os.path.expanduser("~")).free // 2**30
    apple = sys.platform == "darwin" and platform.machine() == "arm64"
    return {"ram_gb": ram, "disk_free_gb": free, "apple_silicon": apple,
            "docker": bool(shutil.which("docker")), "git": bool(shutil.which("git"))}


def _is_py310(cand: str) -> bool:
    code, out = _run([cand, "-c", "import sys;print(sys.version_info[:2]>=(3,10))"], timeout=20)
    return code == 0 and out.strip() == "True"


def uv_path() -> str:
    """The uv tool, if this machine has it (it can install a Python without sudo)."""
    for cand in (shutil.which("uv"), os.path.expanduser("~/.local/bin/uv"), os.path.expanduser("~/.cargo/bin/uv"),
                 "/opt/homebrew/bin/uv"):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return ""


def python310() -> str:
    """A Python 3.10+ for OpenJev's own environment ('' if none).

    A fresh Mac has only /usr/bin/python3 (3.9), so look where people usually put a newer one:
    PATH, ~/.local/bin (uv, pipx), Homebrew, python.org, and the Pythons uv manages."""
    names = ("python3.13", "python3.12", "python3.11", "python3.10")
    cands = [sys.executable] + [shutil.which(n) for n in names]
    for d in (os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin", "/usr/local/bin"):
        cands += [os.path.join(d, n) for n in names] + [os.path.join(d, "python3")]
    cands += [f"/Library/Frameworks/Python.framework/Versions/{v}/bin/python3" for v in ("3.13", "3.12", "3.11", "3.10")]
    seen = set()
    for cand in cands:
        if cand and cand not in seen and os.path.isfile(cand) and os.access(cand, os.X_OK):
            seen.add(cand)
            if _is_py310(cand):
                return cand
    uv = uv_path()
    if uv:
        code, out = _run([uv, "python", "find", "--no-project", ">=3.10"], timeout=30)
        found = out.strip().splitlines()[-1] if code == 0 and out.strip() else ""
        if found and os.path.isfile(found) and _is_py310(found):
            return found
    return ""


def python310_hint(platform_name: str | None = None) -> str:
    """How to get Python 3.10+ here: uv first everywhere (user level, no sudo), then the usual
    package manager of this platform (Homebrew on macOS, the distribution's own on Linux)."""
    uv = "    curl -LsSf https://astral.sh/uv/install.sh | sh && uv python install 3.12   (uv, no sudo)"
    if (platform_name or sys.platform) == "darwin":
        return uv + "\n    or: brew install python@3.12   (Homebrew)"
    return (uv + "\n    or: sudo apt install python3.12 python3.12-venv   (Debian, Ubuntu)"
            "\n    or: sudo dnf install python3.12   (Fedora, RHEL)")


def get_python310() -> str:
    """python310(), or offer to install one with uv (user level, no sudo). '' if there is none."""
    py = python310()
    if py:
        return py
    uv = uv_path()
    if not uv:
        ui.error("OpenJev needs Python 3.10 or newer, and this machine has none. Install one, then run again:\n"
                 + python310_hint())
        return ""
    ui.note("OpenJev needs Python 3.10 or newer", [f"None found. uv can add one for you only (no sudo):",
                                                    f"{uv} python install 3.12"])
    if not ui.confirm("Install Python 3.12 with uv now?", default=True):
        return ""
    with ui.spinner("Installing Python 3.12 with uv") as s:
        code, out = _run([uv, "python", "install", "3.12"], timeout=900)
        if code != 0:
            s.fail("uv python install failed: " + (out.strip().splitlines() or [""])[-1][:120])
            return ""
        s.stop("Python 3.12 installed")
    return python310()


# ---------- decision engine ----------

def choose_engine(answers: dict) -> dict:
    """Returns the jev config section."""
    found = "running at " + openjev_url() if openjev_running() else (
        "installed, not running" if openjev_installed() else "not installed")
    engine = ui.select("Which decision engine should route your tasks?", [
        ("typesafe", "TypeSafe Jev", "hosted, needs an API key"),
        ("openjev", "OpenJev on this machine", f"open weights, private, about 14 GB; {found}"),
        ("codiv", "OpenJev hosted on Codiv", "needs a Codiv API key"),
        ("off", "No decision engine", "local rules, no network, no key"),
    ])
    answers["engine"] = engine
    if engine == "typesafe":
        return {"backend": "typesafe", **choose_key("TYPESAFE_API_KEY", "TypeSafe", "https://console.typesafe.ai")}
    if engine == "codiv":
        return {"backend": "codiv", **choose_key("CODIV_API_KEY", "Codiv", "https://codiv.ai")}
    if engine == "openjev":
        return setup_openjev()
    ui.info("Routing will use local rules: no calibrated confidence. You can add an engine later.")
    return {"backend": "off"}


def choose_key(env_name: str, label: str, signup: str) -> dict:
    """Pick an existing key (environment or Keychain) or store a new one. Returns jev settings."""
    options = []
    if os.environ.get(env_name, "").strip():
        options.append(("env", f"Use ${env_name} from this shell", "only while it is set"))
    if keychain_has(env_name):
        options.append(("keychain", f"Use the Keychain item {env_name}", "macOS login Keychain"))
    options.append(("new", "Add a new key", f"get one at {signup}"))
    pick = ui.select(f"Which {label} key should be used?", options)
    settings = {"api_key_env": env_name, "keychain_service": env_name, "api_key_file": None,
                "key_source": "env" if pick == "env" else "keychain"}
    if pick == "new":
        settings.update(store_new_key(env_name, label))
    backend = "typesafe" if env_name == "TYPESAFE_API_KEY" else "codiv"
    # Built from the defaults only: nothing from an earlier engine (an old base_url) may leak in,
    # or the key would be sent to the previous server.
    js = fo_config.jev_settings(fo_config.deep_merge(fo_config.DEFAULTS, {"jev": {"backend": backend, **settings}}))
    with ui.spinner(f"Checking the {label} key with one tiny call") as s:
        try:
            r = fo_jev.ping(js)
            s.stop(f"{label} answered: model {r['model']}")
        except fo_jev.JevError as e:
            s.fail(f"The check failed: {e}")
            if not ui.confirm("Keep this key anyway?", default=False):
                raise ui.Cancelled()
    return settings


def store_new_key(env_name: str, label: str) -> dict:
    """macOS: the `security` tool asks for the key itself, so it never passes through this program.
    Elsewhere: a file with mode 0600."""
    if sys.platform == "darwin" and os.path.exists("/usr/bin/security"):
        ui.info(f"macOS will ask for the {label} key twice. It goes straight into your login Keychain.")
        code = subprocess.call(["/usr/bin/security", "add-generic-password", "-U", "-a",
                                os.environ.get("USER", "user"), "-s", env_name, "-w"])
        if code != 0 or not keychain_has(env_name):
            ui.error("The key was not stored in the Keychain.")
            raise ui.Cancelled()
        return {"key_source": "keychain"}
    key = ui.password(f"Paste the {label} key (hidden)")
    if not key:
        raise ui.Cancelled()
    path = os.path.join(fo_config.config_dir(), f"{env_name.lower()}.key")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".key-")  # 0600 from the start
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    os.replace(tmp, path)  # never written into an older, wider-mode file
    return {"api_key_file": path, "key_source": "file"}


def setup_openjev() -> dict:
    url = openjev_url()
    served = openjev_models(url)
    if served:
        model = served_model(served)
        ui.info(f"OpenJev answers at {url} with {model}.")
        return openjev_settings(url, model)
    m = machine()
    home = openjev_home()
    ui.note("This machine", [f"Memory: {m['ram_gb']} GB, free disk: {m['disk_free_gb']} GB",
                             "Apple silicon: MLX build" if m["apple_silicon"] else
                             ("NVIDIA: Docker image" if m["docker"] else
                              "No supported runtime found (needs Apple silicon or Docker + NVIDIA GPU)")])
    choice = ui.select("OpenJev is not running here. What now?", [
        ("here", "Install or start OpenJev here", "you pick the build next"),
        ("url", "Use an OpenJev server at another address", "for example a GPU box on your network"),
        ("back", "Choose a different engine", ""),
    ])
    if choice == "back":
        raise _Back()
    if choice == "url":
        url = ui.text("OpenJev address", default="http://127.0.0.1:8080", validate=_address_problem).strip()
        settings = {"backend": "openjev", "base_url": url.rstrip("/")}
        if openjev_url_problem(url):  # parses, but plain http to another machine
            if ui.confirm("This is plain http to another machine. Send requests unencrypted?", default=False):
                settings["allow_insecure_http"] = True
            else:
                raise _Back()
        return settings
    if url != OPENJEV_URL:
        ui.warn(f"FO_OPENJEV_URL is {url}, but the OpenJev that this installer starts serves on {OPENJEV_URL}.")
    if not m["apple_silicon"]:
        return install_openjev_docker(m)
    repo, rev, ram, disk, cache_gb = choose_openjev_build()
    if m["ram_gb"] and m["ram_gb"] < ram:
        ui.warn(f"{m['ram_gb']} GB of memory is less than this build needs (about {ram} GB); it may be slow or fail to load.")
    if m["disk_free_gb"] < disk:
        ui.warn(f"Only {m['disk_free_gb']} GB free; this build needs about {disk} GB.")
    if not openjev_installed(home) and not install_openjev_mlx(home):
        raise _Back()
    weights = download_weights(home, repo, rev)
    if not weights:
        raise _Back()
    write_launcher(home, weights, cache_gb)
    if not start_openjev(home):
        if ui.select("OpenJev is not answering yet.", [
                ("keep", "Use OpenJev anyway", f"it may still be loading; {fo_config.cli()} doctor --live checks later"),
                ("back", "Choose a different engine", "")]) == "back":
            raise _Back()
    return openjev_settings(openjev_url(), "openjev-latest")


def choose_openjev_build() -> tuple:
    """(repo, revision, ram_gb, disk_gb, cache_gb) of the MLX weights. Raises _Back to choose another engine."""
    opts = [(k, b["label"], b["hint"]) for k, b in OPENJEV_BUILDS.items()]
    opts.sort(key=lambda o: o[0] != DEFAULT_BUILD)  # the default build first
    opts += [("custom", "Another DiffusionGemma MLX build", "a Hugging Face id, as owner/name or owner/name@revision"),
             ("back", "Choose a different engine", "")]
    key = ui.select("Which OpenJev build?", opts)
    if key == "back":
        raise _Back()
    if key != "custom":
        b = OPENJEV_BUILDS[key]
        return b["repo"], b["revision"], b["ram_gb"], b["disk_gb"], b["cache_gb"]

    def problem(v):
        repo, at, rev = v.partition("@")
        if not HF_ID.fullmatch(repo):
            return "Use the form owner/name or owner/name@revision"
        if at and not HF_REV.fullmatch(rev):
            return "The revision must be a commit hash, branch or tag"
        return None
    repo, _, rev = ui.text("Hugging Face id of the MLX weights", validate=problem).partition("@")
    ui.warn("Only full DiffusionGemma MLX builds load: the vision tower must be kept, and the quantization must be "
            "affine, mxfp4, nvfp4 or mxfp8 (text-only and TurboQuant builds fail). The download is pinned to the "
            f"revision it finds now. Check it with {fo_config.cli()} eval after the install.")
    return repo, rev or "main", 24, 40, 4


class _Back(Exception):
    pass


def install_openjev_mlx(home: str) -> bool:
    """Clone OpenJev at the tested commit, make its venv, and install it with MLX."""
    py = get_python310()
    if not py:
        return False
    ref = os.environ.get("FO_OPENJEV_REF", "").strip() or OPENJEV_COMMIT
    if ref.startswith("-") or not re.fullmatch(r"[0-9A-Za-z._/-]{1,200}", ref):
        ui.error("FO_OPENJEV_REF must be a commit, tag or branch name.")
        return False
    steps = [f"git clone {OPENJEV_REPO} {home}/repo  (commit {ref[:12]})",
             f"{py} -m venv {home}/venv",
             f"{home}/venv/bin/pip install -e '{home}/repo[mlx]'"]
    ui.note("Install steps", steps)
    if not shutil.which("git"):
        ui.error("git is not installed.")
        return False
    if not ui.confirm("Run these steps now?", default=True):
        return False
    os.makedirs(home, exist_ok=True)
    log = os.path.join(home, "install.log")
    repo = os.path.join(home, "repo")
    with ui.spinner("Getting OpenJev") as s:
        reused = os.path.exists(os.path.join(repo, ".git"))  # a clone or a linked worktree (.git file)
        if reused:
            code, out = 0, "reusing the earlier checkout"  # an interrupted install: keep it, redo the rest
            _, dirty = _run(["git", "-C", repo, "status", "--porcelain", "--untracked-files=no"], timeout=20)
            if dirty.strip():
                s.fail(f"{repo} has local changes; it was left untouched. Move it away and run again.")
                return False
        elif os.path.exists(repo):
            s.fail(f"{repo} exists but is not a git checkout; it was left untouched. Move it away and run again.")
            return False
        else:
            code, out = _run(["git", "clone", "--filter=blob:none", "--no-checkout", OPENJEV_REPO, repo], timeout=600)
        _append(log, out)
        if code != 0:
            s.fail(f"git clone failed (see {log})")
            return False
        # Always check out the tested commit: a --no-checkout clone has no files yet, and a reused
        # checkout may sit at another commit.
        code, out = _run(["git", "-C", repo, "checkout", "-q", "--detach", ref], timeout=300)
        if code != 0:  # an older shallow clone may lack the commit: fetch it, then check out
            _run(["git", "-C", repo, "fetch", "-q", "--filter=blob:none", "origin", ref], timeout=600)
            code, out = _run(["git", "-C", repo, "checkout", "-q", "--detach", ref], timeout=300)
        _append(log, out)
        if code != 0:
            s.fail(f"could not check out OpenJev {ref[:12]} (see {log})")
            return False
        _, sha = _run(["git", "-C", repo, "rev-parse", "HEAD"], timeout=20)
        s.stop(f"Cloned OpenJev at {sha.strip()[:12]}")
    with ui.spinner("Creating its Python environment") as s:
        code, out = _run([py, "-m", "venv", os.path.join(home, "venv")], timeout=300)
        _append(log, out)
        if code != 0:
            s.fail(f"venv failed (see {log})")
            return False
        s.stop("Python environment ready")
    with ui.spinner("Installing OpenJev and MLX (a few minutes)") as s:
        code, out = _run([os.path.join(home, "venv", "bin", "pip"), "install", "-e",
                          os.path.join(home, "repo") + "[mlx]"], timeout=3600)
        _append(log, out)
        if code != 0:
            s.fail(f"pip install failed (see {log})")
            return False
        s.stop("OpenJev installed")
    _, sha = _run(["git", "-C", repo, "rev-parse", "HEAD"], timeout=20)
    with open(os.path.join(home, ".installed"), "w") as f:  # written last: marks a complete install
        f.write(sha.strip() + "\n")
    return True


DOWNLOAD = ("import sys\nfrom huggingface_hub import snapshot_download\n"
            "print(snapshot_download(sys.argv[1], revision=sys.argv[2]))\n")


def download_weights(home: str, repo: str, revision: str) -> str:
    """Download one pinned MLX build into the Hugging Face cache; return its local folder ('' on failure).

    The folder name holds the commit hash, so the launcher always loads exactly these files."""
    if not HF_ID.fullmatch(repo) or not HF_REV.fullmatch(revision):
        ui.error("That is not a valid Hugging Face id or revision.")
        return ""
    py = os.path.join(home, "venv", "bin", "python")
    os.makedirs(home, exist_ok=True)
    log = os.path.join(home, "install.log")
    with ui.spinner(f"Downloading {repo} (this can take a while)") as s:
        # stdout carries only the snapshot folder; progress and warnings go to stderr (logged).
        try:
            p = subprocess.run([py, "-I", "-c", DOWNLOAD, repo, revision], capture_output=True, text=True,
                               timeout=4 * 3600, stdin=subprocess.DEVNULL)
            code, stdout, stderr = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            code, stdout, stderr = 124, "", "timed out"
        except OSError as e:
            code, stdout, stderr = 127, "", str(e)
        _append(log, (stderr or "")[-2000:])
        lines = stdout.strip().splitlines()
        path = lines[-1].strip() if code == 0 and lines else ""
        if not path or not os.path.isdir(path) or not os.path.exists(os.path.join(path, "config.json")):
            s.fail(f"download failed (see {log})")
            return ""
        s.stop(f"Weights ready: {ui.short_path(path)}")
    return path


def write_launcher(home: str, weights: str = "", cache_gb: int = 4) -> str:
    """start-openjev.sh. Every value in it is quoted: no folder name can run as a command.

    cache_gb caps MLX's buffer pool. 1 GB was measured for the pinned 4-bit class builds: peak
    memory 19 GB instead of 22, same answers and speed. Other builds keep 4 GB (OpenJev's own
    advice: an 8-bit or bf16 read can need more)."""
    path = os.path.join(home, "start-openjev.sh")
    python = shlex.quote(os.path.join(home, "venv", "bin", "python"))  # safe for any folder name
    lines = ["#!/bin/sh", "# Starts OpenJev (MLX) on 127.0.0.1:8080. Made by frontier-orchestration.",
             "export OPENJEV_BACKEND=mlx", f"export OPENJEV_MLX_CACHE_LIMIT_GB=${{OPENJEV_MLX_CACHE_LIMIT_GB:-{int(cache_gb)}}}"]
    if weights:
        lines.append(f"export OPENJEV_MLX_MODEL={shlex.quote(weights)}")
    lines.append(f'exec {python} -m openjev "$@"')
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(path, 0o700)
    return path


def start_openjev(home: str) -> bool:
    launcher = os.path.join(home, "start-openjev.sh")
    if not os.path.exists(launcher):
        launcher = write_launcher(home)
    ui.note("Start OpenJev", [launcher, "The first start loads the weights into memory (a minute or two)."])
    if not ui.confirm("Start it now in the background?", default=True):
        ui.info(f"Start it later with: {launcher}")
        return False
    log = os.path.join(home, "server.log")
    with open(log, "a") as out:
        subprocess.Popen([launcher], stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                         start_new_session=True, close_fds=True)
    return wait_for_openjev("Waiting for OpenJev to answer", log)


def install_openjev_docker(m: dict) -> dict:
    cmd = ["docker", "run", "-d", "--gpus", "all", "--ipc=host", "-p", "127.0.0.1:8080:8080",
           "-v", os.path.expanduser("~/.cache/huggingface") + ":/root/.cache/huggingface",
           "razorback16/openjev:0.7.0"]
    ui.note("OpenJev with Docker (NVIDIA GPU, 24 GB or more)", [" ".join(cmd)])
    if not m["docker"]:
        ui.error("Docker is not installed. Install Docker with the NVIDIA runtime, or pick another engine.")
        raise _Back()
    if not ui.confirm("Run this now?", default=True):
        raise _Back()  # nothing runs: do not save an engine that is not there
    with ui.spinner("Starting the OpenJev container") as s:
        code, out = _run(cmd, timeout=1800)
        if code != 0:
            s.fail("docker run failed: " + out.strip().splitlines()[-1][:120] if out.strip() else "docker run failed")
            raise _Back()
        s.stop("Container started; the first start downloads the weights")
    if not wait_for_openjev("Waiting for OpenJev to answer"):
        if ui.select("OpenJev is not answering yet.", [
                ("keep", "Use OpenJev anyway", f"it may still be downloading; {fo_config.cli()} doctor --live checks later"),
                ("back", "Choose a different engine", "")]) == "back":
            raise _Back()
    return {"backend": "openjev", "base_url": openjev_url()}


def wait_for_openjev(message: str, log: str = "") -> bool:
    with ui.spinner(message + " (the first start downloads the weights)") as s:
        deadline = time.time() + int(os.environ.get("FO_OPENJEV_WAIT_S", "1800"))
        while time.time() < deadline:
            if openjev_running():
                s.stop(f"OpenJev answers at {openjev_url()}")
                return True
            time.sleep(5)
        s.fail("OpenJev did not answer yet. It may still be downloading" + (f"; see {log}" if log else "."))
    return False


def _append(path: str, text: str) -> None:
    with open(path, "a") as f:
        f.write(text + "\n")


# ---------- providers ----------

def choose_providers(answers: dict) -> dict:
    """Returns the providers config section. At least one chosen provider must be signed in."""
    while True:
        with ui.spinner("Looking for Claude, Codex and Antigravity on this machine") as s:
            st = {v: fo_providers.session(v) for v in fo_config.VENDORS}
            s.stop("Checked " + ", ".join(f"{fo_providers.LABELS[v]}: {st[v]['detail']}" for v in fo_config.VENDORS))
        options = [(v, fo_providers.LABELS[v], st[v]["detail"]) for v in fo_config.VENDORS]
        chosen = ui.multiselect("Which providers' models may this skill use?", options,
                                initial={v for v in fo_config.VENDORS if st[v]["signed_in"]})
        ready = [v for v in chosen if st[v]["signed_in"]]
        missing = [v for v in chosen if not st[v]["signed_in"]]
        if missing:
            ui.note("Not ready yet", [f"{fo_providers.LABELS[v]}: {st[v]['how_to_sign_in']}" for v in missing])
        if not ready:
            ui.warn("At least one chosen provider must be installed and signed in.")
            if ui.select("What now?", [("retry", "Check again", "after you sign in"),
                                        ("cancel", "Stop here", "")]) == "cancel":
                raise ui.Cancelled()
            continue
        if missing and ui.select("Some providers are not ready.", [
                ("go", f"Continue with {', '.join(fo_providers.LABELS[v] for v in ready)}", "add the others later"),
                ("retry", "Check again", "after you sign in")]) == "retry":
            continue
        if len(ready) == 1:
            ui.info("With one provider there is no independent, different-vendor review. Add a second one later.")
        answers["providers"] = ready
        return {v: {"enabled": v in ready} for v in fo_config.VENDORS}


def _ledger_problem(path: str):
    """None when the folder can be created and written; else the reason (checked BEFORE saving).

    A relative path is refused before anything is created: it would make a folder in whatever
    folder the installer runs from."""
    path = fo_config.clean_path(path)
    problem = fo_config.path_problem(path, "The ledger folder")
    if problem:
        return problem + ". Choose another folder."
    path = os.path.expanduser(path)
    try:
        os.makedirs(path, exist_ok=True)
        for sub in ("lessons", "runs"):  # the ledger's own folders: a file with these names would break it
            sp = os.path.join(path, sub)
            if os.path.exists(sp) and not os.path.isdir(sp):
                return f"{sp} exists and is not a folder. Choose another folder."
        fd, probe = tempfile.mkstemp(dir=path, prefix=".fo-write-test-")
        os.close(fd)
        os.remove(probe)
    except OSError as e:
        return f"Cannot write to {path}: {e.strerror or type(e).__name__}. Choose another folder."
    return None


# ---------- flow ----------

def run_installer() -> int:
    answers: dict = {}
    data = fo_config.read_file()
    try:
        ui.intro("frontier-orchestration")
        if os.environ.get("FO_JEV_BACKEND") or os.environ.get("FO_JEV_BASE_URL"):
            ui.warn("FO_JEV_BACKEND / FO_JEV_BASE_URL are set in this shell and override what you choose here.")
        raw = os.environ.get("FO_OPENJEV_URL", "").strip()
        if raw and openjev_url_problem(raw):
            ui.warn(f"{openjev_url_problem(raw)}. It is ignored: the installer looks for OpenJev at {OPENJEV_URL}.")
        if os.environ.get("FO_LEDGER") or os.environ.get("FO_LEDGER_LOG"):
            ui.warn("FO_LEDGER / FO_LEDGER_LOG are set in this shell and override the ledger folder you choose here.")
        while True:
            try:
                jev = choose_engine(answers)
                break
            except _Back:
                continue
        providers = choose_providers(answers)
        ui.note("Anonymous usage counts", [
            "Sends: command name, skill version, OS, outcome, counts.",
            "Never: prompts, code, file paths, model output, keys, any persistent id.",
            f"Details: references/privacy.md. Turn off any time: {fo_config.cli()} telemetry off"])
        telemetry = ui.confirm("Share anonymous usage counts?", default=False)
        default_ledger = fo_config.ledger_paths(fo_config.load())[0]
        # One normal form (no surrounding spaces) for the check, the saved value and the folder.
        ledger = os.path.expanduser(fo_config.clean_path(ui.text("Where should the learning ledger live?",
                                                                 default=ui.short_path(default_ledger),
                                                                 validate=_ledger_problem)))
    except (ui.Cancelled, KeyboardInterrupt):
        ui.cancel()
        return 130

    data["version"] = fo_config.SCHEMA_VERSION
    data["jev"] = jev  # replace: settings of an earlier engine (base_url, key source) must not carry over
    # The sign-in check looked at the auto transport (agy before gemini, cli for the others), so the
    # saved transport is auto too: an older forced `via` must not point at an unchecked CLI.
    # "command" (a removed override, ignored on load) is dropped from the file too.
    data["providers"] = {v: {**{k: x for k, x in ((data.get("providers") or {}).get(v) or {}).items()
                                if k not in ("via", "command")}, **p}
                         for v, p in providers.items()}
    data["telemetry"] = {"enabled": telemetry, "schema": fo_telemetry.SCHEMA_VERSION}
    if ledger and ledger != default_ledger:
        data.setdefault("ledger", {})["path"] = ledger
    fo_config.validate(fo_config.deep_merge(fo_config.DEFAULTS, data))  # the file itself must be valid
    # The config as it will load (file plus FO_* environment), so the ledger set up here is the
    # same one every later command uses. Set it up BEFORE the config is replaced: a failure here
    # leaves the old config intact.
    root, log = fo_config.ledger_paths(fo_config.effective(data))
    fo_lessons.configure(root, log)
    try:
        with fo_lessons.FolderLock():  # sets up and checks the ledger under its lock
            pass
    except (SystemExit, OSError) as e:
        ui.error(f"The ledger folder could not be set up: {e}")
        ui.cancel()
        return 1
    path = fo_config.write_file(data)
    cfg = fo_config.load()
    js = fo_config.jev_settings(cfg)
    ui.note("Saved", [f"Config: {ui.short_path(path)}",
                      f"Decision engine: {js['label']}",
                      f"Providers: {', '.join(fo_providers.LABELS[v] for v in answers['providers'])}",
                      f"Telemetry: {'on' if telemetry else 'off'}",
                      f"Ledger: {ui.short_path(root)}"])
    resolved = fo_providers.resolve(cfg)
    fo_telemetry.record("setup", {"ok": True, "mode": "interactive", "jev_backend": js["backend"],
                                  "host": fo_providers.detect_host(),
                                  **{f"via_{v}": (r["via"] or "none") if r["enabled"] else "none"
                                     for v, r in resolved.items()}}, cfg)
    ui.outro("Done. Next: " + ui.cyan(f"{fo_config.cli()} doctor --live"))
    return 0
