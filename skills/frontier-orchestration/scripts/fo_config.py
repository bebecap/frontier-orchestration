"""Configuration for frontier-orchestration: one host-neutral JSON file.

Resolution order for every value (first wins):
    1. command-line flags (handled by the caller)
    2. environment variables (FO_*, plus DO_NOT_TRACK and TYPESAFE_BASE_URL)
    3. the config file: $FO_CONFIG, else $XDG_CONFIG_HOME/frontier-orchestration/config.json,
       else ~/.config/frontier-orchestration/config.json
    4. the defaults below

The config file never holds an API key. Keys come from an environment variable, the macOS
Keychain, or a file with mode 0600 (see fo_jev.api_key).
"""

from __future__ import annotations

import copy
import json
import os
import shlex
import tempfile

APP = "frontier-orchestration"
VERSION = "0.1.1"
SCHEMA_VERSION = 1
HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
DATA_DIR = os.path.join(SKILL_DIR, "data")


def _xdg(var: str, fallback: str) -> str:
    base = os.environ.get(var, "").strip()
    if not base or not os.path.isabs(base):
        base = os.path.join(os.path.expanduser("~"), fallback)
    return os.path.join(base, APP)


def config_dir() -> str:
    return _xdg("XDG_CONFIG_HOME", ".config")


def state_dir() -> str:
    return _xdg("XDG_STATE_HOME", os.path.join(".local", "state"))


def data_home() -> str:
    return _xdg("XDG_DATA_HOME", os.path.join(".local", "share"))


def config_path() -> str:
    env = os.environ.get("FO_CONFIG", "").strip()
    return os.path.abspath(os.path.expanduser(env)) if env else os.path.join(config_dir(), "config.json")


# Jev backends. Every backend speaks the same wire API: POST {base_url}/v1/systemone.
JEV_BACKENDS = {
    "typesafe": {
        "label": "TypeSafe Jev (hosted, API key)",
        "base_url": "https://api.typesafe.ai",
        "model": "jev-latest",
        "api_key_env": "TYPESAFE_API_KEY",
        "key_required": True,
        "signup": "https://console.typesafe.ai",
    },
    "openjev": {
        "label": "OpenJev (self-hosted, open weights)",
        "base_url": "http://127.0.0.1:8080",
        "model": "openjev-latest",
        "api_key_env": "OPENJEV_API_KEY",
        "key_required": False,
        "signup": "https://github.com/razorback16/openjev",
    },
    "codiv": {
        "label": "OpenJev hosted on Codiv (API key)",
        "base_url": "https://api.codiv.ai",
        "model": "openjev-latest",
        "api_key_env": "CODIV_API_KEY",
        "key_required": True,
        "signup": "https://codiv.ai",
    },
    "off": {
        "label": "No Jev: a local heuristic router (no network, no key)",
        "base_url": "",
        "model": "",
        "api_key_env": "",
        "key_required": False,
        "signup": "",
    },
}

# Any other server that speaks the System One wire API (set jev.base_url).
CUSTOM_BACKEND = {
    "label": "Custom System One server",
    "base_url": "",
    "model": "jev-latest",
    "api_key_env": "FO_JEV_API_KEY",
    "key_required": False,
    "signup": "",
}

VENDORS = ("claude", "codex", "gemini")

# How a vendor is reached. "auto" picks the first one available on this machine, in this order.
TRANSPORTS = {
    "claude": ("host", "cli"),
    "codex": ("cli",),
    "gemini": ("agy", "gemini-cli"),
}

DEFAULTS = {
    "version": SCHEMA_VERSION,
    "providers": {
        "claude": {"enabled": True, "via": "auto", "model": None},
        "codex": {"enabled": True, "via": "auto", "model": None},
        "gemini": {"enabled": True, "via": "auto", "model": None},
    },
    "jev": {
        "backend": "typesafe",
        "base_url": None,
        "model": None,
        "api_key_env": None,
        "keychain_service": None,
        "api_key_file": None,
        "timeout_s": 180,
        "long_context": False,
        "allow_insecure_http": False,
        "key_source": None,
    },
    "ledger": {
        "path": None,
        "log": None,
    },
    "refresh": {
        "ttl_hours": 24,
        "upgrade_clis": False,
        "agy_keychain_fallback": False,
    },
    "telemetry": {
        "enabled": None,
        "schema": None,
    },
}


class ConfigError(Exception):
    pass


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def read_file(path: str | None = None) -> dict:
    path = path or config_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a JSON object")
    problem = shape_problem(data)
    if problem:
        raise ConfigError(f"{path}: {problem}; fix it in the file, or delete the file and run setup again")
    return migrate(data)


def shape_problem(data: dict):
    """None when every known section of a config file is an object (with object entries for each
    provider); else the reason. Values are checked later by validate()."""
    for section in DEFAULTS:
        if section != "version" and section in data and not isinstance(data[section], dict):
            return f"{section} must be a JSON object, not {type(data[section]).__name__}"
    for vendor, p in (data.get("providers") or {}).items():
        if not isinstance(p, dict):
            return f"providers.{vendor} must be a JSON object, not {type(p).__name__}"
    return None


def write_file(data: dict, path: str | None = None) -> str:
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".config-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def _truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


def _falsy(v: str) -> bool:
    return v.strip().lower() in ("0", "false", "no", "off")


def env_overrides() -> dict:
    env = os.environ
    over: dict = {}
    jev: dict = {}
    if env.get("FO_JEV_BACKEND"):
        jev["backend"] = env["FO_JEV_BACKEND"].strip().lower()
    if env.get("FO_JEV_BASE_URL"):
        jev["base_url"] = env["FO_JEV_BASE_URL"].strip()
    if env.get("FO_JEV_MODEL"):
        jev["model"] = env["FO_JEV_MODEL"].strip()
    if jev:
        over["jev"] = jev
    ledger: dict = {}
    if env.get("FO_LEDGER"):
        ledger["path"] = env["FO_LEDGER"].strip()
    if env.get("FO_LEDGER_LOG"):
        ledger["log"] = env["FO_LEDGER_LOG"].strip()
    if ledger:
        over["ledger"] = ledger
    # FO_TELEMETRY can only turn telemetry OFF. Turning it on needs the stored opt-in
    # (fo.py telemetry on / setup), so an environment variable can never override an opt-out.
    if env.get("FO_TELEMETRY") and _falsy(env["FO_TELEMETRY"]):
        over["telemetry"] = {"enabled": False}
    return over


LEGACY_VIA = ("bridge", "codex-plugin")  # removed in 0.1.0: such configs fall back to auto


def migrate(data: dict) -> dict:
    """Bring an older config up to date in memory (the file changes on its next write)."""
    for p in (data.get("providers") or {}).values():
        if isinstance(p, dict) and p.get("via") in LEGACY_VIA:
            p["via"] = "auto"
        if isinstance(p, dict):
            # providers.<vendor>.command was removed: it could replace the read-only command line
            # and no check could tell reliably whether it stayed read-only. It is ignored, never run.
            p.pop("command", None)
    return data


def removed_keys(path: str | None = None) -> list:
    """Keys in the config file (as written, before migrate) that this version ignores."""
    try:
        with open(path or config_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    provs = data.get("providers") if isinstance(data, dict) else None
    return [f"providers.{v}.command" for v, p in (provs or {}).items() if isinstance(p, dict) and "command" in p]


# Settings that belong to the engine they were saved for: where requests go and which key they carry.
ENGINE_SPECIFIC = ("base_url", "model", "api_key_env", "keychain_service", "api_key_file", "key_source",
                   "allow_insecure_http")


def load(path: str | None = None, with_env: bool = True) -> dict:
    return effective(read_file(path), with_env)


def effective(data: dict, with_env: bool = True) -> dict:
    """What load() would return if `data` were the config file: defaults, file, then environment."""
    cfg = deep_merge(DEFAULTS, migrate(json.loads(json.dumps(data))))
    if isinstance(cfg.get("ledger"), dict):
        for k in ("path", "log"):
            cfg["ledger"][k] = clean_path(cfg["ledger"].get(k))
    if with_env:
        saved = cfg["jev"].get("backend")
        cfg = deep_merge(cfg, env_overrides())
        if cfg["jev"].get("backend") != saved:
            # FO_JEV_BACKEND picked another engine: none of the saved engine's destination or key
            # settings may come along (only explicit FO_JEV_* values from the environment apply).
            env_jev = env_overrides().get("jev", {})
            for k in ENGINE_SPECIFIC:
                cfg["jev"][k] = env_jev.get(k, DEFAULTS["jev"][k])
    validate(cfg)
    return cfg


def clean_path(value):
    """The one normal form of a path setting: surrounding whitespace removed.

    Validation, storage and resolution all use this value, so " /tmp/notes" cannot pass the check
    as /tmp/notes and then resolve to a folder named " " in the current folder."""
    return value.strip() if isinstance(value, str) else value


PATH_KEYS = ("ledger.path", "ledger.log")


def broken_path_keys(data: dict) -> list:
    """The path keys that are set in `data` (a config file) but not valid."""
    led = data.get("ledger") if isinstance(data.get("ledger"), dict) else {}
    return [k for k in PATH_KEYS if led.get(k.split(".")[1]) not in (None, "")
            and path_problem(led.get(k.split(".")[1]), k)]


FO_PY = os.path.join(HERE, "fo.py")


def cli() -> str:
    """The full command that runs fo.py here, for hints a person can paste into a shell."""
    return "python3 -I " + shlex.quote(FO_PY)


def path_problem(value, name: str):
    """None when `value` is a full path (after ~ is expanded); else the reason.

    A relative ledger path would depend on the folder a command runs from: one run would make
    ./<value> in the current folder and the next run, from another folder, would not find it."""
    value = clean_path(value)
    if not isinstance(value, str) or not value:
        return f"{name} must be a folder path"
    if not os.path.isabs(os.path.expanduser(value)):
        return (f"{name} must be a full path that starts with / or ~, not {value!r} "
                "(a relative path would depend on the folder you run the command from)")
    return None


def validate_ledger(cfg: dict) -> None:
    for k in ("path", "log"):
        v = (cfg.get("ledger") or {}).get(k)
        if v not in (None, ""):
            problem = path_problem(v, f"ledger.{k}")
            if problem:
                raise ConfigError(problem)


def validate(cfg: dict) -> None:
    validate_ledger(cfg)
    backend = cfg["jev"].get("backend")
    if backend not in JEV_BACKENDS and backend != "custom":
        raise ConfigError(f"jev.backend must be one of {', '.join(list(JEV_BACKENDS) + ['custom'])}, not {backend!r}")
    if backend == "custom" and not cfg["jev"].get("base_url"):
        raise ConfigError("jev.backend custom needs jev.base_url")
    if cfg["jev"].get("key_source") not in (None, "env", "keychain", "file"):
        raise ConfigError("jev.key_source must be env, keychain or file")
    ttl = (cfg.get("refresh") or {}).get("ttl_hours")
    if ttl is not None and (isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not 0 < ttl <= 24 * 365):
        raise ConfigError("refresh.ttl_hours must be a number of hours above 0")
    for key in (("refresh", "upgrade_clis"), ("refresh", "agy_keychain_fallback"), ("telemetry", "enabled"),
                ("jev", "long_context"), ("jev", "allow_insecure_http")):
        v = (cfg.get(key[0]) or {}).get(key[1])
        if v is not None and not isinstance(v, bool):
            raise ConfigError(f"{'.'.join(key)} must be true or false")
    t = cfg["jev"].get("timeout_s")
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 < t <= 3600:
        raise ConfigError("jev.timeout_s must be a number of seconds between 0 and 3600")
    for vendor, p in cfg["providers"].items():
        if vendor not in VENDORS:
            raise ConfigError(f"unknown provider {vendor!r}: use {', '.join(VENDORS)}")
        for k in ("model",):
            if p.get(k) is not None and not isinstance(p.get(k), str):
                raise ConfigError(f"providers.{vendor}.{k} must be a string")
        if not isinstance(p.get("enabled", True), bool):
            raise ConfigError(f"providers.{vendor}.enabled must be true or false")
        via = p.get("via", "auto")
        if via != "auto" and via not in TRANSPORTS[vendor]:
            raise ConfigError(f"providers.{vendor}.via must be auto or one of {', '.join(TRANSPORTS[vendor])}")


def jev_settings(cfg: dict) -> dict:
    """Effective Jev settings: backend preset, then explicit config values on top."""
    j = cfg["jev"]
    backend = j.get("backend", "typesafe")
    preset = CUSTOM_BACKEND if backend == "custom" else JEV_BACKENDS[backend]
    out = {"backend": backend, **preset}
    for k in ("base_url", "model", "api_key_env", "keychain_service", "api_key_file", "timeout_s", "long_context",
              "allow_insecure_http", "key_source"):
        if j.get(k) not in (None, ""):
            out[k] = j[k]
    # The TypeSafe SDKs read TYPESAFE_BASE_URL; honour it for the typesafe backend only, as they do.
    if backend == "typesafe" and os.environ.get("TYPESAFE_BASE_URL") and not j.get("base_url"):
        out["base_url"] = os.environ["TYPESAFE_BASE_URL"].strip()
    out.setdefault("keychain_service", None)
    out["keychain_service"] = out.get("keychain_service") or out.get("api_key_env") or None
    out["base_url"] = (out.get("base_url") or "").rstrip("/")
    return out


def ledger_paths(cfg: dict) -> tuple[str, str]:
    """(ledger folder, log file). FO_VAULT keeps the old Obsidian layout working."""
    led = {k: clean_path(cfg["ledger"].get(k)) for k in ("path", "log")}
    vault = clean_path(os.environ.get("FO_VAULT", ""))
    if vault and not led.get("path") and path_problem(vault, "FO_VAULT"):
        raise ConfigError(path_problem(vault, "FO_VAULT"))
    for k in ("path", "log"):  # never resolve a value that the check did not see
        if led.get(k) and path_problem(led[k], f"ledger.{k}"):
            raise ConfigError(path_problem(led[k], f"ledger.{k}"))
    if led.get("path"):
        root = os.path.expanduser(led["path"])
    elif vault:
        root = os.path.join(os.path.expanduser(vault), "wiki", "learning", APP)
    else:
        root = os.path.join(data_home(), "ledger")
    if led.get("log"):
        log = os.path.expanduser(led["log"])
    elif vault and not led.get("path"):
        log = os.path.join(os.path.expanduser(vault), "wiki", "log.md")
    else:
        log = os.path.join(root, "log.md")
    return root, log


def get_dotted(cfg: dict, key: str):
    cur = cfg
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise ConfigError(f"no such key: {key}")
        cur = cur[part]
    return cur


def parse_value(raw: str):
    low = raw.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none", ""):
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def set_dotted(data: dict, key: str, value, check: bool = True) -> dict:
    parts = key.split(".")
    # Only keys that exist in DEFAULTS may be set, so a typo cannot hide.
    probe = DEFAULTS
    for part in parts:
        if not isinstance(probe, dict) or part not in probe:
            raise ConfigError(f"unknown key {key}; see `{cli()} config show` for the valid keys")
        probe = probe[part]
    if key in PATH_KEYS:
        value = clean_path(value)
    cur = data
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value
    if check:
        validate(deep_merge(DEFAULTS, data))
    return data


def set_many(data: dict, pairs: list) -> dict:
    """Set several (key, value) pairs, then check the result once.

    One call can repair keys that are only valid together: with both ledger.path and ledger.log
    broken, a single-key set would fail on the key it did not change."""
    for key, value in pairs:
        set_dotted(data, key, value, check=False)
    validate(deep_merge(DEFAULTS, data))
    return data


def roster(cfg: dict | None = None) -> dict:
    """Shipped roster merged with the user's roster overrides (same shape)."""
    with open(os.path.join(DATA_DIR, "roster.json"), encoding="utf-8") as f:
        base = json.load(f)
    user = os.path.join(config_dir(), "roster.json")
    if os.path.exists(user):
        try:
            with open(user, encoding="utf-8") as f:
                over = json.load(f)
        except (OSError, ValueError) as e:
            raise ConfigError(f"cannot read {user}: {e}") from e
        base = deep_merge(base, over)
        for mid, entry in list(base.get("models", {}).items()):
            if entry is None:
                del base["models"][mid]
    return base
