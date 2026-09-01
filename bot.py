import asyncio
import io
import os
import re
import sys
import time
import pathlib
import subprocess
import shutil
import json as _json
import hashlib
import uuid
import ipaddress
import urllib.parse
from datetime import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any

import aiohttp
import openai
import discord
from discord.ui import View, Select, Button
from discord import SelectOption, ButtonStyle

import config
import luraph
import luraph_v2
import luraph_v5
import luarmor
import luarmor2
import funcdumper
import funcdumper2
import simplespy
import pdump
import pastefy
import jnkie
import obscura
import keyforge
import moonveil_integration as _mv

NVIDIA_API_KEY = getattr(config, "NVIDIA_API_KEY", "")
NVIDIA_MODEL = getattr(config, "NVIDIA_MODEL", "deepseek-ai/deepseek-v4-flash-0731")
OWNER_IDS = list(getattr(config, "OWNER_IDS", [])) or [getattr(config, "OWNER_ID", 0)]
OWNER_ID = OWNER_IDS[0] if OWNER_IDS else 0

# ── queue/worker config ─────────────────────────────────────────────────────────
@dataclass
class EngineConfig:
    """Configurable engine analysis flags."""
    debug: bool = False
    constants: bool = False
    type_annotations: bool = False
    runtime_logs: bool = False
    hook_options: bool = False
    max_source_size: int = 5 * 1024 * 1024      # 5 MB
    max_output_size: int = 10 * 1024 * 1024     # 10 MB
    max_attachment_size: int = 100 * 1024 * 1024 # 100 MB
    queue_capacity: int = 1000
    per_user_limit: int = 5                      # concurrent jobs per user
    user_cooldown: float = 2.0                   # seconds between requests
    enable_redaction: bool = True                # redact secrets in output
    enable_fingerprint: bool = True              # SHA-256 source fingerprint
    enable_analysis: bool = True                 # function/URL count analysis
    allow_https_only: bool = True                # reject non-HTTPS URLs
    block_private_ips: bool = True               # block private/internal IPs
    safe_filenames: bool = True                  # sanitize output filenames

@dataclass
class WorkerStats:
    """Worker health statistics."""
    worker_id: int
    started_at: float
    jobs_processed: int = 0
    jobs_failed: int = 0
    total_time: float = 0.0
    current_job: Optional[str] = None
    last_activity: float = field(default_factory=time.time)

# Global engine config
ENGINE_CFG = EngineConfig()

# Worker pool management
WORKER_POOL: Dict[int, asyncio.Task] = {}
WORKER_STATS: Dict[int, WorkerStats] = {}
WORKER_COUNT = 3  # number of concurrent workers
QUEUE_CAPACITY = ENGINE_CFG.queue_capacity

# Per-user pacing
USER_QUEUE: Dict[int, List[float]] = defaultdict(list)  # user_id -> list of timestamps
USER_ACTIVE_JOBS: Dict[int, int] = defaultdict(int)     # user_id -> active job count

# Queue health
QUEUE_HEALTH = {
    "total_queued": 0,
    "total_processed": 0,
    "total_failed": 0,
    "total_rejected": 0,
    "start_time": time.time(),
}

NVIDIA_API_KEY = getattr(config, "NVIDIA_API_KEY", "")
NVIDIA_MODEL = getattr(config, "NVIDIA_MODEL", "deepseek-ai/deepseek-v4-flash-0731")
OWNER_IDS = list(getattr(config, "OWNER_IDS", [])) or [getattr(config, "OWNER_ID", 0)]
OWNER_ID = OWNER_IDS[0] if OWNER_IDS else 0

# ── config ────────────────────────────────────────────────────────────────────
TOKEN      = config.TOKEN
CHANNEL_IDS = config.CHANNEL_IDS
USER_TIMEOUT  = 300   # max timeout for normal users
ADMIN_TIMEOUT = 1800  # max timeout for admins (30 min)
MAX_DL     = 100 * 1024 * 1024

ROOT        = pathlib.Path(__file__).resolve().parent
LUNE        = ROOT / "lune.exe"
LUAJIT      = ROOT / "luajit.exe"                  # bundled LuaJIT — required by prometheus-v2
PY313       = pathlib.Path(f"{os.environ.get('LOCALAPPDATA', os.environ.get('USERPROFILE', ''))}\\Programs\\Python\\Python313\\python.exe")
PROM_V2_DIR = ROOT / "prometheus-v2"               # git clone https://github.com/0x251/Prometheus-DeobfuscatorV2.git prometheus-v2
UNVEILR_DIR = ROOT / "unveilr"                     # optional: place a separate unveilr engine here
LPH2_PY    = ROOT / "lph2"  / "LuraphDeobfuscator.py"  # .py wraps main.luau
IB2_DIR    = ROOT / "ib2deobf"
IB2_EXE    = IB2_DIR / "ib2deobf" / "LuaAnalysis.Ironbrew2.exe"
MOONSEC_DIR = ROOT / "MoonsecDeobfuscator"
# dotnet may build to net8.0, net9.0, or net6.0 depending on installed SDK
def _find_moonsec_exe() -> pathlib.Path:
    for tfm in ("net9.0", "net8.0", "net7.0", "net6.0"):
        p = MOONSEC_DIR / "bin" / "Release" / tfm / "MoonsecDeobfuscator.exe"
        if p.exists():
            return p
    # fallback — return the net8 path so the error message is deterministic
    return MOONSEC_DIR / "bin" / "Release" / "net8.0" / "MoonsecDeobfuscator.exe"
MOONSEC_EXE = _find_moonsec_exe()
PROM_DIR   = ROOT / "prometheus"
DARKLUA       = ROOT / "darklua.exe"
IRONVEIL_DIR  = ROOT / "ironveil" / "deobfuscator"
SEVENSEVEN_EXE = ROOT / "77fuscatorDeobfuscator" / "bin" / "Debug" / "net8.0" / "deobfuscator.exe"
LURAPH_VMP_DIR = ROOT / "luau-vmp-deobf"            # github.com/binxgtl/luau-vmp-deobf
LURAPH_LOGGER  = ROOT / "luraph_runtime_logger.lua"  # runtime VM dump logger (executor script)
TMP           = ROOT / "bot_tmp"
TMP.mkdir(exist_ok=True)

LUAJIT          = "C:\\Users\\Gian Ada\\AppData\\Local\\Programs\\LuaJIT\\bin\\luajit.exe"
PROMETHEUS_CLI  = ROOT / "prometheus" / "prometheus-main.lua"
PROMETHEUS_DIR  = ROOT / "prometheus"

# Resolve Node.js full path so subprocess doesn't rely on PATH
_NODE: pathlib.Path | None = None
_node_candidates = [ROOT / "node.exe", ROOT / "nodejs.exe"]
for c in ("node", "nodejs"):
    found = shutil.which(c)
    if found:
        _node_candidates.append(pathlib.Path(found))
for p in _node_candidates:
    if p.exists():
        _NODE = p
        break
if _NODE is None:
    _NODE = pathlib.Path("node")  # fallback — let OS PATH handle it

ROOT_STR = str(ROOT).replace("\\", "/")
ROOT_STR_WIN = str(ROOT).replace("/", "\\")

def _redact(text: str) -> str:
    """Remove filesystem paths from user-facing output."""
    import re as _re
    text = text.replace(ROOT_STR, "[...]")
    text = text.replace(ROOT_STR_WIN, "[...]")
    text = _re.sub(r"[a-zA-Z]:\\(?:[^\\\"]{1,64}\\){0,5}", "[...]", text)
    text = _re.sub(r"/home/[^/\s]{1,64}", "[...]", text)
    return text

def _redact_secrets(text: str) -> str:
    """Redact sensitive token-like values from output."""
    if not ENGINE_CFG.enable_redaction:
        return text
    import re as _re
    # Discord bot tokens
    text = _re.sub(r"(Bot\s+)?[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27}", "[REDACTED_TOKEN]", text)
    # Generic API keys / secrets
    text = _re.sub(r"(api[_-]?key|secret|token|password|auth)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{20,}[\"']?", r"\1=[REDACTED]", text, flags=_re.IGNORECASE)
    # Webhook URLs
    text = _re.sub(r"https?://discord\.com/api/webhooks/\d+/[A-Za-z0-9_\-]+", "[REDACTED_WEBHOOK]", text)
    text = _re.sub(r"https?://pastefy\.app/api/v2/paste\?[^\"'\s]+", "[REDACTED_PASTEFY]", text)
    # Bearer tokens
    text = _re.sub(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}", "Bearer [REDACTED]", text)
    return text

def _sanitize_filename(name: str) -> str:
    """Sanitize filename to prevent path traversal and unsafe characters."""
    if not ENGINE_CFG.safe_filenames:
        return name
    import re as _re
    # Remove path traversal attempts
    name = _re.sub(r"(\.\./|\.\.\\|~/)", "", name)
    # Replace unsafe characters
    name = _re.sub(r'[<>:"|?*\x00-\x1f]', "_", name)
    # Limit length
    name = name[:200]
    # Ensure not empty
    if not name.strip():
        name = "output"
    return name

def _validate_source_url(url: str) -> tuple[bool, str]:
    """Validate source URL: HTTPS only, no private IPs, no localhost."""
    if not ENGINE_CFG.allow_https_only:
        return True, ""
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            return False, "Only HTTPS URLs are allowed"
        if ENGINE_CFG.block_private_ips and parsed.hostname:
            try:
                ip = ipaddress.ip_address(parsed.hostname)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                    return False, "Private/internal IP addresses are not allowed"
            except ValueError:
                # Hostname, not IP - check for localhost variants
                hostname = parsed.hostname.lower()
                if hostname in ("localhost", "localhost.localdomain", "127.0.0.1", "::1", "0.0.0.0"):
                    return False, "Localhost URLs are not allowed"
    except Exception as e:
        return False, f"Invalid URL: {e}"
    return True, ""

def _check_size_limit(data: bytes, limit: int, label: str) -> tuple[bool, str]:
    """Check if data exceeds size limit."""
    if len(data) > limit:
        return False, f"{label} exceeds maximum size ({len(data) / 1024 / 1024:.1f} MB > {limit / 1024 / 1024:.1f} MB)"
    return True, ""

def _compute_fingerprint(data: bytes) -> str:
    """Compute SHA-256 fingerprint of source."""
    if not ENGINE_CFG.enable_fingerprint:
        return ""
    return hashlib.sha256(data).hexdigest()[:16]

def _analyze_source(source: str) -> dict:
    """Analyze source for function count, URL count, etc."""
    if not ENGINE_CFG.enable_analysis:
        return {}
    import re as _re
    lines = source.splitlines()
    func_count = len(_re.findall(r"function\s+\w+|function\s*\(|:\s*function\s*\(", source))
    url_count = len(_re.findall(r"https?://[^\s\"'>)]+", source))
    require_count = len(_re.findall(r"require\s*\(", source))
    loadstring_count = len(_re.findall(r"loadstring\s*\(", source))
    return {
        "lines": len(lines),
        "functions": func_count,
        "urls": url_count,
        "requires": require_count,
        "loadstrings": loadstring_count,
    }

def _check_user_pacing(user_id: int) -> tuple[bool, str]:
    """Check per-user request pacing and concurrent job limits."""
    now = time.time()
    # Clean old timestamps
    USER_QUEUE[user_id] = [ts for ts in USER_QUEUE[user_id] if now - ts < 60]
    
    if USER_ACTIVE_JOBS[user_id] >= ENGINE_CFG.per_user_limit:
        return False, f"Too many concurrent jobs ({USER_ACTIVE_JOBS[user_id]}/{ENGINE_CFG.per_user_limit})"
    
    if USER_QUEUE[user_id] and now - USER_QUEUE[user_id][-1] < ENGINE_CFG.user_cooldown:
        return False, f"Rate limited - wait {ENGINE_CFG.user_cooldown}s between requests"
    
    USER_QUEUE[user_id].append(now)
    USER_ACTIVE_JOBS[user_id] += 1
    return True, ""

def _release_user_slot(user_id: int):
    """Release a user's job slot."""
    USER_ACTIVE_JOBS[user_id] = max(0, USER_ACTIVE_JOBS.get(user_id, 1) - 1)

def _queue_health_check() -> dict:
    """Return current queue health metrics."""
    return {
        "queue_size": queue.qsize(),
        "capacity": QUEUE_CAPACITY,
        "utilization": f"{queue.qsize() / QUEUE_CAPACITY * 100:.1f}%",
        "total_queued": QUEUE_HEALTH["total_queued"],
        "total_processed": QUEUE_HEALTH["total_processed"],
        "total_failed": QUEUE_HEALTH["total_failed"],
        "total_rejected": QUEUE_HEALTH["total_rejected"],
        "uptime": f"{time.time() - QUEUE_HEALTH['start_time']:.0f}s",
        "workers": len(WORKER_POOL),
        "worker_stats": {wid: {"jobs": ws.jobs_processed, "failed": ws.jobs_failed, "current": ws.current_job} for wid, ws in WORKER_STATS.items()},
        "per_user_active": dict(USER_ACTIVE_JOBS),
    }

def _is_admin(member: discord.Member | None) -> bool:
    """Check if a member has Administrator permission.  Returns False for DM users."""
    if member is None:
        return False
    if not isinstance(member, discord.Member):
        return False
    return member.guild_permissions.administrator

def _is_owner(member: discord.User | discord.Member | None) -> bool:
    """Check if a member is a bot owner."""
    if member is None:
        return False
    return member.id in OWNER_IDS

# ── Token / Premium / Blacklist / Key System ──────────────────────────────────

BOOSTER_ROLE_ID = 1526146377480929330
PREMIUM_ROLE_ID = 1527849762852831483

MAX_TOKENS = 100
NEW_USER_TOKENS = 5
TOKEN_RESET_INTERVAL = 3600
TOKENS_PER_RESET = 2
BOOSTER_REFUND_INTERVAL = 180
BOOSTER_REFUND_AMOUNT = 10
GIVE_COOLDOWN = 10800
GIVE_MAX = 4
BLACKLIST_MAX = 3600

TOKEN_FILE        = ROOT / "tokens.json"
BLACKLIST_FILE    = ROOT / "blacklist.json"
GIVE_COOLDOWN_FILE = ROOT / "give_cooldown.json"
KEY_FILE          = ROOT / "keys.json"
TOS_FILE          = ROOT / "tos_accepted.json"

TOKEN_COMMANDS = {".lua", ".l", ".r", ".r2", ".l2", ".25ms", ".unveilr", ".d", ".ironveil", ".unluac", ".relua", ".relua2", ".lph", ".lphv2", ".lphv3", ".lphv5", ".luarmor", ".luarmor2", ".45ms", ".6vms", ".help3", ".kolenv", ".mimic", ".old45ms", ".flamecoder", ".pengue", ".polyester", ".promdeobf", ".promdeobf2", ".zala", ".oldlarry", ".mimic2", ".moondeobf", ".aspect", ".unveilkitty", ".moonveildeobf", ".decompiler", ".disassembler", ".devirtualize", ".decode", ".xor", ".b64", ".hex", ".analyze", ".explain", ".fix", ".convert", ".deep", ".rewrite", ".dump", ".scan", ".wat", ".anti", ".loadstring", ".patch", ".diff", ".ai2", ".ultra", ".ultra2", ".vm", ".chain", ".bulk", ".mega", ".megalune", ".deobf", ".obf2", ".antienv", ".funcdumper", ".simplespy", ".luraphdeobf", ".luraphdeobf2", ".aicfg", ".keyforge", ".status"}

_token_store = {}
_blacklist_store = {}
_give_cd_store = {}
_key_store: dict[str, dict] = {}
_tos_store: set = set()

def _load_store(path, default):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except: return default

def _save_store(path, data):
    try: path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except: pass

def _load_token_data():
    global _token_store
    _token_store = _load_store(TOKEN_FILE, {})
def _save_token_data():
    _save_store(TOKEN_FILE, _token_store)

def _load_blacklist_data():
    global _blacklist_store
    _blacklist_store = _load_store(BLACKLIST_FILE, {})
def _save_blacklist_data():
    _save_store(BLACKLIST_FILE, _blacklist_store)

def _load_give_cd():
    global _give_cd_store
    _give_cd_store = _load_store(GIVE_COOLDOWN_FILE, {})
def _save_give_cd():
    _save_store(GIVE_COOLDOWN_FILE, _give_cd_store)

def _load_keys():
    global _key_store
    _key_store = _load_store(KEY_FILE, {})
def _save_keys():
    _save_store(KEY_FILE, _key_store)

def _load_tos():
    global _tos_store
    data = _load_store(TOS_FILE, {})
    _tos_store = set(data.get("accepted", []))
def _save_tos():
    _save_store(TOS_FILE, {"accepted": sorted(_tos_store)})

def _has_accepted_tos(user_id) -> bool:
    return user_id in _tos_store

def _accept_tos(user_id):
    _tos_store.add(user_id)
    _save_tos()

def _has_role(member, role_id):
    return member and any(r.id == role_id for r in getattr(member, "roles", []))

def _parse_duration(text: str) -> int | None:
    """Parse duration strings like 7d, 30d, 12h, 3600s, 'perm'. Returns seconds or None for permanent."""
    if not text:
        return None
    text = text.lower().strip()
    if text in ("perm", "permanent", "inf", "infinity", "forever", "0"):
        return None
    m = re.match(r"(\d+)\s*(d|day|days|h|hr|hrs|hour|hours|m|min|mins|minute|minutes|s|sec|secs|second|seconds)?$", text)
    if not m:
        return None
    val = int(m.group(1))
    unit = m.group(2) or "d"
    mult = {"d": 86400, "day": 86400, "days": 86400,
            "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
            "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
            "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1}
    return val * mult.get(unit, 86400)

def _is_premium(member):
    return _has_role(member, PREMIUM_ROLE_ID)

def _is_booster(member):
    return _has_role(member, BOOSTER_ROLE_ID)

def _tos_gated(member) -> bool:
    """Premium, boosters and admins bypass the Terms of Service gate."""
    return not (_is_premium(member) or _is_booster(member) or _is_admin(member))

def _blacklist_expiry(user_id):
    exp = _blacklist_store.get(str(user_id))
    if exp and time.time() < exp:
        return exp
    if exp:
        del _blacklist_store[str(user_id)]
        _save_blacklist_data()
    return None

def _init_user(e: dict, now: float) -> dict:
    """Initialize a new user with starting tokens if first-ever access."""
    if "tokens" not in e:
        e["tokens"] = NEW_USER_TOKENS
        e["reset_at"] = now
    elif now - e.get("reset_at", 0) >= TOKEN_RESET_INTERVAL:
        e["tokens"] = TOKENS_PER_RESET
        e["reset_at"] = now
    return e

def _get_tokens(user_id):
    uid = str(user_id); now = time.time()
    e = _token_store.get(uid, {})
    e = _init_user(e, now)
    _token_store[uid] = e
    return e["tokens"]

def _spend_token(user_id) -> bool:
    uid = str(user_id); now = time.time()
    e = _token_store.get(uid, {})
    e = _init_user(e, now)
    if e.get("tokens", 0) <= 0:
        return False
    e["tokens"] -= 1
    _token_store[uid] = e
    _save_token_data()
    return True

def _add_tokens(user_id: str, amount: int):
    """Grant token balance to a user (used by .redeem)."""
    now = time.time()
    e = _token_store.get(user_id, {})
    e = _init_user(e, now)
    e["tokens"] = e.get("tokens", 0) + amount
    _token_store[user_id] = e
    _save_token_data()

def _gen_key(tokens: int, duration_sec: int | None, key_type: str = "token") -> str:
    """Generate a redeem key. key_type: 'token' (tokens+premium) or 'premium' (premium only)."""
    import secrets
    key = "6VMS-" + secrets.token_hex(8).upper()
    now = time.time()
    _key_store[key] = {
        "type": key_type,
        "tokens": tokens,
        "duration_sec": duration_sec,
        "expires_at": (now + 86400 * 365) if duration_sec is None else (now + duration_sec),
        "redeemed_by": None,
        "created_at": now,
    }
    _save_keys()
    _dump_premium_keys()
    return key

def _dump_premium_keys():
    """Write all unredeemed keys to premium_keys.txt."""
    unredeemed = {k: v for k, v in _key_store.items() if v.get("redeemed_by") is None}
    lines = [f"# 6Vms keys ({len(unredeemed)} total, updated {time.strftime('%Y-%m-%d %H:%M:%S')})"]
    for kid, kdata in unredeemed.items():
        ktype = kdata.get("type", "?")
        tokens = kdata.get("tokens", 0)
        dur = kdata.get("duration_sec")
        label = "permanent" if dur is None else f"{dur}s"
        lines.append(f"{kid} | type={ktype} | tokens={tokens} | duration={label} | expires={kdata.get('expires_at', '?')}")
    try:
        (ROOT / "premium_keys.txt").write_text("\n".join(lines))
    except Exception:
        pass

def _redeem_key(key: str, user_id: str) -> str | None:
    """Redeem a key for a user. Returns None on success, or an error message string."""
    k = _key_store.get(key)
    if not k:
        return "Invalid key."
    if k["redeemed_by"] is not None:
        return "Key already redeemed."
    if k["expires_at"] < time.time():
        return "Key has expired."
    k["redeemed_by"] = user_id
    dur = k["duration_sec"]
    if k.get("type") != "premium":
        _add_tokens(user_id, k["tokens"])
    _save_keys()
    return None

def _maybe_refund_booster(member):
    if not _is_booster(member):
        return
    uid = str(member.id); now = time.time()
    e = _token_store.get(uid, {})
    e = _init_user(e, now)
    if now - e.get("refund_at", 0) >= BOOSTER_REFUND_INTERVAL:
        e["tokens"] = min(MAX_TOKENS, e.get("tokens", 0) + BOOSTER_REFUND_AMOUNT)
        e["refund_at"] = now
        _token_store[uid] = e
        _save_token_data()

# ── RELUA2 per‑channel config ─────────────────────────────────────────────────

RELUA2_DEFAULT_CFG = {
    "bypassantienv": True,
    "constants": False,
    "explore_funcs": True,
    "hook_op": True,
    "inf_loop_guard": True,
    "log_errors": False,
    "minifier": False,
    "pretty": True,
    "spy_exec_only": False,
    "timeout": 180,
    "type_annotations": False,
}
_relua2_cfgs: dict[int, dict] = {}

_LPH_COOLDOWNS: dict[int, float] = {}
_LURAPH_COOLDOWNS: dict[int, float] = {}

def _get_relua2_cfg(channel_id: int) -> dict:
    return _relua2_cfgs.setdefault(channel_id, dict(RELUA2_DEFAULT_CFG))

ACCENT = 0x5865F2
GOOD   = 0x57F287
BAD    = 0xED4245
WARN   = 0xFEE75C

# ── 25ms / main.luau runtime settings ────────────────────────────────────────
# These map 1:1 to the Settings table in main.luau and are passed as CLI args.
# Each entry: (display_label, description, default_value)
_CFG_DEFS: dict[str, tuple[str, str, bool]] = {
    "hookOp":           ("Hook Op",           "Enable operation hooks (comparisons, loops)",        True),
    "minifier":         ("Minifier",           "Minify / compact the output",                        True),
    "explore_funcs":    ("Explore Funcs",      "Recursively explore inner functions",                True),
    "inf_loops":        ("Inf Loop Guard",     "Detect and break infinite while loops",              True),
    "discord":          ("Discord Mode",       "Extra logging for discord.gg / invite links",        True),
    "constants":        ("Constants",          "Extract and label constant strings",                 False),
    "spyexeconly":      ("Spy Exec Only",      "Only spy on executor-specific globals",              False),
    "roblox":           ("Roblox Strict",      "Strict Roblox type checks (errors on bad types)",   False),
    "type_annotations": ("Type Annotations",   "Emit Luau type annotations in output",               False),
    "isPremium":        ("Is Premium",         "Enable premium features (HTTP GET requests)",        True),
    "lua":              ("Lua Mode",           "Treat script as plain Lua 5.1 (not Luau)",           False),
}

# ── Goofyscator obfuscator settings ───────────────────────────────────────────
# Each entry: (display_label, description, default_value)
_OBF_CFG_DEFS: dict[str, tuple[str, str, bool]] = {
    "dontModifyBytecode": ("Don't Modify Bytecode", "Preserve original bytecode",            False),
    "dontAddAntitamper":  ("Don't Add Antitamper",  "Skip antitamper protection",             False),
    "encodeNumbers":      ("Encode Numbers",        "Obfuscate numeric literals",             True),
    "renameGlobals":      ("Rename Globals",        "Rename global variable references",      False),
}

_OBF_GENERATORS = ["Number", "String"]

# Global settings store — keyed by channel_id (int), falls back to defaults.
_cfg_store: dict[int, dict[str, bool]] = {}
CFG_FILE = ROOT / "cfg_settings.json"

def _load_cfg():
    """Load persisted per-channel settings from disk into _cfg_store."""
    if not CFG_FILE.exists():
        return
    try:
        raw = _json.loads(CFG_FILE.read_text(encoding="utf-8"))
        for ch_id, vals in raw.items():
            if not isinstance(vals, dict):
                continue
            entry = {}
            for k, v in vals.items():
                if k == "_runtime":
                    entry[k] = str(v) if v in ("lune", "lute") else "lune"
                elif k == "_timeout":
                    v_int = int(v) if isinstance(v, (int, float)) else 180
                    # migrate old defaults
                    entry[k] = 180 if v_int == 300 else v_int
                else:
                    entry[k] = bool(v)
            _cfg_store[int(ch_id)] = entry
    except Exception as e:
        print(f"[6Vms] cfg load warning: {e}")

def _save_cfg():
    """Persist _cfg_store to disk."""
    try:
        CFG_FILE.write_text(
            _json.dumps({str(k): v for k, v in _cfg_store.items()}, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[6Vms] cfg save warning: {e}")

_load_cfg()  # load persisted settings at import time

def _get_cfg(channel_id: int) -> dict:
    """Return a copy of the settings for a channel, filled with defaults.
    Also includes _runtime ('lune'|'lute') and _timeout (int seconds)."""
    defaults: dict = {k: v[2] for k, v in _CFG_DEFS.items()}
    defaults["_runtime"] = "lune"   # default runtime for .l
    defaults["_timeout"] = 64
    stored = _cfg_store.get(channel_id, {})
    return {**defaults, **stored}

# ── obfuscator config persistence ──────────────────────────────────────────────
_obf_cfg_store: dict[int, dict] = {}
_OBF_CFG_FILE = ROOT / "obf_settings.json"

def _load_obf_cfg():
    if not _OBF_CFG_FILE.exists():
        return
    try:
        raw = _json.loads(_OBF_CFG_FILE.read_text(encoding="utf-8"))
        for ch_id, vals in raw.items():
            if not isinstance(vals, dict):
                continue
            entry = {}
            for k, v in vals.items():
                if k == "_generator":
                    entry[k] = str(v) if v in _OBF_GENERATORS else "Number"
                else:
                    entry[k] = bool(v)
            _obf_cfg_store[int(ch_id)] = entry
    except Exception as e:
        print(f"[6Vms] obf cfg load warning: {e}")

def _save_obf_cfg():
    try:
        _OBF_CFG_FILE.write_text(
            _json.dumps({str(k): v for k, v in _obf_cfg_store.items()}, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[6Vms] obf cfg save warning: {e}")

def _get_obf_cfg(channel_id: int) -> dict:
    defaults: dict = {k: v[2] for k, v in _OBF_CFG_DEFS.items()}
    defaults["_generator"] = "Number"
    stored = _obf_cfg_store.get(channel_id, {})
    return {**defaults, **stored}

_load_obf_cfg()

# ── AI config persistence ──────────────────────────────────────────────────────
_AI_MODELS = [
    "deepseek-ai/deepseek-v4-flash-0731",
    "deepseek-ai/deepseek-v4-flash",
    "deepseek-ai/deepseek-v4-pro",
    "deepseek-ai/deepseek-r1",
    "meta/llama-3.3-70b-instruct",
    "meta/llama-3.1-8b-instruct",
    "mistralai/mistral-large",
    "mistralai/mixtral-8x7b-instruct",
    "nvidia/llama-3.1-nemotron-70b-instruct",
    "qwen/qwen2.5-72b-instruct",
    "microsoft/phi-4",
    "google/gemma-2-27b-it",
]
_AI_TEMPS     = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0]
_AI_TOKEN_OPTS = [1024, 2048, 4096, 8192, 16384]

_ai_cfg: dict = {
    "model": "deepseek-ai/deepseek-v4-flash-0731",
    "temperature": 0.3,
    "max_tokens": 16384,
    "thinking": False,
}
_AI_CFG_FILE = ROOT / "ai_settings.json"

def _load_ai_cfg():
    """Load persisted AI config from disk into _ai_cfg."""
    if not _AI_CFG_FILE.exists():
        return
    try:
        raw = _json.loads(_AI_CFG_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            if str(raw.get("model")) in _AI_MODELS:
                _ai_cfg["model"] = str(raw["model"])
            try:
                t = float(raw.get("temperature", 0.3))
                if t in _AI_TEMPS:
                    _ai_cfg["temperature"] = t
            except (TypeError, ValueError):
                pass
            try:
                mt = int(raw.get("max_tokens", 16384))
                if mt in _AI_TOKEN_OPTS:
                    _ai_cfg["max_tokens"] = mt
            except (TypeError, ValueError):
                pass
            _ai_cfg["thinking"] = bool(raw.get("thinking", False))
    except Exception as e:
        print(f"[6Vms] ai cfg load warning: {e}")

def _save_ai_cfg():
    """Persist _ai_cfg to disk."""
    try:
        _AI_CFG_FILE.write_text(_json.dumps(_ai_cfg, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[6Vms] ai cfg save warning: {e}")

_load_ai_cfg()

def _cfg_to_args(cfg: dict) -> list[str]:
    """Convert a settings dict to main.luau CLI arg strings (e.g. '!hookOp').
    Skips the non-boolean meta-keys (_runtime, _timeout)."""
    args = []
    for key, val in cfg.items():
        if key not in _CFG_DEFS:
            continue  # skip _runtime, _timeout, any future meta-keys
        default = _CFG_DEFS[key][2]
        if val != default:
            args.append(key if val else f"!{key}")
    return args

URL_RE  = re.compile(r"https?://[^\s<>()]+", re.I)
TIME_RE = re.compile(r"Finished processing in ([\d.]+) seconds", re.I)
# Matches lune's own status brackets — [lune], [info], [warn], [debug], [trace]
# These are NOT errors; real errors look like  [string "..."]:3: attempt to...
_LUNE_META_RE = re.compile(r"^\[(lune|info|warn|debug|trace)\]", re.IGNORECASE)
OK_EXT  = (".lua", ".txt", ".tsv")

HEADER = "-- [[ File Generated By 6Vms https://discord.gg/XEP4KMaCVH ]]\n"

# Every third-party / tool-generated header comment we want to strip.
# Each pattern matches a full comment line (including its trailing newline).
_STRIP_PATTERNS: list[re.Pattern] = [
    # ── 25ms / revea branding ──────────────────────────────────────────────
    re.compile(r"^--[^\n]*(?:Made by 25ms|discord\.gg/25ms|starting the dummppP|25ms)[^\n]*\n?",
               re.IGNORECASE | re.MULTILINE),

    # ── UnveilR / threaded branding ────────────────────────────────────────
    # "This file was generated with UnveilR V... at discord.gg/threaded..."
    re.compile(r"^--[^\n]*(?:UnveilR|discord\.gg/threaded|generated with UnveilR)[^\n]*\n?",
               re.IGNORECASE | re.MULTILINE),

    # ── Generic "generated by" tool headers ───────────────────────────────
    # Catches: "-- This file was generated with ..."
    #          "-- Generated by ..."
    #          "-- Auto-generated by ..."
    re.compile(r"^--\s*(?:This file was |Auto-)?[Gg]enerated (?:with|by)\b[^\n]*\n?",
               re.MULTILINE),

    # ── LeakD beautifier branding ─────────────────────────────────────────
    re.compile(r"^--[^\n]*(?:Beautified by LeakD|LeakD|discord\.gg/qteAQmfJmP)[^\n]*\n?",
               re.IGNORECASE | re.MULTILINE),

    # ── HTTP-GET success notices written by main.luau ─────────────────────
    # "-- Successfully sent http GET requests to the following site(s): ..."
    re.compile(r"^--[^\n]*Successfully sent http GET requests[^\n]*\n?",
               re.IGNORECASE | re.MULTILINE),

    # ── LPH Devirtualizer / 6Vms Luraph headers ──────────────────────────
    re.compile(r"-- \[\[ 6Vms Luraph Devirtualizer \]\].*?(?=\n-- =+|\Z)",
               re.DOTALL | re.IGNORECASE),
    re.compile(r"^--[^\n]*LPH DEVIRTUALIZER[^\n]*\n?", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^--[^\n]*DEVIRTUALIZER REPORT[^\n]*\n?", re.IGNORECASE | re.MULTILINE),

    # ── discord invite redaction left by main.luau ────────────────────────
    # main.luau replaces discord.gg/xxx with "(discord invite)" in output
    # Sometimes the surrounding comment line ends up as a lone stub
    re.compile(r"^--[^\n]*\(discord invite\)[^\n]*\n?",
               re.IGNORECASE | re.MULTILINE),

    # ── Luraph / LPH tool markers ─────────────────────────────────────────
    re.compile(r"^--[^\n]*(?:LuraphDeobfuscator|lph2|LPH Deobfuscator)[^\n]*\n?",
               re.IGNORECASE | re.MULTILINE),

    # ── IronBrew tool markers ─────────────────────────────────────────────
    re.compile(r"^--[^\n]*(?:IronBrew|ironbrew)[^\n]*\n?",
               re.MULTILINE),

    # ── Prometheus tool markers ───────────────────────────────────────────
    re.compile(r"^--[^\n]*(?:Prometheus Deobfuscator|deobfuscated by prometheus)[^\n]*\n?",
               re.IGNORECASE | re.MULTILINE),

    # ── Generic "deobfuscated by" / "obfuscated with" one-liners ──────────
    re.compile(r"^--[^\n]*(?:deobfuscated by|obfuscated (?:with|by))\b[^\n]*\n?",
               re.IGNORECASE | re.MULTILINE),

    # ── Goofyscator branding ─────────────────────────────────────────────
    re.compile(r"^--[^\n]*goofyscator[^\n]*\n?", re.IGNORECASE | re.MULTILINE),

    # ── 6Vms own header (prevent double-stamping on re-runs) ──────────────
    re.compile(r"^--\s*\[\[\s*File Generated By 6Vms[^\n]*\]\]\n?",
               re.IGNORECASE | re.MULTILINE),
]

# ── helpers ───────────────────────────────────────────────────────────────────
def _kill_tree(pid: int):
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)

def _stamp() -> str:
    return f"{int(time.time()*1000)}_{os.getpid()}"

async def react(msg, emoji):
    try: await msg.add_reaction(emoji)
    except discord.HTTPException: pass

async def unreact(msg, emoji):
    try: await msg.remove_reaction(emoji, bot.user)
    except discord.HTTPException: pass

async def gather_jobs(message) -> list[dict]:
    sources = [message]
    if message.reference and message.reference.resolved:
        sources.append(message.reference.resolved)
    jobs, seen = [], set()
    for src in sources:
        for att in getattr(src, "attachments", []):
            if att.filename.lower().endswith(OK_EXT) and att.id not in seen:
                seen.add(att.id)
                jobs.append({"name": att.filename, "att": att, "url": None})
        text = getattr(src, "content", "") or ""
        for url in URL_RE.findall(text):
            url = url.rstrip(".,)`'\"")
            if url in seen: continue
            seen.add(url)
            name = url.split("?")[0].rstrip("/").split("/")[-1] or "script"
            if not name.lower().endswith(OK_EXT):
                name += ".lua"
            jobs.append({"name": name, "att": None, "url": url})
    return jobs

async def fetch_source(job) -> bytes:
    if job.get("att") is not None:
        try:
            return await job["att"].read()
        except Exception:
            # Attachment CDN reads can 403/timeout in discord.py — fall back to
            # a direct download through our shared session.
            if http is None:
                raise
            async with http.get(job["att"].url, timeout=aiohttp.ClientTimeout(total=120)) as r:
                r.raise_for_status()
                return await r.read()
    if http is None:
        raise RuntimeError("HTTP session not ready yet")
    async with http.get(job["url"], timeout=aiohttp.ClientTimeout(total=120)) as r:
        r.raise_for_status()
        chunks, total = [], 0
        async for part in r.content.iter_chunked(65536):
            total += len(part)
            if total > MAX_DL: raise ValueError("file too large")
            chunks.append(part)
        return b"".join(chunks)

def _run_proc(cmd: list, cwd=None, timeout: int = ADMIN_TIMEOUT) -> tuple[bool, str, float]:
    started = time.perf_counter()
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd or ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except (FileNotFoundError, OSError) as exc:
        return False, f"failed to start: {exc}", time.perf_counter() - started
    try:
        log, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        try: proc.communicate(timeout=5)
        except Exception: pass
        return False, "timeout", timeout
    if log:
        log = _redact(log)
    took = time.perf_counter() - started
    if proc.returncode != 0:
        lines = [l.strip() for l in (log or "").strip().splitlines() if l.strip()]
        err_lines = [l for l in lines if not _LUNE_META_RE.match(l) and not l.startswith("Node.js v")]
        msg = (err_lines[-1] if err_lines else lines[-1] if lines else "unknown error")[:300]
        return False, msg, took
    return True, log or "", took

# ── output post-processing ────────────────────────────────────────────────────

def _stamp_output(text: str) -> str:
    """
    1. Strip all known third-party / tool-generated header comment lines.
    2. Collapse runs of blank lines left behind.
    3. Prepend the 6Vms header.
    """
    for pat in _STRIP_PATTERNS:
        text = pat.sub("", text)
    # Collapse 3+ consecutive blank lines down to one
    text = re.sub(r"\n{3,}", "\n\n", text)
    return HEADER + text.lstrip("\n")

def _rand_name(suffix: str) -> str:
    """Generate a randomised output filename like `6vms_a3f8c2b1.suffix`."""
    import secrets as _sec
    return f"6vms_{_sec.token_hex(4)}.{suffix}"

def _ensure_dirs():
    """Guarantee all working directories exist before any tool runs."""
    TMP.mkdir(exist_ok=True)
    (ROOT / "dumps" / "original").mkdir(parents=True, exist_ok=True)
    (ROOT / "dumps" / "dumped").mkdir(parents=True, exist_ok=True)
    IB2_DIR.mkdir(parents=True, exist_ok=True)
    PROM_DIR.mkdir(parents=True, exist_ok=True)

_ensure_dirs()  # run once at import time

# ── deobfuscator runners ──────────────────────────────────────────────────────

def _run_luraph_strings(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    """Generate Luraph v14.7 string dumper injection script for Roblox executor."""
    started = time.perf_counter()
    try:
        injection_script = luraph.build(src_bytes, key="")
        took = time.perf_counter() - started
        return True, injection_script.encode("utf-8"), took
    except Exception as ex:
        took = time.perf_counter() - started
        return False, _redact(f"luraph injection failed: {ex}")[:300], took

def _run_luraph_v2(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    """Generate LPH V2 string dumper injection script for Roblox executor."""
    started = time.perf_counter()
    try:
        injection_script = luraph_v2.build_bytes(src_bytes, key="")
        took = time.perf_counter() - started
        return True, injection_script.encode("utf-8"), took
    except Exception as ex:
        took = time.perf_counter() - started
        return False, _redact(f"luraph v2 injection failed: {ex}")[:300], took

def _run_luraph_v5(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    """Generate 6Vms Luraph 14.7/14.8 decryptor injection script for Roblox executor."""
    started = time.perf_counter()
    try:
        injection_script = luraph_v5.build_bytes(src_bytes)
        took = time.perf_counter() - started
        return True, injection_script.encode("utf-8"), took
    except Exception as ex:
        took = time.perf_counter() - started
        return False, _redact(f"6Vms lphv5 injection failed: {ex}")[:300], took

def _run_luarmor(name: str) -> tuple[bool, bytes | str, float]:
    """Generate 6Vms Luarmor logger injection script for Roblox executor."""
    started = time.perf_counter()
    try:
        script = luarmor.build_bytes()
        took = time.perf_counter() - started
        return True, script.encode("utf-8"), took
    except Exception as ex:
        took = time.perf_counter() - started
        return False, _redact(f"luarmor logger failed: {ex}")[:300], took

def _run_luarmor2(payload: str | None = None) -> tuple[bool, bytes | str, float]:
    """Generate 6Vms Luarmor 2 logger/dumper injection script for Roblox executor."""
    started = time.perf_counter()
    try:
        script = luarmor2.build_bytes(payload=payload)
        took = time.perf_counter() - started
        return True, script.encode("utf-8"), took
    except Exception as ex:
        took = time.perf_counter() - started
        return False, _redact(f"luarmor2 logger failed: {ex}")[:300], took

def _run_funcdumper(name: str) -> tuple[bool, bytes | str, float]:
    """Generate 6Vms Function Dumper injection script for Roblox executor."""
    started = time.perf_counter()
    try:
        script = funcdumper.build_bytes()
        took = time.perf_counter() - started
        return True, script.encode("utf-8"), took
    except Exception as ex:
        took = time.perf_counter() - started
        return False, _redact(f"funcdumper failed: {ex}")[:300], took

def _run_funcdumper2(name: str) -> tuple[bool, bytes | str, float]:
    """Generate 6Vms Game Structure Dumper injection script for Roblox executor."""
    started = time.perf_counter()
    try:
        script = funcdumper2.build_bytes()
        took = time.perf_counter() - started
        return True, script.encode("utf-8"), took
    except Exception as ex:
        took = time.perf_counter() - started
        return False, _redact(f"funcdumper2 failed: {ex}")[:300], took

def _run_simplespy(name: str) -> tuple[bool, bytes | str, float]:
    """Generate 6Vms SimpleSpy (RemoteSpy) injection script + tutorial."""
    started = time.perf_counter()
    try:
        script = simplespy.build_bytes()
        took = time.perf_counter() - started
        return True, script.encode("utf-8"), took
    except Exception as ex:
        took = time.perf_counter() - started
        return False, _redact(f"simplespy failed: {ex}")[:300], took

def _run_pdump(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    """Generate LPH V3 optimized string dumper (pdump) for Roblox executor."""
    started = time.perf_counter()
    tmp_in  = TMP / f"__pdump_in_{_stamp()}.lua"
    tmp_out = TMP / f"__pdump_out_{_stamp()}.lua"
    try:
        tmp_in.write_bytes(src_bytes)
        pdump.build(str(tmp_in), str(tmp_out), key="")
        result = tmp_out.read_bytes()
        took = time.perf_counter() - started
        return True, result, took
    except Exception as ex:
        took = time.perf_counter() - started
        return False, _redact(f"pdump (lphv3) failed: {ex}")[:300], took
    finally:
        for p in (tmp_in, tmp_out):
            try: p.unlink()
            except OSError: pass

def _run_luraph_dumper(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    """
    25ms Luraph Dumper — runs luraph_dumper.luau via Lune.
    luraph_dumper.luau reads from dumps/original/<stem>.lua and
    writes to dumps/dumped/<stem>.lua   (Lua's gsub strips the .lua suffix).
    """
    stamp    = _stamp()
    stem     = f"{stamp}_ldump"
    in_name  = f"{stem}.lua"
    in_path  = ROOT / "dumps" / "original" / in_name
    out_path = ROOT / "dumps" / "dumped" / in_name
    (ROOT / "dumps" / "original").mkdir(parents=True, exist_ok=True)
    (ROOT / "dumps" / "dumped").mkdir(parents=True, exist_ok=True)
    TMP.mkdir(exist_ok=True)

    # Lines printed to stdout by luraph_dumper.luau that are noise, not output
    _BRANDING = re.compile(
        r"(?:starting the dummppP|Made by 25ms|discord\.gg/25ms|success in|"
        r"evaluating in|reduced from|lol you didnt|lol that file)",
        re.IGNORECASE,
    )

    try:
        in_path.write_bytes(src_bytes)
        lune_bin = str(LUNE) if LUNE.exists() else "lune"
        ok, log, took = _run_proc([lune_bin, "run", "luraph_dumper.luau", in_name])

        if out_path.exists() and out_path.stat().st_size > 0:
            data = out_path.read_bytes()
            text = data.decode("utf-8", errors="ignore").strip()
            if "Luarmor files are not supported" in text:
                return False, "Luarmor files are not supported", took
            if text.startswith("lol"):
                return False, text[:300], took
            error_comment_pat = re.compile(
                r"\n?--\s*The script errored here\..*",
                re.DOTALL | re.IGNORECASE,
            )
            text_clean = error_comment_pat.sub("", text).strip()
            meaningful = [l for l in text_clean.splitlines()
                          if l.strip() and l.strip() != "[Stack End]"
                          and not _BRANDING.search(l)]
            if not meaningful:
                m = re.search(r"Error:\s*(.+?)(?:\n|$|--\]\])", text, re.IGNORECASE)
                reason = m.group(1).strip() if m else "no strings extracted — unsupported obfuscator or empty script"
                if reason in ("[Stack End]", "[StackEnd]"):
                    reason = "script threw `[Stack End]` — anti-tamper or unsupported obfuscator triggered"
                return False, reason[:300], took
            return True, text_clean.encode("utf-8"), took

        # Output file missing — surface the most useful line from stdout
        tail = (log or "").strip().splitlines()
        err_lines = [
            l.strip() for l in tail
            if l.strip()
            and l.strip() not in ("[Stack End]", "[StackEnd]")
            and not _BRANDING.search(l)
            and not _LUNE_META_RE.match(l.strip())
        ]
        if not err_lines:
            return False, "script threw [Stack End] — anti-tamper or unsupported obfuscator triggered", took
        msg = err_lines[-1][:300]
        return False, msg, took
    finally:
        for p in (in_path, out_path):
            try: p.unlink()
            except OSError: pass

# ── IronVeil Deobfuscator (Node.js) ─────────────────────────────────────────
def _run_ironveil(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    if not IRONVEIL_DIR.exists():
        return False, "IronVeil deobfuscator not found", 0.0
    stamp = _stamp()
    TMP.mkdir(exist_ok=True)
    in_path  = TMP / f"{stamp}_iv_in.lua"
    out_path = TMP / f"{stamp}_iv_out.lua"
    top = time.time()
    try:
        in_path.write_bytes(src_bytes)
        ok, log, took = _run_proc(
            [str(_NODE), str(IRONVEIL_DIR / "index.js"), str(in_path), str(out_path)]
        )
        if out_path.exists() and out_path.stat().st_size > 0:
            data = out_path.read_bytes()
            if data.strip():
                return True, data, took
        clean = re.sub(r"\x1b\[[0-9;]*m", "", log or "")
        lines = [l for l in clean.splitlines() if l.strip() and "Deobfuscation failed" not in l]
        msg = lines[-1] if lines else "not an IronVeil-obfuscated script"
        return False, msg[:300], took
    except Exception as ex:
        return False, _redact(f"ironveil error: {ex}"), time.time() - top
    finally:
        for p in (in_path, out_path):
            try: p.unlink()
            except OSError: pass

def _run_77fuscator(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    """Deobfuscate a 77fuscator 0.6.1-protected script via the leaked .NET deobfuscator.

    Returns the textual recovery report (proto tree + lift disassembly). The
    recovered Lua 5.1 binary chunk is also emitted to a temp .luac file.
    """
    if not SEVENSEVEN_EXE.exists():
        return False, "77fuscator deobfuscator not found — run: git clone https://github.com/Bytecoded1337/77fuscatorDeobfuscator 77fuscatorDeobfuscator && dotnet build", 0.0
    stamp = _stamp()
    TMP.mkdir(exist_ok=True)
    in_path  = TMP / f"{stamp}_77f_in.lua"
    out_path = TMP / f"{stamp}_77f_out.luac"
    top = time.time()
    try:
        in_path.write_bytes(src_bytes)
        ok, log, _ = _run_proc(
            [str(SEVENSEVEN_EXE), str(in_path), "-o", str(out_path), "--strings", "--dump-lifted"],
            timeout=300,
        )
        took = time.time() - top
        clean = re.sub(r"\x1b\[[0-9;]*m", "", log or "")
        if out_path.exists() and out_path.stat().st_size > 0:
            kernel = out_path.read_bytes()
            data_str = ("# 77fuscator 0.6.1 deobfuscation — recovered Lua 5.1 chunk\n"
                        + "# %d bytes written to .luac\n\n" % len(kernel)
                        + clean)
            return True, data_str, took
        lines = [l for l in clean.splitlines() if l.strip() and "error" not in l.lower()]
        msg = lines[-1] if lines else "not a 77fuscator-obfuscated script"
        return False, msg[:300], took
    except Exception as ex:
        return False, _redact(f"77fuscator error: {ex}"), time.time() - top
    finally:
        for p in (in_path, out_path):
            try: p.unlink()
            except OSError: pass

def _build_runtime_logger(src_bytes: bytes) -> bytes:
    """Build the executor runtime VM dump logger, hex-embedding the input source.

    The logger runs inside a Roblox executor: it loads the target, lets the VM
    materialize, then snapshots protos/strings/constants/relations from memory.
    Works on ANY Luraph version/build (live runtime data, not static parsing).
    """
    try:
        tmpl = LURAPH_LOGGER.read_text(encoding="utf-8")
        start = tmpl.index('_LPH_SRC = "') + len('_LPH_SRC = "')
        end = tmpl.index('"', start)
        built = tmpl[:start] + src_bytes.hex() + tmpl[end:]
        built = ("-- 6Vms LuraphDump v9 — runtime VM logger (devirtualizes ANY Luraph version)\n"
                 "-- way better than `.lphv5` (full VM extraction, not just strings)\n"
                 "-- inject + run in any Roblox executor, then reply with the dumps.\n"
                 + built)
        return built.encode("utf-8")
    except Exception as ex:
        return ("-- 6Vms LuraphDump logger build failed: %s\n" % _redact(str(ex))).encode("utf-8")


def _run_luraph_dumpdevirt(proto_bytes: bytes, strings_bytes: bytes, name: str):
    """Devirtualize a runtime logger dump pair (protos.tsv + strings.txt).

    Returns (ok, payload, took); payload has files + best (pseudo source).
    """
    top = time.time()
    stamp = _stamp()
    p_path = TMP / f"{stamp}_ddv_protos.tsv"
    s_path = TMP / f"{stamp}_ddv_strings.txt"
    base    = TMP / f"{stamp}_ddv"
    try:
        p_path.write_bytes(proto_bytes)
        s_path.write_bytes(strings_bytes)
        cmd = [
            sys.executable, "-m", "luauvmp", "luraph-dumpdevirt",
            str(p_path), str(s_path), "-o", str(base),
        ]
        ok, log, took = _run_proc(cmd, cwd=ROOT, timeout=300)
        files = []
        for suf in (".devirt.dis", ".pseudo.lua", ".flow.lua"):
            f = TMP / (base.name + suf)
            if f.exists() and f.stat().st_size > 0:
                files.append({"name": "runtime_devirt" + suf, "bytes": f.read_bytes()})
        if not files:
            return False, (log or "no runtime dump artifacts recovered")[:300], time.time() - top
        best = "runtime_devirt.pseudo.lua" if any(f["name"] == "runtime_devirt.pseudo.lua" for f in files) else files[0]["name"]
        return True, {"files": files, "best": best, "partial": False, "log": log}, time.time() - top
    except Exception as ex:
        return False, _redact(f"luraph-dumpdevirt error: {ex}")[:300], time.time() - top
    finally:
        for p in (p_path, s_path):
            try: p.unlink()
            except OSError: pass
        for suf in (".devirt.dis", ".pseudo.lua", ".flow.lua"):
            try: (TMP / (base.name + suf)).unlink()
            except OSError: pass


def _run_luraph_vmp(src_bytes: bytes, name: str):
    """Forced Luau VMP / Luraph devirtualizer (luau-vmp-deobf) — never gives up.

    Runs the full pipeline AND fallback attempts (generic deobf, legacy luraph
    unpack, generic unpack) so every run returns output + logs — even when the
    version/signature doesn't match. Returns (ok, payload, took) where payload:
        files:   list of {"name", "bytes"} for every captured artifact
        log:     full engine stdout/stderr from every attempt
        best:    preferred filename for pastefy
        partial: True when the primary pipeline exited non-zero
    """
    if not LURAPH_VMP_DIR.exists():
        return False, "luau-vmp-deobf not found — run: git clone https://github.com/binxgtl/luau-vmp-deobf.git luau-vmp-deobf", 0.0
    if not LUNE.exists():
        return False, "lune.exe not found — luau-vmp-deobf needs lune for safe capture", 0.0
    stamp = _stamp()
    TMP.mkdir(exist_ok=True)
    in_path = TMP / f"{stamp}_lvmp_in.lua"
    out_dir = TMP / f"{stamp}_lvmp_out"
    base    = TMP / f"{stamp}_lvmp"
    top = time.time()

    failed_notes = []

    def _grab(dir_path, prefix: str):
        """Collect files from a directory, or globbed files sharing a prefix."""
        found = []
        if dir_path and os.path.isdir(str(dir_path)):
            for p in sorted(pathlib.Path(str(dir_path)).rglob("*")):
                if p.is_file():
                    if p.name.upper() == "FAILED.TXT":
                        try:
                            failed_notes.append(p.read_text(encoding="utf-8", errors="replace"))
                        except OSError:
                            pass
                        continue
                    try:
                        found.append({
                            "name": str(p.relative_to(dir_path)).replace("\\", "/"),
                            "bytes": p.read_bytes(),
                        })
                    except OSError:
                        pass
        if prefix:
            for p in sorted(TMP.glob(prefix + "*")):
                if p.is_file():
                    try:
                        found.append({
                            "name": str(p.relative_to(TMP)).replace("\\", "/"),
                            "bytes": p.read_bytes(),
                        })
                    except OSError:
                        pass
        return found

    def _merge(existing, new):
        seen = {f["name"] for f in existing}
        for f in new:
            if f["name"] not in seen:
                existing.append(f)
                seen.add(f["name"])
        return existing

    try:
        in_path.write_bytes(src_bytes)
        logs = []
        files = []
        partial = False

        # ── attempt A: full Luraph pipeline (keeps partial artifacts) ──────
        cmd_a = [
            sys.executable, "-m", "luauvmp", "luraph-full",
            str(in_path), "-o", str(out_dir),
            "--runtime", '"%s"' % str(LUNE),
            "--no-lua-expert", "--force", "--timeout", "600",
            "--keep-failed",
        ]
        ok_a, log_a, _t_a = _run_proc(cmd_a, cwd=ROOT, timeout=900)
        logs.append(("luraph-full", log_a or "", _t_a))
        if not ok_a:
            partial = True
        # success renames the stage to out_dir; keep-failed failures stay as .partial-*
        stage_dirs = []
        if out_dir.exists():
            stage_dirs.append(out_dir)
        stage_dirs += sorted(TMP.glob(out_dir.name + ".partial-*"))
        for d in stage_dirs:
            files = _merge(files, _grab(d, None))

        # ── fallbacks: force generic decompile / unpack when nothing useful ─
        has_src = any(
            f["name"].endswith((".deobf.lua", "program.decompiled.luau", "embedded_main.luau", "program.pseudo.lua"))
            for f in files
        )
        if not has_src:
            cmd_b = [
                sys.executable, "-m", "luauvmp", "deobf",
                str(in_path), "-o", str(base),
                "--disasm", "--strings", "--spec",
            ]
            ok_b, log_b, _t_b = _run_proc(cmd_b, cwd=ROOT, timeout=180)
            logs.append(("deobf", log_b or "", _t_b))
            if not ok_b:
                partial = True
            files = _merge(files, _grab(None, f"{base.name}."))

            cmd_c = [
                sys.executable, "-m", "luauvmp", "luraph",
                str(in_path), "-o", str(base),
            ]
            ok_c, log_c, _t_c = _run_proc(cmd_c, cwd=ROOT, timeout=180)
            logs.append(("luraph-unpack", log_c or "", _t_c))
            if not ok_c:
                partial = True
            files = _merge(files, _grab(None, f"{base.name}."))

            cmd_d = [
                sys.executable, "-m", "luauvmp", "unpack",
                str(in_path), "-o", str(base),
            ]
            ok_d, log_d, _t_d = _run_proc(cmd_d, cwd=ROOT, timeout=180)
            logs.append(("unpack", log_d or "", _t_d))
            if not ok_d:
                partial = True
            files = _merge(files, _grab(None, f"{base.name}."))

        # ── last resort: force a beautify/devirtualize pass on the input ────
        # Even when nothing matched, always ship a readable, transformed file.
        has_src = any(
            f["name"].endswith((".deobf.lua", "program.decompiled.luau", "embedded_main.luau", "program.pseudo.lua", "forced.beautified.lua"))
            for f in files
        )
        if not has_src and DARKLUA.exists():
            ok_bf, out_bf, _t_bf = _run_darklua(
                src_bytes,
                ["compute_expression", "convert_index_to_field", "remove_comments"],
                "readable", True,
            )
            if ok_bf and out_bf:
                files = _merge(files, [{"name": "forced.beautified.lua", "bytes": out_bf if isinstance(out_bf, bytes) else out_bf.encode()}])
                logs.append(("forced-beautify", "darklua beautify pass wrote forced.beautified.lua", _t_bf))
            else:
                logs.append(("forced-beautify", str(out_bf or "darklua beautify failed"), _t_bf))
                partial = True
        else:
            logs.append(("forced-beautify", "skipped — recovered source already present", 0.0))

        # ── always include the original input so nothing is lost ───────────
        files = _merge(files, [{"name": "original.lua", "bytes": src_bytes}])

        # ── pick the best recoverable source for pastefy ────────────────────
        best = None
        for cand in ("embedded_main.luau", "program.decompiled.luau"):
            if any(f["name"] == cand for f in files):
                best = cand
                break
        if best is None:
            for f in files:
                if f["name"].endswith(".deobf.lua"):
                    best = f["name"]
                    break
        if best is None:
            for f in files:
                if f["name"] == "forced.beautified.lua":
                    best = f["name"]
                    break
        if best is None and any(f["name"] == "program.pseudo.lua" for f in files):
            best = "program.pseudo.lua"
        best = best or "original.lua"

        full_log = []
        for label, text, dur in logs:
            clean = re.sub(r"\x1b\[[0-9;]*m", "", text or "").strip()
            full_log.append(f"===== {label} ({dur:.1f}s) =====")
            full_log.append(clean if clean else "(no output)")
        full_log_text = "\n".join(full_log)

        payload = {
            "files": files,
            "log": full_log_text,
            "best": best,
            "partial": partial,
        }
        if partial:
            payload["logger"] = _build_runtime_logger(src_bytes)
        failed_note = "\n".join(failed_notes).strip()
        if failed_note:
            payload["failed_note"] = failed_note

        return True, payload, time.time() - top
    except Exception as ex:
        return False, _redact(f"luauvmp error: {ex}"), time.time() - top
    finally:
        try: in_path.unlink()
        except OSError: pass
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        for pd in TMP.glob(out_dir.name + ".partial-*"):
            shutil.rmtree(pd, ignore_errors=True)
        for p in TMP.glob(f"{base.name}.*"):
            try: p.unlink()
            except OSError: pass

def _luraph_consts_counts(text: str) -> dict:
    """Count entries in a rendered luraph-constants.lua for the summary embed."""
    protos = strings = numbers = imports = closures = 0
    for line in text.splitlines():
        s = line.strip()
        if s == "strings = {":
            strings += 1
        elif s == "numbers = {":
            numbers += 1
        elif s == "imports = {":
            imports += 1
        elif s == "closures = {":
            closures += 1
        elif re.match(r"^\[\d+\] = \{$", s):
            protos += 1
    return {
        "protos": protos,
        "strings": strings,
        "numbers": numbers,
        "imports": imports,
        "closures": closures,
    }


def _run_luraph_constants(src_bytes: bytes, name: str):
    """Lucidity Luraph constants dumper (6Vms layout) via luau-vmp-deobf.

    Runs unpack -> safe capture -> per-proto constant extraction and returns
    (ok, payload, took) where payload is the rendered luraph-constants.lua text.
    """
    if not LURAPH_VMP_DIR.exists():
        return False, "luau-vmp-deobf not found — run: git clone https://github.com/binxgtl/luau-vmp-deobf.git luau-vmp-deobf", 0.0
    if not LUNE.exists():
        return False, "lune.exe not found — luau-vmp-deobf needs lune for safe capture", 0.0
    stamp = _stamp()
    TMP.mkdir(exist_ok=True)
    in_path = TMP / f"{stamp}_lrc_in.lua"
    out_dir = TMP / f"{stamp}_lrc_out"
    top = time.time()
    try:
        in_path.write_bytes(src_bytes)
        cmd = [
            sys.executable, "-m", "luauvmp", "luraph-constants",
            str(in_path), "-o", str(out_dir),
            "--runtime", '"%s"' % str(LUNE),
            "--force", "--timeout", "300",
            "--keep-failed",
        ]
        proc = subprocess.Popen(
            cmd, cwd=str(ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        try:
            full_log, _ = proc.communicate(timeout=600)
        except subprocess.TimeoutExpired:
            _kill_tree(proc.pid)
            full_log = "timeout"
            proc.returncode = -1
        full_log = (full_log or "").strip()
        if proc.returncode != 0:
            lines = [l.strip() for l in full_log.splitlines() if l.strip()]
            err = next((l for l in lines if re.search(r"(failed|expected|error:|Exception|Traceback|Unsupported)", l, re.I)), None)
            return False, (err or (lines[-1] if lines else "luraph-constants failed"))[:600], time.time() - top
        target = out_dir / "luraph-constants.lua"
        if not target.exists():
            partials = sorted(TMP.glob(out_dir.name + ".partial-*"))
            print("lrc partial dirs:", [str(p) for p in partials])
            return False, "capture produced no luraph-constants.lua — loader not supported statically", time.time() - top
        text = target.read_text(encoding="utf-8", errors="surrogateescape")
        if not text.strip():
            return False, "luraph-constants.lua was empty — no constants recovered", time.time() - top
        return True, text, time.time() - top
    except Exception as ex:
        return False, _redact(f"luraph-constants error: {ex}")[:600], time.time() - top
    finally:
        try: in_path.unlink()
        except OSError: pass
        for d in ([out_dir] + sorted(TMP.glob(out_dir.name + ".partial-*"))):
            if pathlib.Path(d).exists():
                shutil.rmtree(d, ignore_errors=True)

def _run_25ms(src_bytes: bytes, name: str, extra_args: list[str] | None = None) -> tuple[bool, bytes | str, float]:
    """
    25ms Dumper — runs the source through main.luau (same engine as .l/.unveilr)
    with a fixed set of settings tuned for the 25ms checkpoint style output.
    """
    stamp   = _stamp()
    TMP.mkdir(exist_ok=True)
    in_rel  = f"bot_tmp/{stamp}_25ms_in.lua"
    out_rel = f"bot_tmp/{stamp}_25ms_out.lua"
    in_path  = ROOT / in_rel
    out_path = ROOT / out_rel
    lune_bin = str(LUNE) if LUNE.exists() else "lune"
    try:
        in_path.write_bytes(src_bytes)
        cmd = [lune_bin, "run", "main.luau", in_rel, f"out={out_rel}", "version=1"]
        if extra_args:
            cmd.extend(extra_args)
        ok, log, took = _run_proc(cmd, cwd=ROOT)
        if out_path.exists() and out_path.stat().st_size > 0:
            text = out_path.read_text(errors="ignore").strip()
            if text.startswith("--err"):
                return False, text[5:].strip()[:300], took
            return True, text.encode("utf-8"), took
        # no output file — surface a clean error from the log
        tail = [l.strip() for l in (log or "").splitlines()
                if l.strip() and not l.strip().startswith("[")]
        msg = (tail[-1] if tail else "25ms dumper produced no output")[:300]
        return False, msg, took
    finally:
        for p in (in_path, out_path):
            try: p.unlink()
            except OSError: pass

def _run_unveilr(src_bytes: bytes, name: str, extra_args: list[str] | None = None) -> tuple[bool, bytes | str, float]:
    """
    UnveilR engine — runs through root main.luau with version=1 flag.
    """
    stamp = _stamp()
    # Use relative-to-ROOT paths so lune (cwd=ROOT) can resolve them
    TMP.mkdir(exist_ok=True)
    in_rel  = f"bot_tmp/{stamp}_unveilr_in.lua"
    out_rel = f"bot_tmp/{stamp}_unveilr_out.lua"
    in_path  = ROOT / in_rel
    out_path = ROOT / out_rel
    lune_bin = str(LUNE) if LUNE.exists() else "lune"

    try:
        in_path.write_bytes(src_bytes)
        cmd = [lune_bin, "run", "main.luau", in_rel, f"out={out_rel}", "version=1"]
        if extra_args:
            cmd.extend(extra_args)
        ok, log, took = _run_proc(cmd, cwd=ROOT)
        if out_path.exists() and out_path.stat().st_size > 0:
            text = out_path.read_text(errors="ignore")
            if text.lstrip().startswith("--err"):
                reason = text.lstrip()[5:].strip()
                return False, reason[:300] or "unveilr error", took
            return True, text.encode("utf-8"), took
        tail = (log or "").strip().splitlines()
        lines = [l.strip() for l in tail if l.strip() and not l.startswith("[")]
        msg = (lines[-1] if lines else tail[-1] if tail else "unveilr produced no output")[:300]
        return False, msg, took
    finally:
        for p in (in_path, out_path):
            try: p.unlink()
            except OSError: pass

def _run_prometheus(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    """Prometheus decryption engine — string/constant extraction."""
    stamp = _stamp()
    in_name  = f"{stamp}_prom_in.lua"
    out_name = f"{stamp}_prom_in_deobf.lua"   # pol.py naming convention
    in_path  = PROM_DIR / in_name
    out_path = PROM_DIR / out_name
    try:
        in_path.write_bytes(src_bytes)
        # pol.py runs in PROM_DIR; command: python pol.py input_filename
        ok, log, took = _run_proc(
            ["python", "pol.py", in_name], cwd=PROM_DIR
        )
        if out_path.exists() and out_path.stat().st_size > 0:
            return True, out_path.read_bytes(), took
        # pol.py exits 0 even on some failures — check output size
        tail = (log or "").strip().splitlines()
        msg = (tail[-1] if tail else "prometheus produced no output")[:300]
        return False, msg, took
    finally:
        for p in (in_path, out_path):
            try: p.unlink()
            except OSError: pass

def _run_prometheus_v2(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    """Prometheus V2 decryption engine — updated pipeline with enhanced constant recovery."""
    stamp = _stamp()
    TMP.mkdir(exist_ok=True)
    in_path  = TMP / f"{stamp}_promv2_in.lua"
    out_path = TMP / f"{stamp}_promv2_out.lua"
    try:
        in_path.write_bytes(src_bytes)
        # Prefer bundled luajit.exe (sits next to bot.py), fall back to PATH
        if LUAJIT.exists():
            lua_cmd = str(LUAJIT)
        else:
            lua_cmd = None
            for candidate in ["luajit", "lua5.1", "lua51", "lua"]:
                if shutil.which(candidate):
                    lua_cmd = candidate
                    break

        if not lua_cmd:
            return False, "luajit.exe not found — copy luajit.exe into the bot folder or install LuaJIT", 0.0

        cli_path = PROM_V2_DIR / "src" / "deob" / "cli.lua"
        if not cli_path.exists():
            return False, (
                f"prometheus-v2 not found at {PROM_V2_DIR} — "
                "run: git clone https://github.com/0x251/Prometheus-DeobfuscatorV2.git prometheus-v2"
            ), 0.0

        # Run from PROM_V2_DIR so add_path()'s relative package.path resolves correctly
        ok, log, took = _run_proc(
            [lua_cmd, str(cli_path), str(in_path),
             "--out", str(out_path), "--static-only"],
            cwd=PROM_V2_DIR,
        )
        if ok and out_path.exists():
            data = out_path.read_bytes()
            if data.strip():
                return True, data, took
            return False, "v2 decryption produced empty output", took
        tail = (log or "").strip().splitlines()
        return False, (tail[-1] if tail else "prometheus v2 failed")[:300], took
    finally:
        for p in (in_path, out_path):
            try: p.unlink()
            except OSError: pass

def _run_lph(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    stamp = _stamp()
    TMP.mkdir(exist_ok=True)
    in_path  = TMP / f"{stamp}_lph_in.lua"
    out_path = TMP / f"{stamp}_lph_out.lua"
    try:
        in_path.write_bytes(src_bytes)
        ok, log, took = _run_proc(
            [sys.executable, str(LPH2_PY), "decompile", str(in_path), "--output", str(out_path)]
        )
        if ok and out_path.exists():
            data = out_path.read_bytes()
            if data.strip():
                return True, data, took
            return False, "deobfuscator produced empty output", took
        tail = (log or "").strip().splitlines()
        return False, (tail[-1] if tail else "lph failed")[:300], took
    finally:
        for p in (in_path, out_path):
            try: p.unlink()
            except OSError: pass

def _run_luraph(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    stamp = _stamp()
    TMP.mkdir(exist_ok=True)
    in_path  = TMP / f"{stamp}_lph_in.lua"
    out_path = TMP / f"{stamp}_lph_out.lua"
    try:
        in_path.write_bytes(src_bytes)
        ok, log, took = _run_proc(
            [sys.executable, str(LPH2_PY), "decompile", str(in_path), "--output", str(out_path)]
        )
        if ok and out_path.exists():
            data = out_path.read_bytes()
            if data.strip():
                return True, data, took
            return False, "deobfuscator produced empty output", took
        tail = (log or "").strip().splitlines()
        return False, (tail[-1] if tail else "luraph failed")[:300], took
    finally:
        for p in (in_path, out_path):
            try: p.unlink()
            except OSError: pass

def _run_ib2(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    stamp = _stamp()
    in_name  = f"{stamp}_ib2_in.lua"
    out_name = f"{stamp}_ib2_out.lua"
    in_path  = IB2_DIR / in_name
    out_path = IB2_DIR / out_name
    try:
        in_path.write_bytes(src_bytes)
        ok, log, took = _run_proc(
            [str(IB2_EXE), in_name, out_name], cwd=IB2_DIR
        )
        if ok and out_path.exists() and out_path.stat().st_size > 0:
            return True, out_path.read_bytes(), took
        tail = (log or "").strip().splitlines()
        return False, (tail[-1] if tail else "ib2 produced no output")[:300], took
    finally:
        for p in (in_path, out_path):
            try: p.unlink()
            except OSError: pass

def _run_moonsec(src_bytes: bytes, name: str, mode: str = "dev") -> tuple[bool, bytes | str, float]:
    """
    MoonSec Disassembler — devirtualizes MoonSec V2/V3 protected scripts.
    mode='dev'  → full Lua source via devirtualization  (-dev flag)
    mode='dis'  → bytecode disassembly                  (-dis flag)
    """
    if not MOONSEC_EXE.exists():
        return False, (
            f"MoonsecDeobfuscator not found at {MOONSEC_EXE} — "
            "run: git clone https://github.com/tupsutumppu/MoonsecDeobfuscator.git "
            "then: cd MoonsecDeobfuscator && dotnet build -c Release"
        ), 0.0
    stamp    = _stamp()
    TMP.mkdir(exist_ok=True)
    in_path  = TMP / f"{stamp}_moonsec_in.lua"
    out_path = TMP / f"{stamp}_moonsec_out.lua"
    flag     = "-dev" if mode == "dev" else "-dis"
    try:
        in_path.write_bytes(src_bytes)
        ok, log, took = _run_proc(
            [str(MOONSEC_EXE), flag, "-i", str(in_path), "-o", str(out_path)]
        )
        if ok and out_path.exists() and out_path.stat().st_size > 0:
            return True, out_path.read_bytes(), took
        # exe may exit 0 but write nothing on unsupported input
        tail = (log or "").strip().splitlines()
        return False, (tail[-1] if tail else "MoonsecDeobfuscator produced no output")[:300], took
    finally:
        for p in (in_path, out_path):
            try: p.unlink()
            except OSError: pass

def _run_moonsec_dev(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    return _run_moonsec(src_bytes, name, mode="dev")

def _run_moonsec_dis(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    return _run_moonsec(src_bytes, name, mode="dis")

# ── Universal VM interpreter ──────────────────────────────────────────────────
def _run_universal_vm(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    """Deobfuscate via Revea, then sandbox-execute the cleaned code."""
    stamp = _stamp()
    TMP.mkdir(exist_ok=True)
    lune_bin = str(LUNE) if LUNE.exists() else "lune"

    # Step 1 — deobfuscate through main.luau (Revea) with version=1
    in_rel1  = f"bot_tmp/{stamp}_uvm_in.lua"
    out_rel1 = f"bot_tmp/{stamp}_uvm_deobf.lua"
    in_path1  = ROOT / in_rel1
    out_path1 = ROOT / out_rel1
    total_took = 0.0
    try:
        in_path1.write_bytes(src_bytes)
        ok, log, took = _run_proc(
            [lune_bin, "run", "main.luau", in_rel1, f"out={out_rel1}", "version=1"], cwd=ROOT
        )
        total_took += took
        if out_path1.exists() and out_path1.stat().st_size > 0:
            text = out_path1.read_text(errors="ignore")
            if not text.lstrip().startswith("--err"):
                src_bytes = text.encode("utf-8")  # use deobfuscated code
    finally:
        for p in (in_path1, out_path1):
            try: p.unlink()
            except OSError: pass

    # Step 2 — run through universal_vm sandbox
    in_rel2  = f"bot_tmp/{stamp}_uvm_sbox.lua"
    out_rel2 = f"bot_tmp/{stamp}_uvm_result.lua"
    in_path2  = ROOT / in_rel2
    out_path2 = ROOT / out_rel2
    try:
        in_path2.write_bytes(src_bytes)
        ok, log, took = _run_proc(
            [lune_bin, "run", "universal_vm.luau", in_rel2, out_rel2], cwd=ROOT
        )
        total_took += took
        if out_path2.exists() and out_path2.stat().st_size > 0:
            text = out_path2.read_text(errors="ignore")
            return True, text.encode("utf-8"), total_took
        tail = (log or "").strip().splitlines()
        return False, (tail[-1] if tail else "universal vm produced no output")[:300], total_took
    finally:
        for p in (in_path2, out_path2):
            try: p.unlink()
            except OSError: pass

# ── Luau Decompiler (Unluau) ──────────────────────────────────────────────────
_UNLUAU_CLI = ROOT / "unluau" / "win-x64" / "Unluau.CLI.exe"
UNLUAC_JAR  = ROOT / "unluac.jar"

def _run_lunaux(
    src_bytes: bytes,
    name: str,
) -> tuple[bool, bytes | str, float]:
    """Decompile Luau bytecode via Unluau.CLI (.NET 9 required)."""
    cli = _UNLUAU_CLI
    if not cli.exists():
        return False, "Unluau decompiler not found — re-run start.bat to download dependencies", 0.0
    is_source = not src_bytes[:64].decode("utf-8", errors="replace").strip("\x00").startswith("\x00")
    if is_source:
        return False, "Input looks like source code. Provide compiled Luau bytecode instead.", 0.0
    stamp = _stamp()
    TMP.mkdir(exist_ok=True)
    top = time.time()
    in_dir = TMP / stamp
    in_dir.mkdir(exist_ok=True)
    try:
        bc_path = in_dir / "bytecode.bin"
        bc_path.write_bytes(src_bytes)
        out_path = in_dir / "decompiled.luau"
        ok, log, _ = _run_proc(
            [str(cli), str(bc_path), "--output", str(out_path)], cwd=ROOT
        )
        took = time.time() - top
        if out_path.exists() and out_path.stat().st_size > 0:
            result = out_path.read_text(errors="replace")
            if result.strip():
                return True, result.encode("utf-8"), took
        lines = (log or "").strip().splitlines()
        msg = lines[-1] if lines else "Unluau produced no output"
        msg = msg.split("Unluau.")[-1] if "Unluau." in msg else msg
        return False, msg[:200], took
    except Exception as ex:
        return False, _redact(f"Unluau error: {ex}"), time.time() - top
    finally:
        import shutil
        shutil.rmtree(in_dir, ignore_errors=True)


def _run_unluac(
    src_bytes: bytes,
    name: str,
) -> tuple[bool, bytes | str, float]:
    """Decompile Lua 5.1 bytecode via unluac (Java)."""
    jar = UNLUAC_JAR
    if not jar.exists():
        return False, "unluac.jar not found — place it in the bot root directory", 0.0
    is_source = not src_bytes[:64].decode("utf-8", errors="replace").strip("\x00").startswith("\x1b")
    if is_source:
        return False, "Input looks like source code. Provide compiled Lua 5.1 bytecode (.luac) instead.", 0.0
    stamp = _stamp()
    TMP.mkdir(exist_ok=True)
    top = time.time()
    in_dir = TMP / stamp
    in_dir.mkdir(exist_ok=True)
    try:
        bc_path = in_dir / "bytecode.luac"
        bc_path.write_bytes(src_bytes)
        out_path = in_dir / "decompiled.lua"
        ok, log, took_raw = _run_proc(
            ["java", "-jar", str(jar), str(bc_path)], cwd=ROOT
        )
        took = time.time() - top
        if ok and log.strip():
            out_path.write_text(log, encoding="utf-8")
            return True, log.encode("utf-8"), took
        lines = (log or "").strip().splitlines()
        msg = lines[-1] if lines else "unluac produced no output"
        return False, msg[:200], took
    except FileNotFoundError:
        return False, "Java is not installed. Install Java Runtime (JRE) to use unluac.", time.time() - top
    except Exception as ex:
        return False, _redact(f"unluac error: {ex}"), time.time() - top
    finally:
        import shutil
        shutil.rmtree(in_dir, ignore_errors=True)

# ── Discord panel ─────────────────────────────────────────────────────────────
TOOLS = {
    "luraph_strings": ("Luraph String Dumper", "v14.7 injection script for Roblox executors", "📋", _run_luraph_strings),
    "lphv2":          ("LPH V2",               "v2 Luraph string dumper injection script",     "📋", _run_luraph_v2),
    "lphv5":          ("LPH V5",               "6Vms Luraph 14.7/14.8 decryptor",              "🔓", _run_luraph_v5),
    "luarmor":        ("Luarmor",               "6Vms Luarmor v1/v2/v3 HTTP logger",            "📡", _run_luarmor),
    "funcdumper":     ("Function Dumper",        "6Vms Function Dumper (xAPI fork) executor script", "🧬", _run_funcdumper),
    "simplespy":      ("SimpleSpy",              "6Vms SimpleSpy RemoteSpy + tutorial",         "🕵️", _run_simplespy),
    "lphv3":          ("LPH V3",               "v3 optimized string dumper (pdump)",           "📋", _run_pdump),
    "luraph_dumper":  ("Luraph Dumper",         "25ms string constant extractor via Lune",      "🔎", _run_luraph_dumper),
    "25ms":           ("25ms Dumper",           "25ms full env dump (main.luau engine)",       "🟣", _run_25ms),
    "unveilr":        ("UnveilR",               "UnveilR environment logger (full dump)",       "🔍", _run_unveilr),
    "lph":    ("LPH",                          "Legacy Luraph (only supports older versions)", "💜", _run_lph),
    "luraph": ("Luraph",                       "LuraphDeobfuscator (only supports older versions)", "🔷", _run_luraph),
    "ib2":    ("IronBrew2",                    "IB2 .NET deobfuscator",                       "🔶", _run_ib2),
    "prom":   ("Prometheus/Moonsec Decryptor", "String & constant extraction pipeline",       "🔴", _run_prometheus_v2),
    "promv2": ("Prometheus/Moonsec V2",        "Enhanced decryption with constant recovery",  "🔴", _run_prometheus_v2),
    "moonsec":     ("MoonSec Disassembler",    "Devirtualize MoonSec V2/V3 → Lua source",    "🌙", _run_moonsec_dev),
    "moonsec_dis": ("MoonSec Disassembly",     "MoonSec V2/V3 → bytecode disassembly",       "🌙", _run_moonsec_dis),
    "universal_vm": ("Lua Interpreter (BETA)",   "Universal VM: loads & sandbox-executes any Lua/Luau code", "⚡", _run_universal_vm),
    "lunaux":       ("Luau Decompiler",          "Decompile Luau bytecode → readable source (via Unluau)", "🔧", _run_lunaux),
    "unluac":       ("unluac Decompiler",        "Decompile Lua 5.1 bytecode → source (Java)",             "☕", _run_unluac),
    "ironveil":     ("IronVeil Deobfuscator",    "Deobfuscate IronVeil-obfuscated scripts (Node.js)",      "🛡️", _run_ironveil),
    "77fuscator":   ("77fuscator Deobfuscator",  "Deobfuscate 77fuscator 0.6.1 scripts → Lua 5.1 .luac (.NET)", "7️⃣", _run_77fuscator),
}

class DeobfSelect(Select):
    def __init__(self, src_bytes: bytes, name: str, orig_msg: discord.Message):
        self.src_bytes = src_bytes
        self.name      = name
        self.orig_msg  = orig_msg
        options = [
            SelectOption(label=v[0], value=k, description=v[1], emoji=v[2])
            for k, v in TOOLS.items()
        ]
        super().__init__(placeholder="Choose a deobfuscator…", options=options,
                         min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        label, desc, emoji, runner = TOOLS[key]
        self.disabled = True
        for item in self.view.children:
            item.disabled = True

        e_proc = discord.Embed(color=ACCENT, timestamp=datetime.now())
        e_proc.description = (
            f"{emoji} **{label}** — processing `{self.name}`…\n"
            f"-# This may take up to {USER_TIMEOUT}s"
        )
        e_proc.set_footer(text="6Vms")
        await interaction.response.edit_message(embed=e_proc, view=self.view)

        asyncio.create_task(
            self._process(interaction, key, label, emoji, runner)
        )

    async def _process(self, interaction, key, label, emoji, runner):
        await react(self.orig_msg, "⏳")
        try:
            ok, result, took = await asyncio.to_thread(
                runner, self.src_bytes, self.name
            )
            if ok:
                data_bytes  = result if isinstance(result, bytes) else result.encode()
                data_str    = data_bytes.decode("utf-8", errors="ignore")
                data_str    = _stamp_output(data_str)
                data_bytes  = data_str.encode("utf-8")
                out_ext     = ".lua"
                out_fname   = _rand_name(f"{key}{out_ext}")
                raw_url     = await pastefy.upload(http, out_fname, data_str)
                display_name = re.sub(r"(\.(lua|txt|luau))+$", "", self.name, flags=re.I) or self.name
                e = discord.Embed(color=GOOD, timestamp=datetime.now())
                e.description = (
                    f"{emoji} **{label}** — `{display_name}`\n"
                    f"`{len(data_str.splitlines()):,} lines` · "
                    f"`{len(data_bytes)/1024:.1f} KB` · `{took:.2f}s`"
                )
                if raw_url: e.description += f"\n{raw_url}"
                # Tutorial for injection-based dumpers
                if key in ("luraph_strings", "lphv2", "lphv3"):
                    e.description += (
                        "\n\n**How to use:**\n"
                        "1. Execute the `.lua` file above in your Roblox executor\n"
                        "2. Wait for it to finish running\n"
                        "3. Check your executor's **workspace folder** for `luraph_strings_dump.txt`\n"
                        "4. That file contains all the dumped strings"
                    )
                e.set_footer(text="6Vms")
                with open(TMP / f"__send_{_stamp()}.lua", "wb") as fh:
                    fh.write(data_bytes)
                    fh.flush()
                    fpath = pathlib.Path(fh.name)
                try:
                    await interaction.followup.send(
                        content=self.orig_msg.author.mention,
                        embed=e,
                        file=discord.File(str(fpath), filename=out_fname),
                    )
                finally:
                    try: fpath.unlink()
                    except OSError: pass
                await unreact(self.orig_msg, "⏳"); await react(self.orig_msg, "✅")
            else:
                e = discord.Embed(color=BAD, timestamp=datetime.now())
                e.description = f"{emoji} **{label}** — `{self.name}`\n{result}"
                e.set_footer(text="6Vms")
                await interaction.followup.send(
                    content=self.orig_msg.author.mention, embed=e
                )
                await unreact(self.orig_msg, "⏳"); await react(self.orig_msg, "❌")
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"{emoji} **{label}** — `{self.name}`\nerror: {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            try: await interaction.followup.send(content=self.orig_msg.author.mention, embed=e)
            except Exception: pass
            await unreact(self.orig_msg, "⏳"); await react(self.orig_msg, "❌")

class DeobfPanel(View):
    def __init__(self, src_bytes: bytes, name: str, orig_msg: discord.Message):
        super().__init__(timeout=120)
        self.add_item(DeobfSelect(src_bytes, name, orig_msg))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except Exception:
            pass

# ── .lua analysis panel ────────────────────────────────────────────────────────

class LuaAnalysisSelect(Select):
    def __init__(self, src_bytes: bytes, name: str, orig_msg: discord.Message):
        self.src_bytes = src_bytes
        self.name      = name
        self.orig_msg  = orig_msg
        options = [
            SelectOption(label=v[0], value=k, description=v[1], emoji=v[2])
            for k, v in _LUA_TOOLS.items()
        ]
        super().__init__(placeholder="Choose an analysis tool…", options=options,
                         min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        label, desc, emoji, runner_key = _LUA_TOOLS[key]
        self.disabled = True
        for item in self.view.children:
            item.disabled = True

        e_proc = discord.Embed(color=ACCENT, timestamp=datetime.now())
        e_proc.description = f"{emoji} **{label}** — processing `{self.name}`…"
        e_proc.set_footer(text="6Vms")
        await interaction.response.edit_message(embed=e_proc, view=self.view)

        asyncio.create_task(self._process(interaction, key, label, emoji))

    async def _process(self, interaction, key, label, emoji):
        await react(self.orig_msg, "⏳")
        runner = _run_string_dumper if key == "strings" else _run_lua_tracer
        try:
            ok, result, took = await asyncio.to_thread(runner, self.src_bytes, self.name)
            if ok:
                data_str   = result.decode() if isinstance(result, bytes) else result
                data_bytes = data_str.encode()
                ext        = "strings" if key == "strings" else "trace"
                out_fname  = _rand_name(f"{ext}.txt")
                raw_url    = await pastefy.upload(http, out_fname, data_str)
                e = discord.Embed(color=GOOD, timestamp=datetime.now())
                e.description = (
                    f"{emoji} **{label}** — `{self.name}`\n"
                    f"`{data_str.count(chr(10))+1:,} lines` · `{len(data_bytes)/1024:.1f} KB` · `{took:.2f}s`"
                )
                if raw_url: e.description += f"\n{raw_url}"
                e.set_footer(text="6Vms")
                tmp = TMP / f"__lua_analysis_{_stamp()}.txt"
                tmp.write_bytes(data_bytes)
                try:
                    await self.orig_msg.reply(
                        content=self.orig_msg.author.mention, embed=e,
                        file=discord.File(str(tmp), filename=out_fname), mention_author=True,
                    )
                finally:
                    try: tmp.unlink()
                    except OSError: pass
                await unreact(self.orig_msg, "⏳"); await react(self.orig_msg, "✅")
            else:
                e = discord.Embed(color=BAD, timestamp=datetime.now())
                e.description = f"{emoji} **{label}** — `{self.name}`\n```\n{result.decode() if isinstance(result, bytes) else result}\n```"
                e.set_footer(text="6Vms")
                await self.orig_msg.reply(embed=e, mention_author=False)
                await unreact(self.orig_msg, "⏳"); await react(self.orig_msg, "❌")
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"{emoji} **{label}** — `{self.name}`\nerror: {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            try: await self.orig_msg.reply(embed=e, mention_author=False)
            except discord.HTTPException: pass
            await unreact(self.orig_msg, "⏳"); await react(self.orig_msg, "❌")

class LuaAnalysisPanel(View):
    def __init__(self, src_bytes: bytes, name: str, orig_msg: discord.Message):
        super().__init__(timeout=120)
        self.add_item(LuaAnalysisSelect(src_bytes, name, orig_msg))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except Exception:
            pass

# ── .cfg panel ───────────────────────────────────────────────────────────────

class CfgToggleButton(discord.ui.Button):
    def __init__(self, panel: "CfgPanel", key: str, row: int):
        self.panel = panel
        self.key   = key
        label, desc, default = _CFG_DEFS[key]
        enabled = panel.cfg[key]
        super().__init__(
            label=f"{'✅' if enabled else '❌'} {label}",
            style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        self.panel.cfg[self.key] = not self.panel.cfg[self.key]
        self.panel._rebuild()
        await interaction.response.edit_message(embed=self.panel.build_embed(), view=self.panel)

class CfgResetButton(discord.ui.Button):
    def __init__(self, panel: "CfgPanel"):
        self.panel = panel
        super().__init__(label="Reset Defaults", style=discord.ButtonStyle.danger, row=4)

    async def callback(self, interaction: discord.Interaction):
        self.panel.cfg = {k: v[2] for k, v in _CFG_DEFS.items()}
        self.panel.cfg["_runtime"] = "lune"
        self.panel.cfg["_timeout"] = 64
        self.panel._rebuild()
        await interaction.response.edit_message(embed=self.panel.build_embed(), view=self.panel)

class CfgSaveButton(discord.ui.Button):
    def __init__(self, panel: "CfgPanel"):
        self.panel = panel
        super().__init__(label="Save", style=discord.ButtonStyle.primary, row=4)

    async def callback(self, interaction: discord.Interaction):
        _cfg_store[self.panel.channel_id] = dict(self.panel.cfg)
        _save_cfg()
        for item in self.panel.children:
            item.disabled = True
        e = self.panel.build_embed()
        e.set_footer(text="6Vms · saved ✅")
        await interaction.response.edit_message(embed=e, view=self.panel)

class CfgRuntimeButton(discord.ui.Button):
    """Toggles the .l runtime — currently only LUNE is supported.
    lute.exe does not support lune's require('./mods/...') module system."""
    def __init__(self, panel: "CfgPanel"):
        self.panel = panel
        rt = panel.cfg.get("_runtime", "lune").upper()
        # Force back to lune if lute was somehow saved — lute breaks main.luau
        if rt == "LUTE":
            panel.cfg["_runtime"] = "lune"
            rt = "LUNE"
        super().__init__(
            label=f"Runtime (.l): {rt}  (lune only)",
            style=discord.ButtonStyle.secondary,
            row=3,
            disabled=True,  # greyed out — lute is not compatible with main.luau
        )

    async def callback(self, interaction: discord.Interaction):
        # Lute is incompatible with main.luau's require('./mods/...') — keep lune always
        self.panel.cfg["_runtime"] = "lune"
        self.panel._rebuild()
        await interaction.response.edit_message(embed=self.panel.build_embed(), view=self.panel)


class CfgTimeoutButton(discord.ui.Button):
    """Steps the per-channel processing timeout up or down."""
    _STEPS = [30, 45, 64, 90, 120, 180, 300, 600, 900, 1800]

    def __init__(self, panel: "CfgPanel", direction: int):
        self.panel     = panel
        self.direction = direction  # +1 or -1
        label = "⏱️ Timeout +" if direction > 0 else "⏱️ Timeout −"
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=3)

    async def callback(self, interaction: discord.Interaction):
        current = self.panel.cfg.get("_timeout", USER_TIMEOUT)
        steps   = self._STEPS
        # find nearest step index
        idx = min(range(len(steps)), key=lambda i: abs(steps[i] - current))
        idx = max(0, min(len(steps) - 1, idx + self.direction))
        self.panel.cfg["_timeout"] = steps[idx]
        self.panel._rebuild()
        await interaction.response.edit_message(embed=self.panel.build_embed(), view=self.panel)



class CfgPanel(discord.ui.View):
    # Lay out buttons in rows of 4 (max 5 rows × 5 buttons = 25 slots)
    _KEYS_PER_ROW = 4

    def __init__(self, channel_id: int):
        super().__init__(timeout=180)
        self.channel_id = channel_id
        self.cfg = _get_cfg(channel_id)
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        keys = list(_CFG_DEFS.keys())
        for i, key in enumerate(keys):
            row = i // self._KEYS_PER_ROW  # rows 0–2 for 11 settings
            self.add_item(CfgToggleButton(self, key, row))
        # row 3: runtime + timeout controls
        self.add_item(CfgRuntimeButton(self))
        self.add_item(CfgTimeoutButton(self, -1))
        self.add_item(CfgTimeoutButton(self, +1))
        # row 4: reset + save
        self.add_item(CfgResetButton(self))
        self.add_item(CfgSaveButton(self))

    def build_embed(self) -> discord.Embed:
        e = discord.Embed(color=ACCENT)
        e.title = "⚙️  Dumpers Config"
        lines = []
        for key, (label, desc, default) in _CFG_DEFS.items():
            val     = self.cfg[key]
            changed = "  *(changed)*" if val != default else ""
            icon    = "✅" if val else "❌"
            lines.append(f"{icon} **{label}** — {desc}{changed}")
        # Runtime (only affects .l) — lute is not compatible with main.luau's module system
        rt = self.cfg.get("_runtime", "lune").upper()
        if rt == "LUTE":
            rt = "LUNE"  # lute breaks require('./mods/...'), always display lune
        lines.append(f"🔧 **Runtime (.l only)** — `{rt}` (lune only — lute is incompatible with main.luau)")
        # Timeout
        secs = self.cfg.get("_timeout", 180)
        lines.append(f"⏱️ **Processing Timeout** — `{secs}s` (max {USER_TIMEOUT}s)")
        e.description = "\n".join(lines)
        e.set_footer(text="6Vms · Dumpers Config · affects all dumpers · panel expires in 3 min")
        return e

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except Exception:
            pass


# ── AI config panel ────────────────────────────────────────────────────────────
class AIModelSelect(discord.ui.Select):
    def __init__(self, panel: "AICfgPanel"):
        self.panel = panel
        current = _ai_cfg["model"]
        options = [
            discord.SelectOption(label=m, value=m, default=(m == current))
            for m in _AI_MODELS
        ]
        super().__init__(
            placeholder="Select NVIDIA model",
            min_values=1, max_values=1,
            options=options, row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        _ai_cfg["model"] = self.values[0]
        self.panel._rebuild()
        await interaction.response.edit_message(embed=self.panel.build_embed(), view=self.panel)

class AITempSelect(discord.ui.Select):
    def __init__(self, panel: "AICfgPanel"):
        self.panel = panel
        current = _ai_cfg["temperature"]
        options = [
            discord.SelectOption(
                label=f"{t:.1f}", value=f"{t}",
                default=(t == current),
            )
            for t in _AI_TEMPS
        ]
        super().__init__(
            placeholder="Select temperature",
            min_values=1, max_values=1,
            options=options, row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        _ai_cfg["temperature"] = float(self.values[0])
        self.panel._rebuild()
        await interaction.response.edit_message(embed=self.panel.build_embed(), view=self.panel)

class AITokenSelect(discord.ui.Select):
    def __init__(self, panel: "AICfgPanel"):
        self.panel = panel
        current = _ai_cfg["max_tokens"]
        options = [
            discord.SelectOption(
                label=f"{t:,}", value=f"{t}",
                default=(t == current),
            )
            for t in _AI_TOKEN_OPTS
        ]
        super().__init__(
            placeholder="Select max tokens",
            min_values=1, max_values=1,
            options=options, row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        _ai_cfg["max_tokens"] = int(self.values[0])
        self.panel._rebuild()
        await interaction.response.edit_message(embed=self.panel.build_embed(), view=self.panel)

class AIThinkingButton(discord.ui.Button):
    def __init__(self, panel: "AICfgPanel", row: int):
        self.panel = panel
        enabled = _ai_cfg["thinking"]
        super().__init__(
            label=f"🧠 Thinking: {'ON' if enabled else 'OFF'}",
            style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        _ai_cfg["thinking"] = not _ai_cfg["thinking"]
        self.panel._rebuild()
        await interaction.response.edit_message(embed=self.panel.build_embed(), view=self.panel)

class AIResetButton(discord.ui.Button):
    def __init__(self, panel: "AICfgPanel", row: int):
        self.panel = panel
        super().__init__(label="Reset Defaults", style=discord.ButtonStyle.danger, row=row)

    async def callback(self, interaction: discord.Interaction):
        _ai_cfg["model"] = "deepseek-ai/deepseek-v4-flash-0731"
        _ai_cfg["temperature"] = 0.3
        _ai_cfg["max_tokens"] = 16384
        _ai_cfg["thinking"] = False
        self.panel._rebuild()
        await interaction.response.edit_message(embed=self.panel.build_embed(), view=self.panel)

class AISaveButton(discord.ui.Button):
    def __init__(self, panel: "AICfgPanel", row: int):
        self.panel = panel
        super().__init__(label="Save", style=discord.ButtonStyle.primary, row=row)

    async def callback(self, interaction: discord.Interaction):
        _save_ai_cfg()
        for item in self.panel.children:
            item.disabled = True
        e = self.panel.build_embed()
        e.set_footer(text="6Vms · AI config saved ✅")
        await interaction.response.edit_message(embed=e, view=self.panel)

class AICfgPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        self.add_item(AIModelSelect(self))
        self.add_item(AITempSelect(self))
        self.add_item(AITokenSelect(self))
        self.add_item(AIThinkingButton(self, row=3))
        self.add_item(AIResetButton(self, row=3))
        self.add_item(AISaveButton(self, row=4))

    def build_embed(self) -> discord.Embed:
        e = discord.Embed(color=ACCENT)
        e.title = "🤖 AI Config"
        e.description = (
            f"**Model** — `{_ai_cfg['model']}`\n"
            f"**Temperature** — `{_ai_cfg['temperature']:.1f}`\n"
            f"**Max Tokens** — `{_ai_cfg['max_tokens']:,}`\n"
            f"**Thinking** — `{'ON' if _ai_cfg['thinking'] else 'OFF'}`\n\n"
            "Switch the model / settings used by every NVIDIA AI call (`.deobf`, `.ultra`, `.ultra2`, `.vm`, `.chain`, `.bulk`...).\n"
            "Changes apply after **Save**."
        )
        e.set_footer(text="6Vms · AI Config · panel expires in 3 min")
        return e

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except Exception:
            pass


# ── Obfuscator config panel ────────────────────────────────────────────────────
class ObfCfgToggleButton(discord.ui.Button):
    def __init__(self, panel: "ObfCfgPanel", key: str, row: int):
        self.panel = panel
        self.key   = key
        label, desc, default = _OBF_CFG_DEFS[key]
        enabled = panel.cfg[key]
        super().__init__(
            label=f"{'✅' if enabled else '❌'} {label}",
            style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        self.panel.cfg[self.key] = not self.panel.cfg[self.key]
        self.panel._rebuild()
        await interaction.response.edit_message(embed=self.panel.build_embed(), view=self.panel)

class ObfCfgGeneratorSelect(discord.ui.Select):
    def __init__(self, panel: "ObfCfgPanel"):
        self.panel = panel
        current = panel.cfg.get("_generator", "Number")
        options = [
            discord.SelectOption(label=g, value=g, default=(g == current))
            for g in _OBF_GENERATORS
        ]
        super().__init__(
            placeholder="Select generator type",
            min_values=1, max_values=1,
            options=options, row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        self.panel.cfg["_generator"] = self.values[0]
        self.panel._rebuild()
        await interaction.response.edit_message(embed=self.panel.build_embed(), view=self.panel)

class ObfCfgResetButton(discord.ui.Button):
    def __init__(self, panel: "ObfCfgPanel"):
        self.panel = panel
        super().__init__(label="Reset Defaults", style=discord.ButtonStyle.danger, row=3)

    async def callback(self, interaction: discord.Interaction):
        self.panel.cfg = {k: v[2] for k, v in _OBF_CFG_DEFS.items()}
        self.panel.cfg["_generator"] = "Number"
        self.panel._rebuild()
        await interaction.response.edit_message(embed=self.panel.build_embed(), view=self.panel)

class ObfCfgSaveButton(discord.ui.Button):
    def __init__(self, panel: "ObfCfgPanel"):
        self.panel = panel
        super().__init__(label="Save", style=discord.ButtonStyle.primary, row=3)

    async def callback(self, interaction: discord.Interaction):
        _obf_cfg_store[self.panel.channel_id] = dict(self.panel.cfg)
        _save_obf_cfg()
        for item in self.panel.children:
            item.disabled = True
        e = self.panel.build_embed()
        e.set_footer(text="6Vms · obf config saved ✅")
        await interaction.response.edit_message(embed=e, view=self.panel)

class ObfCfgPanel(discord.ui.View):
    _KEYS_PER_ROW = 2

    def __init__(self, channel_id: int):
        super().__init__(timeout=180)
        self.channel_id = channel_id
        self.cfg = _get_obf_cfg(channel_id)
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        keys = list(_OBF_CFG_DEFS.keys())
        for i, key in enumerate(keys):
            row = i // self._KEYS_PER_ROW
            self.add_item(ObfCfgToggleButton(self, key, row))
        self.add_item(ObfCfgGeneratorSelect(self))
        self.add_item(ObfCfgResetButton(self))
        self.add_item(ObfCfgSaveButton(self))

    def build_embed(self) -> discord.Embed:
        e = discord.Embed(color=ACCENT)
        e.title = "⚡ Goofyscator Config"
        lines = []
        for key, (label, desc, default) in _OBF_CFG_DEFS.items():
            val     = self.cfg[key]
            changed = "  *(changed)*" if val != default else ""
            icon    = "✅" if val else "❌"
            lines.append(f"{icon} **{label}** — {desc}{changed}")
        gen = self.cfg.get("_generator", "Number")
        lines.append(f"🔢 **Generator** — `{gen}`")
        e.description = "\n".join(lines)
        e.set_footer(text="6Vms · Obfuscator Config · panel expires in 3 min")
        return e

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except Exception:
            pass

# ── DarkLua panel ─────────────────────────────────────────────────────────────
DARKLUA_RULES = [
    "compute_expression",
            "remove_unused_while",
            "remove_unused_if_branch",
            "remove_nil_declaration",
            "convert_index_to_field",
            "remove_comments",
            "remove_method_definition",
            "remove_spaces",
            "remove_types",
            "remove_unused_variable",
            "remove_function_call_parens",
            "remove_empty_do",
            "remove_compound_assignment",
            "remove_continue",
            "remove_if_expression",
            "remove_interpolated_string",
            "remove_floor_division",
            "filter_after_early_return",
            "group_local_assignment",
            "rename_variables",

]
DARKLUA_GENS = ["readable", "dense", "retain_lines"]


def _build_darklua_cfg(rules: list[str], generator: str, column_unlimited: bool) -> str:
    cfg: dict = {"generator": {"name": generator}}
    if not column_unlimited:
        cfg["generator"]["column_span"] = 80
    if rules:
        cfg["rules"] = rules
    return _json.dumps(cfg, indent=2)

def _run_darklua(src_bytes: bytes, rules: list[str], generator: str, column_unlimited: bool):
    stamp = _stamp()
    in_path  = TMP / f"{stamp}_dlua_in.lua"
    out_path = TMP / f"{stamp}_dlua_out.lua"
    cfg_path = TMP / f"{stamp}_dlua.json"
    try:
        in_path.write_bytes(src_bytes)
        cfg_path.write_text(_build_darklua_cfg(rules, generator, column_unlimited))
        ok, log, took = _run_proc(
            [str(DARKLUA), "process", "--config", str(cfg_path), str(in_path), str(out_path)]
        )
        if ok and out_path.exists() and out_path.stat().st_size > 0:
            return True, out_path.read_bytes(), took
        tail = (log or "").strip().splitlines()
        return False, (tail[-1] if tail else "darklua produced no output")[:300], took
    finally:
        for p in (in_path, out_path, cfg_path):
            try: p.unlink()
            except OSError: pass

class DarkluaRuleSelect(discord.ui.Select):
    def __init__(self, panel: "DarkluaPanel"):
        self.panel = panel
        options = [
            discord.SelectOption(label=r, value=r)
            for r in DARKLUA_RULES
        ]
        super().__init__(
            placeholder="select your darklua configuration",
            min_values=0, max_values=len(options),
            options=options, row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        self.panel.rules = list(self.values)
        await interaction.response.edit_message(
            embed=self.panel.build_embed(), view=self.panel
        )

class DarkluaGenButton(discord.ui.Button):
    def __init__(self, panel: "DarkluaPanel", gen: str, row: int):
        self.panel = panel
        self.gen   = gen
        label = f"Gen: {gen}"
        style = discord.ButtonStyle.primary if panel.generator == gen else discord.ButtonStyle.secondary
        super().__init__(label=label, style=style, row=row)

    async def callback(self, interaction: discord.Interaction):
        self.panel.generator = self.gen
        self.panel._refresh_buttons()
        await interaction.response.edit_message(
            embed=self.panel.build_embed(), view=self.panel
        )

class DarkluaColButton(discord.ui.Button):
    def __init__(self, panel: "DarkluaPanel", unlimited: bool, row: int):
        self.panel     = panel
        self.unlimited = unlimited
        label = "Column: unlimited" if unlimited else "∞"
        style = discord.ButtonStyle.primary if panel.column_unlimited == unlimited else discord.ButtonStyle.secondary
        super().__init__(label=label, style=style, row=row)

    async def callback(self, interaction: discord.Interaction):
        self.panel.column_unlimited = self.unlimited
        self.panel._refresh_buttons()
        await interaction.response.edit_message(
            embed=self.panel.build_embed(), view=self.panel
        )

class DarkluaApplyButton(discord.ui.Button):
    def __init__(self, panel: "DarkluaPanel"):
        self.panel = panel
        super().__init__(label="Apply", style=discord.ButtonStyle.success, row=3)

    async def callback(self, interaction: discord.Interaction):
        for item in self.panel.children:
            item.disabled = True
        e = discord.Embed(color=ACCENT, timestamp=datetime.now())
        e.description = f"⚙️ Processing `{self.panel.name}` with DarkLua…"
        e.set_footer(text="6Vms")
        await interaction.response.edit_message(embed=e, view=self.panel)
        asyncio.create_task(self.panel._apply(interaction))

class DarkluaPanel(discord.ui.View):
    def __init__(self, src_bytes: bytes, name: str, orig_msg: discord.Message):
        super().__init__(timeout=180)
        self.src_bytes       = src_bytes
        self.name            = name
        self.orig_msg        = orig_msg
        self.rules: list[str] = []
        self.generator        = "readable"
        self.column_unlimited = True
        self._build_items()

    def _build_items(self):
        self.clear_items()
        self.add_item(DarkluaRuleSelect(self))
        for gen in DARKLUA_GENS:
            self.add_item(DarkluaGenButton(self, gen, row=1))
        self.add_item(DarkluaColButton(self, True,  row=2))
        self.add_item(DarkluaColButton(self, False, row=2))
        self.add_item(DarkluaApplyButton(self))

    def _refresh_buttons(self):
        self._build_items()

    def build_embed(self) -> discord.Embed:
        rules_str = ", ".join(f"`{r}`" for r in self.rules) if self.rules else "*none selected*"
        e = discord.Embed(color=ACCENT, timestamp=datetime.now())
        e.description = (
            f"**`{self.name}`** — DarkLua\n"
            f"Rules: {rules_str}\n"
            f"Generator: `{self.generator}` · Column: `{'unlimited' if self.column_unlimited else '80'}`"
        )
        e.set_footer(text="6Vms · panel expires in 3 min")
        return e

    async def _apply(self, interaction: discord.Interaction):
        await react(self.orig_msg, "⏳")
        try:
            ok, result, took = await asyncio.to_thread(
                _run_darklua, self.src_bytes, self.rules, self.generator, self.column_unlimited
            )
            if ok:
                data_bytes = result if isinstance(result, bytes) else result.encode()
                data_str   = data_bytes.decode("utf-8", errors="ignore")
                out_fname  = _rand_name("darklua.lua")
                raw_url    = await pastefy.upload(http, out_fname, data_str)
                e = discord.Embed(color=GOOD, timestamp=datetime.now())
                e.description = (
                    f"**`{self.name}`** — DarkLua\n"
                    f"`{len(data_str.splitlines()):,} lines` · "
                    f"`{len(data_bytes)/1024:.1f} KB` · `{took:.2f}s`"
                )
                if raw_url: e.description += f"\n{raw_url}"
                e.set_footer(text="6Vms")
                tmp_out = TMP / f"__dl_{_stamp()}.lua"
                tmp_out.write_bytes(data_bytes)
                try:
                    await interaction.followup.send(
                        content=self.orig_msg.author.mention, embed=e,
                        file=discord.File(str(tmp_out), filename=out_fname),
                    )
                finally:
                    try: tmp_out.unlink()
                    except OSError: pass
                await unreact(self.orig_msg, "⏳"); await react(self.orig_msg, "✅")
            else:
                e = discord.Embed(color=BAD, timestamp=datetime.now())
                e.description = f"**`{self.name}`**\n{result}"
                e.set_footer(text="6Vms")
                await interaction.followup.send(content=self.orig_msg.author.mention, embed=e)
                await unreact(self.orig_msg, "⏳"); await react(self.orig_msg, "❌")
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"**`{self.name}`**\nerror: {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            try: await interaction.followup.send(content=self.orig_msg.author.mention, embed=e)
            except Exception: pass
            await unreact(self.orig_msg, "⏳"); await react(self.orig_msg, "❌")

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try: await self.message.edit(view=self)
        except Exception: pass

# ── Obf2 Panel ─────────────────────────────────────────────────────────────────
OBF2_GOOFY_PRESETS = {
    "Minify":  {"renameAllLocals": False, "renameGlobals": False, "encryptStrings": False, "controlFlow": False, "opaquePredicates": False, "bytecodeObfuscation": False, "removeComments": True},
    "Medium":  {"renameAllLocals": True,  "renameGlobals": False, "encryptStrings": True,  "controlFlow": False, "opaquePredicates": False, "bytecodeObfuscation": False, "removeComments": True},
    "High":    {"renameAllLocals": True,  "renameGlobals": True,  "encryptStrings": True,  "controlFlow": True,  "opaquePredicates": False, "bytecodeObfuscation": True,  "removeComments": True},
    "Max":     {"renameAllLocals": True,  "renameGlobals": True,  "encryptStrings": True,  "controlFlow": True,  "opaquePredicates": True,  "bytecodeObfuscation": True,  "removeComments": True},
}
OBF2_PROM_PRESETS = ["Minify", "Weak", "Medium", "Strong"]

class Obf2PresetSelect(discord.ui.Select):
    def __init__(self, panel: "Obf2Panel"):
        self.panel = panel
        self._rebuild_options()
        super().__init__(placeholder="Select preset…", options=self._options, min_values=1, max_values=1, row=0)

    def _rebuild_options(self):
        if self.panel.backend == "Prometheus":
            names = OBF2_PROM_PRESETS
        else:
            names = list(OBF2_GOOFY_PRESETS.keys())
        self._options = [discord.SelectOption(label=k, value=k) for k in names]

    async def callback(self, interaction: discord.Interaction):
        self.panel.preset = self.values[0]
        await interaction.response.edit_message(embed=self.panel.build_embed(), view=self.panel)

class Obf2BackendButton(discord.ui.Button):
    def __init__(self, panel: "Obf2Panel"):
        self.panel = panel
        label = "⚙ Local (Prometheus)" if panel.backend == "Prometheus" else "☁ API (Goofyscator)"
        super().__init__(label=label, style=discord.ButtonStyle.primary, row=1)

    async def callback(self, interaction: discord.Interaction):
        self.panel.backend = "API" if self.panel.backend == "Prometheus" else "Prometheus"
        self.label = "⚙ Local (Prometheus)" if self.panel.backend == "Prometheus" else "☁ API (Goofyscator)"
        self.panel.preset = OBF2_PROM_PRESETS[0] if self.panel.backend == "Prometheus" else list(OBF2_GOOFY_PRESETS.keys())[0]
        for child in self.panel.children:
            if isinstance(child, Obf2PresetSelect):
                child._rebuild_options()
                child.options = child._options
        await interaction.response.edit_message(embed=self.panel.build_embed(), view=self.panel)

class Obf2BeautifyButton(discord.ui.Button):
    def __init__(self, panel: "Obf2Panel"):
        self.panel = panel
        label = "✅ Beautify" if panel.beautify else "❌ Beautify"
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction):
        self.panel.beautify = not self.panel.beautify
        self.label = "✅ Beautify" if self.panel.beautify else "❌ Beautify"
        await interaction.response.edit_message(embed=self.panel.build_embed(), view=self.panel)

class Obf2RunButton(discord.ui.Button):
    def __init__(self, panel: "Obf2Panel"):
        self.panel = panel
        super().__init__(label="▶ Run", style=discord.ButtonStyle.success, row=3)

    async def callback(self, interaction: discord.Interaction):
        self.disabled = True
        for item in self.panel.children:
            item.disabled = True
        e = self.panel.build_embed()
        e.description += "\n⏳ **Obfuscating…**"
        await interaction.response.edit_message(embed=e, view=self.panel)
        await react(self.panel.orig_msg, "⏳")
        try:
            if self.panel.backend == "Prometheus":
                out = await self._run_prometheus()
            else:
                out = await self._run_goofyscator()
        except Exception as ex:
            out = f"-- Obfuscation failed: {ex}"
        tmp = TMP / f"{_stamp()}_obf2.lua"
        tmp.write_text(out)
        try:
            raw_url = await pastefy.upload(http, f"obf2_{self.panel.preset.lower()}.lua", out)
            e2 = discord.Embed(color=GOOD, timestamp=datetime.now())
            e2.description = f"🔒 **Obf2** — `{self.panel.name}` — `{self.panel.preset}` — `{self.panel.backend}` — `{len(out)}` bytes"
            if raw_url: e2.description += f"\n{raw_url}"
            e2.set_footer(text="6Vms")
            await interaction.followup.send(content=self.panel.orig_msg.author.mention, embed=e2, file=discord.File(str(tmp), filename=f"obf2_{self.panel.preset.lower()}.lua"))
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(self.panel.orig_msg, "⏳"); await react(self.panel.orig_msg, "✅")

    async def _run_prometheus(self) -> str:
        inp = TMP / f"{_stamp()}_prom_in.lua"
        inp.write_text(self.panel.src)
        try:
            proc = await asyncio.create_subprocess_exec(
                LUAJIT, PROMETHEUS_CLI,
                "--preset", self.panel.preset,
                str(inp), "--out", str(inp.with_suffix(".out.lua")),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=PROMETHEUS_DIR,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            out_path = inp.with_suffix(".out.lua")
            if out_path.exists():
                result = out_path.read_text("utf-8", errors="replace")
                out_path.unlink()
                return result
            err = stderr.decode("utf-8", errors="replace") if stderr else "no output"
            return f"-- Prometheus failed:\n{err[:500]}"
        finally:
            try: inp.unlink()
            except: pass

    async def _run_goofyscator(self) -> str:
        preset = OBF2_GOOFY_PRESETS.get(self.panel.preset, OBF2_GOOFY_PRESETS["Max"])
        payload = {"code": self.panel.src, "config": dict(preset), "seed": hashlib.md5(uuid.uuid4().bytes).hexdigest()[:8]}
        if self.panel.beautify:
            payload["config"]["beautify"] = True
        async with http.post(
            "https://api.goofyscator.xyz/v1/obfuscate",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as r:
            data = await r.json()
        out = data.get("code") or data.get("obfuscated") or "-- API returned no output"
        if len(out) < 50:
            out = f"-- Minimal output\n{self.panel.src}"
        return out

class Obf2Panel(discord.ui.View):
    def __init__(self, src: str, name: str, orig_msg: discord.Message):
        super().__init__(timeout=180)
        self.src = src
        self.name = name
        self.orig_msg = orig_msg
        self.backend = "Prometheus"
        self.preset = OBF2_PROM_PRESETS[0]
        self.beautify = False
        self.add_item(Obf2PresetSelect(self))
        self.add_item(Obf2BackendButton(self))
        self.add_item(Obf2BeautifyButton(self))
        self.add_item(Obf2RunButton(self))

    def build_embed(self) -> discord.Embed:
        e = discord.Embed(color=ACCENT, timestamp=datetime.now())
        e.title = "🔒 Obf2 — Prometheus / Goofyscator"
        e.description = (
            f"**File:** `{self.name}`\n"
            f"**Backend:** `{self.backend}`\n"
            f"**Preset:** `{self.preset}`\n"
            f"**Beautify:** `{self.beautify}`\n\n"
            f"Switch backend with ⚙ button, then press **▶ Run**."
        )
        e.set_footer(text="6Vms · panel expires in 3 min")
        return e

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try: await self.message.edit(view=self)
        except Exception: pass

# ── bot ───────────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot  = discord.Client(intents=intents)
http: aiohttp.ClientSession | None = None
queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=QUEUE_CAPACITY)

def _queue_put(job: dict, message) -> int | str:
    """Put a job into the priority queue with capacity protection.
    Priority: 0=premium/admin, 1=booster, 2=normal.
    Returns the number of jobs ahead after insertion (0 = processing now).
    Returns error string if queue is full or user pacing limits exceeded."""
    import time as _t
    user_id = message.author.id
    
    # Check per-user pacing
    ok, reason = _check_user_pacing(user_id)
    if not ok:
        QUEUE_HEALTH["total_rejected"] += 1
        return f"rejected: {reason}"
    
    # Check queue capacity
    if queue.full():
        QUEUE_HEALTH["total_rejected"] += 1
        _release_user_slot(user_id)
        return "rejected: queue at capacity"
    
    job["message"] = message
    stamp = _t.time()
    member = message.author
    if _is_premium(member) or _is_admin(member):
        priority = 0
    elif _is_booster(member):
        priority = 1
    else:
        priority = 2
    try:
        queue.put_nowait((priority, stamp, job))
    except asyncio.QueueFull:
        QUEUE_HEALTH["total_rejected"] += 1
        _release_user_slot(user_id)
        return "rejected: queue full"
    
    QUEUE_HEALTH["total_queued"] += 1
    return max(0, queue.qsize() - 1)
_byp_state: dict = {}  # rate-limit tracking for .byp
_delta_state: dict = {}  # rate-limit tracking for .delta

def _run_revea(src_bytes: bytes, name: str, extra_args: list[str] | None = None) -> tuple[bool, bytes | str, float]:
    """Execute Revea.lol dumper — runs src through main.luau with version=1 flag."""
    stamp = _stamp()
    TMP.mkdir(exist_ok=True)
    in_rel  = f"bot_tmp/{stamp}_revea_in.lua"
    out_rel = f"bot_tmp/{stamp}_revea_out.lua"
    in_path  = ROOT / in_rel
    out_path = ROOT / out_rel
    try:
        in_path.write_bytes(src_bytes)
        lune_bin = str(LUNE) if LUNE.exists() else "lune"
        cmd = [lune_bin, "run", "main.luau", in_rel, f"out={out_rel}", "version=1"]
        if extra_args:
            cmd.extend(extra_args)
        ok, log, took = _run_proc(cmd, cwd=ROOT)
        if out_path.exists() and out_path.stat().st_size > 0:
            text = out_path.read_text(errors="ignore")
            if text.lstrip().startswith("--err"):
                reason = text.lstrip()[5:].strip()
                return False, reason[:300] or "revea error", took
            return True, text.encode("utf-8"), took
        tail = (log or "").strip().splitlines()
        return False, (tail[-1] if tail else "revea dumper produced no output")[:300], took
    finally:
        for p in (in_path, out_path):
            try: p.unlink()
            except OSError: pass

# ── .r2 worker (Aspect) ───────────────────────────────────────────────────────

def _run_aspect(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    """Execute Aspect environment dumper — runs src through mods/aspect.luau."""
    stamp = _stamp()
    TMP.mkdir(exist_ok=True)
    in_rel  = f"bot_tmp/{stamp}_aspect_in.lua"
    out_rel = f"bot_tmp/{stamp}_aspect_out.lua"
    in_path  = ROOT / in_rel
    out_path = ROOT / out_rel
    aspect_script = ROOT / "mods" / "aspect.luau"
    if not aspect_script.exists():
        return False, "aspect script not found", 0
    try:
        in_path.write_bytes(src_bytes)
        lune_bin = str(LUNE) if LUNE.exists() else "lune"
        cmd = [lune_bin, "run", str(aspect_script), str(in_path), str(out_path)]
        ok, log, took = _run_proc(cmd, cwd=ROOT)
        if out_path.exists() and out_path.stat().st_size > 0:
            text = out_path.read_text(errors="ignore")
            if not text.strip():
                tail = (log or "").strip().splitlines()
                return False, (tail[-1] if tail else "aspect produced no output")[:300], took
            return True, text.encode("utf-8"), took
        tail = (log or "").strip().splitlines()
        return False, (tail[-1] if tail else "aspect produced no output")[:300], took
    finally:
        for p in (in_path, out_path):
            try: p.unlink()
            except OSError: pass

def _run_aspect_native(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    """Execute Aspect-Native dumper — runs src through mods/aspect_native.luau."""
    stamp = _stamp()
    TMP.mkdir(exist_ok=True)
    in_rel  = f"bot_tmp/{stamp}_an_in.lua"
    out_rel = f"bot_tmp/{stamp}_an_out.lua"
    in_path  = ROOT / in_rel
    out_path = ROOT / out_rel
    script = ROOT / "mods" / "aspect_native.luau"
    if not script.exists():
        return False, "aspect_native script not found", 0
    try:
        in_path.write_bytes(src_bytes)
        lune_bin = str(LUNE) if LUNE.exists() else "lune"
        cmd = [lune_bin, "run", str(script), str(in_path), str(out_path)]
        ok, log, took = _run_proc(cmd, cwd=ROOT)
        if out_path.exists() and out_path.stat().st_size > 0:
            text = out_path.read_text(errors="ignore")
            if not text.strip():
                tail = (log or "").strip().splitlines()
                return False, (tail[-1] if tail else "no output")[:300], took
            return True, text.encode("utf-8"), took
        tail = (log or "").strip().splitlines()
        return False, (tail[-1] if tail else "no output")[:300], took
    finally:
        for p in (in_path, out_path):
            try: p.unlink()
            except OSError: pass

# ── .l worker (Lune) ──────────────────────────────────────────────────────────
def _lune_dump(
    in_rel: str,
    out_rel: str,
    extra_args: list[str] | None = None,
    runtime: str = "lune",   # kept for API compatibility — lune is always used
    job_timeout: int | None = None,
    main_luau: str = "main.luau",
) -> tuple[bool, str | None, float]:
    TMP.mkdir(exist_ok=True)
    effective_timeout = int(job_timeout) if job_timeout else ADMIN_TIMEOUT

    # lute.exe cannot run main.luau — it lacks lune's require('./mods/...') module resolver.
    # Always use lune regardless of what the runtime setting says.
    lune_bin = str(LUNE) if LUNE.exists() else "lune"
    cmd = [lune_bin, "run", main_luau, in_rel, f"out={out_rel}", "version=1"]
    if extra_args:
        cmd.extend(extra_args)

    print(f"[_lune_dump] running: {' '.join(cmd)} (timeout={effective_timeout}s)")
    started = time.perf_counter()
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except (FileNotFoundError, OSError) as exc:
        return False, f"failed to start: {exc}", time.perf_counter() - started
    try:
        stdout, stderr = proc.communicate(timeout=effective_timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        try: proc.communicate(timeout=5)
        except Exception: pass
        print(f"[_lune_dump] TIMEOUT after {effective_timeout}s")
        return False, "timeout", effective_timeout

    took = time.perf_counter() - started
    log  = (stdout or "") + (stderr or "")
    log  = _redact(log)

    print(f"[_lune_dump] exit={proc.returncode} took={took:.1f}s")
    if log.strip():
        # Print last 5 lines so we can see what went wrong without flooding
        for line in log.strip().splitlines()[-5:]:
            print(f"[_lune_dump]  > {line}")

    m = TIME_RE.search(log)
    if m:
        took = float(m.group(1))

    out_path = ROOT / out_rel
    if proc.returncode != 0 or not out_path.exists():
        lines     = [l.strip() for l in log.splitlines() if l.strip()]
        # Only drop lune's own meta-brackets [lune]/[info]/etc, keep [string "..."]:N: errors
        err_lines = [l for l in lines if not _LUNE_META_RE.match(l)]
        msg = (err_lines[-1] if err_lines else lines[-1] if lines else "engine error")[:300]
        return False, msg, took

    head = out_path.read_text(errors="ignore")
    if head.lstrip().startswith("--err"):
        reason = head.lstrip()[5:].strip()
        return False, (reason or "engine error")[:300], took

    return True, None, took

_RENUMBER_RE = re.compile(r"\br(\d+)\b")

def _make_source_table(lua_code: str) -> str:
    lines = lua_code.splitlines()
    result = ["| IP | Line | Code (first 72 chars)", "| :- | :--- | :----------------------"]
    ip = 0
    for i, line in enumerate(lines, 1):
        trimmed = line.strip()
        if not trimmed:
            continue
        ip += 1
        if len(trimmed) > 72:
            trimmed = trimmed[:69] + "..."
        trimmed = trimmed.replace("`", "'")
        result.append(f"| {ip} | {i} | `{trimmed}`")
    return "\n".join(result)

def _detect_obfuscators(code: str) -> str:
    """Detect known obfuscators from source code patterns — returns formatted string."""
    import re as _re
    _patterns: list[tuple[str, str, str]] = [
        (r"return [A-Za-z0-9_]+\([0-9A-Za-z_]+\(\),[A-Za-z0-9,_\{\}\)\(]+\)", "IronBrew 2", "IB2-style VM dispatch"),
        (r"newproxy,...metatable,...metatable,select,", "Prometheus", "Prometheus-style env hooking"),
        (r"\(\[\[This file was protected with MoonSec V3", "MoonSec V3", "MoonSec V3 header"),
        (r"local v0=string\.char;local v1=string\.byte;local v2=string\.sub;local v3=bit32 or bit", "LuaObfuscator", "LuaObfuscator string encoding"),
        (r"does your environment support load/loadstring\?", "Luraph", "Luraph loader check"),
        (r"local [a-z]+='?v1'?;local [a-z]+='?v2'?;local [a-z]+='?v3'?", "Generic VM", "VM version guards"),
        (r"_G\.LuraphContinue", "Luraph", "Luraph continue polyfill"),
        (r"Luraph Obfuscator", "Luraph", "Luraph branding"),
        (r"Prometheus\.new\(\)", "Prometheus V2", "Prometheus V2 instantiation"),
        (r"Synapse Xen|syn\.crypt|synapse_xen", "Synapse Xen", "Synapse Xen crypto"),
        (r"ScriptGuard|antitamper|integrity.check", "Anti-Tamper", "Integrity check detected"),
        (r"local [a-z]+=string\.char;local [a-z]+=string\.byte", "LuaObfuscator", "LuaObfuscator byte encoding"),
    ]
    hits = []
    for pat, name, desc in _patterns:
        if _re.search(pat, code):
            hits.append((name, desc))
    if not hits:
        return ""
    seen = {}
    lines = []
    for name, desc in hits:
        if name not in seen:
            seen[name] = desc
            lines.append(f"  ⚠ {name} — {desc}")
    return "Detected Obfuscators:\n" + "\n".join(lines)


def _run_lph_py(src_bytes: bytes, name: str) -> tuple[bool, bytes | str | dict, float]:
    """Run the Python LPH AI deobfuscator (deobfuscator.py)."""
    stamp = _stamp()
    TMP.mkdir(exist_ok=True)
    in_path = TMP / f"{stamp}_lph_in.lua"
    out_lua  = TMP / f"{stamp}_lph_in.deobf.lua"
    out_txt  = TMP / f"{stamp}_lph_in.analysis.txt"
    out_json = TMP / f"{stamp}_lph_in.instructions.json"
    try:
        in_path.write_bytes(src_bytes)
        start = time.time()
        import subprocess as _sp
        env = {**os.environ}
        try:
            proc = _sp.Popen(
                [sys.executable, str(ROOT / "deobfuscator.py"), str(in_path)],
                cwd=str(ROOT),
                stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True,
                env=env,
                creationflags=getattr(_sp, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            log, _ = proc.communicate(timeout=600)
        except _sp.TimeoutExpired:
            try: proc.kill()
            except: pass
            return False, "timeout (600s)", time.time() - start
        except (FileNotFoundError, OSError) as exc:
            return False, f"failed to start: {exc}", time.time() - start
        took = time.time() - start
        log = _redact(log or "")
        if proc.returncode != 0:
            tail = [l.strip() for l in log.splitlines() if l.strip()]
            return False, (tail[-1] if tail else "lph_py failed")[:500], took
        result = {}
        if out_lua.exists():
            result["dumped.lua"] = out_lua.read_bytes()
        if out_json.exists():
            result["env.json"] = out_json.read_bytes()
        if out_txt.exists():
            result["summary.txt"] = out_txt.read_bytes()
        if not result:
            err = log.strip() or "deobfuscator produced no output files"
            return False, err[:800], took
        return True, result, took
    finally:
        for p in (in_path, out_lua, out_txt, out_json):
            try: p.unlink()
            except OSError: pass


def _run_delta_sync(url: str) -> str:
    """Synchronous Delta-OpenSrc bypass — imports deltax and calls getKey."""
    try:
        import deltax as _dx
        return _dx.getKey(url)
    except Exception as ex:
        return f"error: {ex}"

async def _run_delta(url: str) -> str:
    """Run Delta-OpenSrc bypass in executor thread."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run_delta_sync, url)


def _run_45ms(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    """Run 45ms.luau dumper via lune."""
    stamp = _stamp()
    TMP.mkdir(exist_ok=True)
    in_rel  = f"bot_tmp/{stamp}_45ms_in.lua"
    out_rel = f"bot_tmp/{stamp}_45ms_out.lua"
    in_path  = ROOT / in_rel
    out_path = ROOT / out_rel
    try:
        in_path.write_bytes(src_bytes)
        lune_bin = str(LUNE) if LUNE.exists() else "lune"
        cmd = [lune_bin, "run", "45ms.luau", in_rel, out_rel]
        ok, log, took = _run_proc(cmd, cwd=ROOT)
        if out_path.exists() and out_path.stat().st_size > 0:
            text = out_path.read_text(errors="ignore")
            return True, text.encode("utf-8"), took
        tail = (log or "").strip().splitlines()
        return False, (tail[-1] if tail else "45ms produced no output")[:300], took
    finally:
        for p in (in_path, out_path):
            try: p.unlink()
            except OSError: pass


# ── Alternative Dumper Registry ────────────────────────────────────────────────
_ALT_DUMPERS = {
    ".kolenv":    ("Kolenv",        ROOT / "Kolenv" / "Kolenv.lua",              "lune",     "Luraph env logger / Kolenv-style dumper"),
    ".mimic":     ("Mimic",         ROOT / "Mimic" / "main.luau",                "lune",     "Mimic environment logger (sandboxed exec)"),
    ".mimic2":    ("Mimic2",        ROOT / "Mimic2" / "main.luau",               "lune",     "Mimic v2 environment logger"),
    ".old45ms":   ("Old-45ms",      ROOT / "Old-45ms" / "45ms.luau",             "lune",     "Original 45ms dumper (old version)"),
    ".flamecoder":("FlameCoderV3",  ROOT / "FlamecoderV3" / "flamecoder.lua",    "lune",     "FlameCoder dumper v3"),
    ".pengue":    ("Pengue-Env",    ROOT / "mods" / "aspect_native.luau",         "lune",     "Pengue environment logger"),
    ".polyester": ("Polyester",     ROOT / "Polyester" / "EMJKaQ.lua",           "lune",     "Polyester dumper / env extractor"),
    ".promdeobf": ("PromDeobf",     ROOT / "PromDeobf" / "main.js",              "node",     "Prometheus deobfuscator (Node.js)"),
    ".promdeobf2":("PromDeobf2",    ROOT / "PromDeobf" / "main.js",              "node",     "Prometheus deobfuscator v2 (Node.js + luau-web)"),
    ".zala":      ("Zala",          ROOT / "zala-src-main" / "dumper.lua",       "lune",     "Zala dumper / server-based dumper"),
    ".oldlarry":  ("Old-Larry",     ROOT / "Old-larry" / "larry.lua",            "lune",     "Original Larry dumper"),
    ".moondeobf": ("MoonDeobf",     ROOT / "MoonDeobf" / "main.py",              "python",   "MoonSec deobfuscator (Python)"),
    ".larryv2":   ("Larry-v2",      ROOT / "Larry-v2" / "larry old src" / "dumper.luau", "lune", "Larry v2 dumper"),
    ".aspect":    ("Aspect-Native",  ROOT / "mods" / "aspect_native.luau",                "lune", "Aspect env dump (native Lune Roblox API)"),
    ".unveilkitty":("UnveilKitty",    "https://raw.githubusercontent.com/bbbbbbbbbbbbbb121/thee-bot/refs/heads/main/unveilr/main.luau", "lune_url", "UnveilKitty dumper (fetched from remote)"),
    ".decompiler":("LunaUX-Decompiler", ROOT / "tools" / "lunaux-decompiler" / "decompiler_run.py", "python313", "Luau bytecode decompiler (Python 3.13)"),
    ".disassembler":("LunaUX-Disassembler", ROOT / "tools" / "lunaux-decompiler" / "disassembler_run.py", "python313", "Luau bytecode disassembler (Python 3.13)"),
    ".devirtualize":("Static-VM-Devirt", ROOT / "tools" / "devirtualizer" / "devirtualize_run.py", "python313", "Static VM deobfuscator — dispatcher analysis, CFG, handler/register/table recovery, lifts to structured Luau"),
}

# ── obfuscator signature table (compiled once) ──────────────────────────────
# Keys mirror the folder names in github.com/terrorlua/obfuscator-samples so
# `.detect` can link straight to a labelled sample.  Patterns are folded into a
# single compiled regex per family (IGNORECASE) and scanned in one pass.
_OBF_SAMPLES: dict[str, tuple[str, str]] = {
    # key            display              signature
    "luraph":        ("Luraph",        r"does your environment support load/loadstring\?|_G\.LuraphContinue|Luraph Obfuscator|discord\.gg/(?:phosphor|luraph|7cG7qK)|LPH_RandInit|LPH_TESTS|LPH_STR|local v0=string\.char;local v1=string\.byte;local v2=string\.sub(?!;local v3=bit32)"),
    "moonsec":       ("MoonSec",       r"This file was protected with MoonSec|MoonSec V2|MoonSecV2|MoonSecV3|_MoonSec|MoonSec"),
    "prometheus":    ("Prometheus",    r"Prometheus|\bnewproxy\s*\("),
    "synapsexen":    ("SynapseXen",    r"SynapseXen"),
    "luaobfuscator": ("LuaObfuscator", r"LuaObfuscator|local v0=string\.char;local v1=string\.byte;local v2=string\.sub;local v3=bit32 or bit"),
    "ironbrew2":     ("IronBrew 2",    r"IronBrew2|Ironbrew2|IRONBREW2"),
    "ironbrew1":     ("Ironbrew 1",    r"Ironbrew1|IronBrew1|IRONBREW1"),
    "ironbrew3":     ("Ironbrew 3",    r"Ironbrew3|IronBrew3|IRONBREW3"),
    "moonveil":      ("MoonVeil",      r"MoonVeil|moonveil|MV_OPCODE"),
    "psu":           ("PSU",           r"\bPSU\b|psu_obfuscator|PSU Obfuscator"),
    "lps":           ("LPS",           r"\bLPS\b|LPS Obfuscator"),
    "boronide":      ("Boronide",      r"Boronide|boronide"),
    "hercules":      ("Hercules",      r"Hercules|hercules|HerculesVM"),
    "77fuscator":    ("77fuscator",    r"77fuscator|77Fuscator|SeventySeven"),
    "wynfuscate":    ("wYnFuscate",    r"wYnFuscate|wynfuscate"),
}
_OBF_SAMPLES_RE = {k: re.compile(p, re.IGNORECASE) for k, (_, p) in _OBF_SAMPLES.items()}


def _detect_obfuscators(code: str) -> list[str]:
    """Return sample-folder keys whose signature appears in ``code`` (ordered)."""
    return [k for k, rx in _OBF_SAMPLES_RE.items() if rx.search(code)]


def _run_alt_dumper(src_bytes: bytes, name: str, cmd_name: str) -> tuple[bool, bytes | str, float]:
    """Run one of the alternative dumpers by command name."""
    entry = _ALT_DUMPERS.get(cmd_name)
    if not entry:
        return False, f"unknown dumper: {cmd_name}", 0.0
    label, runner_script, runner, desc = entry
    stamp = _stamp()
    TMP.mkdir(exist_ok=True)
    in_rel  = f"bot_tmp/{stamp}_alt_in.lua"
    out_rel = f"bot_tmp/{stamp}_alt_out.lua"
    is_remote = runner == "lune_url"
    # Remote lune scripts (e.g. UnveilKitty) require ./mods and ./env relative to
    # the script's own directory, so they must live at the repo root, not bot_tmp.
    in_path  = ROOT / in_rel
    out_path = ROOT / out_rel
    script_path: pathlib.Path = None       # filled for remote runners (downloaded file)
    cleanup  = [in_path, out_path]         # files to always unlink in finally
    try:
        in_path.write_bytes(src_bytes)
        if is_remote:
            import aiohttp
            import asyncio
            script_path = ROOT / f"{stamp}_alt_runner.luau"
            cleanup.append(script_path)
            runner = "lune"
            url = entry[1]
            async def _fetch():
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        resp.raise_for_status()
                        script_path.write_bytes(await resp.read())
            try:
                loop = asyncio.get_event_loop()
                loop.run_until_complete(_fetch())
            except Exception:
                # fallback sync fetch
                import urllib.request
                with urllib.request.urlopen(url, timeout=30) as resp:
                    script_path.write_bytes(resp.read())

        if runner == "lune":
            cmd = [str(LUNE) if LUNE.exists() else "lune", "run",
                   str(script_path if is_remote else runner_script),
                   in_rel if is_remote else str(in_path)]
        elif runner == "python":
            cmd = [sys.executable, str(runner_script), str(in_path)]
        elif runner == "python313":
            cmd = [str(PY313), str(runner_script), str(in_path)]
        elif runner == "node":
            cmd = [str(_NODE), str(runner_script), str(in_path)]
        else:
            return False, f"unknown runner: {runner}", 0.0
        cmd.extend([f"out={out_rel}" if is_remote else str(out_path)])
        ok, log, took = _run_proc(cmd, cwd=ROOT)
        if ok and out_path.exists() and out_path.stat().st_size > 0:
            data = out_path.read_bytes()
            if is_remote and data.lstrip().startswith(b"--err"):
                reason = data.lstrip()[5:].strip().decode("utf-8", "ignore")[:300]
                return False, reason or f"{label} error", took
            return True, data, took
        if ok:
            # Some dumpers just print to stdout
            data = (log or "").encode("utf-8")
            if data.strip():
                return True, data, took
        log_lines = (log or "").strip().splitlines()
        filtered = [l for l in log_lines if not l.startswith("Node.js v")]
        msg = "\n".join(filtered[-5:]) if filtered else f"{label} produced no output"
        msg = msg[:500]
        return False, msg, took
    finally:
        for p in cleanup:
            try: p.unlink()
            except: pass


def _run_6vdumper(
    src_bytes: bytes,
    name: str,
    extra_args: list[str] | None = None,
) -> tuple[bool, bytes | str | dict, float]:
    """Run main.luau with version=1 (revea mode), rename vars rN → lolN.

    Returns (True, {"lua": bytes, "txt": bytes}, took) on success.
    """
    stamp = _stamp()
    TMP.mkdir(exist_ok=True)
    in_rel  = f"bot_tmp/{stamp}_6vd_in.lua"
    out_rel = f"bot_tmp/{stamp}_6vd_out.lua"
    in_path  = ROOT / in_rel
    out_path = ROOT / out_rel
    try:
        in_path.write_bytes(src_bytes)
        lune_bin = str(LUNE) if LUNE.exists() else "lune"
        cmd = [lune_bin, "run", "main.luau", in_rel, f"out={out_rel}", "version=1"]
        if extra_args:
            cmd.extend(extra_args)
        ok, log, took = _run_proc(cmd, cwd=ROOT)
        if out_path.exists() and out_path.stat().st_size > 0:
            text = out_path.read_text(errors="ignore")
            if text.lstrip().startswith("--err"):
                reason = text.lstrip()[5:].strip()
                return False, reason[:300] or "6vdumper error", took
            # Rename variables rN → lolN
            def _renumber(m):
                return f"lol{m.group(1)}"
            text = _RENUMBER_RE.sub(_renumber, text)
            lua_bytes = text.encode("utf-8")
            txt_str   = _make_source_table(text)
            return True, {"lua": lua_bytes, "txt": txt_str.encode("utf-8")}, took
        tail = (log or "").strip().splitlines()
        return False, (tail[-1] if tail else "6vdumper produced no output")[:300], took
    finally:
        for p in (in_path, out_path):
            try: p.unlink()
            except OSError: pass

# ── Lua Analysis Tools ─────────────────────────────────────────────────────────

_LUA_TOOLS = {
    "strings": ("String Dumper", "Extract all strings, constants, and VM opcodes", "📝", "strings"),
    "tracer":  ("Lua Tracer",    "Trace function calls, variables, and control flow", "🔍", "tracer"),
}

def _run_string_dumper(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    """Extract strings, constants, and VM opcodes from Lua source/bytecode."""
    top = time.time()
    code = src_bytes.decode("utf-8", errors="replace")
    lines = []
    lines.append("═" * 60)
    lines.append("  6Vms String Dumper — Static Analysis")
    lines.append("═" * 60)
    lines.append(f"  File: {name}")
    lines.append(f"  Size: {len(src_bytes)} bytes ({len(src_bytes)/1024:.1f} KB)")
    lines.append(f"  Lines: {code.count(chr(10)) + 1}")
    lines.append("")

    is_bytecode = code[:4].encode() == b"\x1bLua" or src_bytes[:4] == b"\x1bLua"
    if is_bytecode:
        lines.append("! Detected compiled Lua bytecode (header: \\x1bLua)")
        lines.append("  Try .unluac or .d → unluac Decompiler for decompilation.")
        lines.append("")

    # Extract string literals
    import re as _re
    strings_found = _re.findall(r"\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'", code)
    unique_strings = sorted({s[0] or s[1] for s in strings_found if s[0] or s[1]})

    lines.append(f"[ Strings Found: {len(unique_strings)} ]")
    lines.append("─" * 60)
    for i, s in enumerate(unique_strings, 1):
        if len(s) > 200:
            s = s[:197] + "..."
        decoded = ""
        try:
            import base64
            decoded_b64 = base64.b64decode(s).decode("utf-8", errors="replace")
            if decoded_b64 and len(decoded_b64) > 3:
                decoded = f"  → b64decode: {decoded_b64[:100]}"
        except Exception:
            pass
        if not decoded:
            try:
                decoded_hex = bytes.fromhex(s.replace("\\x", "")).decode("utf-8", errors="replace")
                if decoded_hex and len(decoded_hex) > 3:
                    decoded = f"  → hexdecode: {decoded_hex[:100]}"
            except Exception:
                pass
        if not decoded:
            try:
                if s.startswith("\\") and all(c in "0123456789" for c in s[1:]):
                    decoded = f"  → char: {chr(int(s[1:]))}"
            except Exception:
                pass
        display = s.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        lines.append(f"  {i:>4}. \"{display}\"")
        if decoded:
            lines.append(f"       {decoded}")

    # Extract number constants from char/byte patterns
    char_calls = _re.findall(r'string\.char\s*\(([^)]+)\)', code)
    if char_calls:
        lines.append("")
        lines.append(f"[ string.char() calls: {len(char_calls)} ]")
        lines.append("─" * 60)
        for i, call in enumerate(char_calls[:50], 1):
            lines.append(f"  {i:>4}. string.char({call[:120]})")

    # Detect potential VM opcodes
    opcode_patterns = _re.findall(r'(OP_?\w+|opcode|vmdispatch|instruction|LOP_\w+)', code)
    if opcode_patterns:
        unique_ops = sorted(set(opcode_patterns))
        lines.append("")
        lines.append(f"[ Potential VM Opcodes: {len(unique_ops)} ]")
        lines.append("─" * 60)
        for op in unique_ops:
            lines.append(f"  • {op}")

    # Detect common obfuscation patterns
    obf_patterns = []
    if _re.search(r'loadstring\(game:HttpGet', code) or _re.search(r'loadstring\(game:API', code):
        obf_patterns.append("Remote loader (loadstring + HttpGet)")
    if _re.search(r'\\x[0-9a-fA-F]{2}', code):
        obf_patterns.append("Hex-encoded strings")
    if _re.search(r'string\.byte|string\.char', code):
        obf_patterns.append("Byte/Char encoding")
    if _re.search(r'bit32|bit\.', code):
        obf_patterns.append("Bitwise operations (common in VM dispatch)")
    if _re.search(r"local [a-z]+ = \((?:function|\{)", code):
        obf_patterns.append("Complex VM dispatch table")
    if is_bytecode:
        obf_patterns.append("Compiled bytecode")
    if obf_patterns:
        lines.append("")
        lines.append("[ Detected Patterns ]")
        lines.append("─" * 60)
        for p in obf_patterns:
            lines.append(f"  ⚡ {p}")

    # Obfuscator identification
    obf_detect = _detect_obfuscators(code)
    if obf_detect:
        lines.append("")
        lines.append(f"[ {obf_detect} ]")

    lines.append("")
    lines.append("═" * 60)
    lines.append("  Analysis complete.")
    lines.append("═" * 60)

    took = time.time() - top
    return True, "\n".join(lines).encode("utf-8"), took


def _run_lua_tracer(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    """Trace Lua source: functions, globals, control flow, and variable patterns."""
    top = time.time()
    code = src_bytes.decode("utf-8", errors="replace")
    lines = []
    lines.append("═" * 60)
    lines.append("  6Vms Lua Tracer — Static Analysis")
    lines.append("═" * 60)
    lines.append(f"  File: {name}")
    lines.append(f"  Size: {len(src_bytes)} bytes")
    lines.append(f"  Lines: {code.count(chr(10)) + 1}")
    lines.append("")

    import re as _re

    # Trace function definitions
    local_funcs = _re.findall(r'local\s+function\s+(\w+)', code)
    global_funcs = _re.findall(r'^function\s+(\w+)', code, _re.MULTILINE)
    anon_funcs = _re.findall(r'=\s*function\s*\(', code)

    lines.append(f"[ Function Definitions: {len(local_funcs) + len(global_funcs) + len(anon_funcs)} ]")
    lines.append("─" * 60)
    for f in local_funcs:
        lines.append(f"  📦 local {f}")
    for f in global_funcs:
        lines.append(f"  🌐 {f}")
    if anon_funcs:
        lines.append(f"  🔒 {len(anon_funcs)} anonymous functions")

    # Trace global variable reads/writes
    global_writes = _re.findall(r'^(\w+)\s*=', code, _re.MULTILINE)
    global_reads = _re.findall(r'(?<!local\s)(?<!function\s)(\w+)\s*(?=[=(])', code)
    lines.append("")
    lines.append(f"[ Global Variables: {len(set(global_writes))} ]")
    lines.append("─" * 60)
    for g in sorted(set(global_writes)):
        lines.append(f"  ✏️  {g}")

    # Trace require / loadstring patterns
    loads = _re.findall(r'(loadstring|load|dofile|require)\s*\(', code)
    if loads:
        lines.append("")
        lines.append("[ Dynamic Code Loading ]")
        lines.append("─" * 60)
        for l in set(loads):
            lines.append(f"  📥 {l}()")

    # Trace HTTP requests
    http_calls = _re.findall(r'(game:HttpGet|game:HttpPost|syn\.request|http_request|request)\s*\(', code)
    if http_calls:
        lines.append("")
        lines.append("[ HTTP Requests ]")
        lines.append("─" * 60)
        for h in set(http_calls):
            lines.append(f"  🌐 {h}")

    # Trace metatable usage
    mt_usage = _re.findall(r'(setmetatable|getmetatable|__index|__newindex|__call|__tostring)', code)
    if mt_usage:
        lines.append("")
        lines.append("[ Metatable Operations ]")
        lines.append("─" * 60)
        for m in set(mt_usage):
            lines.append(f"  🔗 {m}")

    # Trace coroutine usage
    coro = _re.findall(r'(coroutine\.\w+|task\.\w+|spawn|delay)', code)
    if coro:
        lines.append("")
        lines.append("[ Concurrency / Threading ]")
        lines.append("─" * 60)
        for c in set(coro):
            lines.append(f"  🧵 {c}")

    # Trace control flow structures
    if_statements = code.count("if ") - code.count("end")
    for_loops = len(_re.findall(r'\bfor\b', code))
    while_loops = len(_re.findall(r'\bwhile\b', code))
    repeat_loops = len(_re.findall(r'\brepeat\b', code))
    lines.append("")
    lines.append("[ Control Flow Summary ]")
    lines.append("─" * 60)
    lines.append(f"  🔀 if/else: ~{max(0, if_statements)}")
    lines.append(f"  🔁 for loops: {for_loops}")
    lines.append(f"  🔁 while loops: {while_loops}")
    lines.append(f"  🔁 repeat loops: {repeat_loops}")

    # Trace environment access
    env_access = _re.findall(r'(getfenv|setfenv|getgenv|_G|ENV)', code)
    if env_access:
        lines.append("")
        lines.append("[ Environment Access ]")
        lines.append("─" * 60)
        for e in set(env_access):
            lines.append(f"  ⚙️  {e}")

    # Obfuscator identification
    obf_detect = _detect_obfuscators(code)
    if obf_detect:
        lines.append("")
        lines.append(f"[ {obf_detect} ]")

    lines.append("")
    lines.append("═" * 60)
    lines.append("  Trace complete.")
    lines.append("═" * 60)

    took = time.time() - top
    return True, "\n".join(lines).encode("utf-8"), took


def _run_lua_analysis(src_bytes: bytes, name: str) -> tuple[bool, bytes | str, float]:
    """Combined Lua Analysis: String Dumper + Lua Tracer."""
    top = time.time()
    ok_s, result_s, took_s = _run_string_dumper(src_bytes, name)
    ok_t, result_t, took_t = _run_lua_tracer(src_bytes, name)
    sd = result_s.decode("utf-8", errors="replace") if isinstance(result_s, bytes) else str(result_s)
    tr = result_t.decode("utf-8", errors="replace") if isinstance(result_t, bytes) else str(result_t)
    combined = sd + "\n\n" + tr
    took = time.time() - top
    return True, combined.encode("utf-8"), took


# ── Decoder utility functions ────────────────────────────────────────────────

def _xor_bruteforce(code: str) -> list[tuple[int, str, str]]:
    """Try 256 single-byte XOR keys on string.char(...) patterns, return (key, decoded, snippet)."""
    results = []
    for m in re.finditer(r'string\.char\s*\(([^)]+)\)', code):
        raw = m.group(1)
        try:
            nums = [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
        except ValueError:
            continue
        if not nums:
            continue
        best = None
        for key in range(256):
            decoded = "".join(chr(n ^ key) for n in nums if 32 <= (n ^ key) <= 126)
            if len(decoded) > len(nums) * 0.6:
                best = (key, decoded, raw[:60])
                break
        if best:
            results.append(best)
    return results


def _b64_extract(code: str) -> list[tuple[str, str]]:
    """Find and decode base64 strings, return (encoded, decoded)."""
    results = []
    for m in re.finditer(r'["\']([A-Za-z0-9+/=]{20,})["\']', code):
        enc = m.group(1)
        try:
            dec = base64.b64decode(enc).decode("utf-8", errors="replace")
            if len(dec) > 3:
                results.append((enc[:60], dec[:200]))
        except Exception:
            pass
    return results


def _hex_extract(code: str) -> list[str]:
    """Decode \\xNN hex escape sequences, return decoded strings."""
    results = []
    for m in re.finditer(r'["\']((?:\\x[0-9a-fA-F]{2})+)["\']', code):
        try:
            decoded = bytes.fromhex(m.group(1).replace("\\x", "")).decode("utf-8", errors="replace")
            if len(decoded) > 2:
                results.append(decoded[:200])
        except Exception:
            pass
    return results


def _nibble_decode(code: str) -> list[str]:
    """Decode IronBrew-style [A-P] nibble encoding."""
    results = []
    for m in re.finditer(r'["\']([A-P]{10,})["\']', code):
        enc = m.group(1)
        try:
            bytes_list = []
            for i in range(0, len(enc), 2):
                if i + 1 < len(enc):
                    b = ((ord(enc[i]) - 65) << 4) | (ord(enc[i + 1]) - 65)
                    bytes_list.append(b)
            decoded = bytes(bytes_list).decode("utf-8", errors="replace")
            if len(decoded) > 2:
                results.append(decoded[:200])
        except Exception:
            pass
    return results


def _loadstring_unwrap(code: str) -> list[tuple[str, str]]:
    """Extract loadstring payloads: base64, hex, or raw string."""
    results = []
    # base64 loadstring
    for m in re.finditer(r'loadstring\s*\(\s*["\']([A-Za-z0-9+/=]{50,})["\']', code):
        try:
            dec = base64.b64decode(m.group(1)).decode("utf-8", errors="replace")
            results.append(("b64", dec[:300]))
        except Exception:
            pass
    # hex loadstring
    for m in re.finditer(r'loadstring\s*\(\s*["\']((?:\\x[0-9a-fA-F]{2}){20,})["\']', code):
        try:
            dec = bytes.fromhex(m.group(1).replace("\\x", "")).decode("utf-8", errors="replace")
            results.append(("hex", dec[:300]))
        except Exception:
            pass
    return results


def _anti_tamper_strip(code: str) -> str:
    """Remove common anti-tamper patterns: getinfo, os.exit, infinite loops, crash triggers."""
    patterns = [
        (r'pcall\s*\(\s*debug\.getinfo\s*,[^)]+\)\s*', ''),
        (r'os\.exit\s*\([^)]*\)', '-- os.exit removed'),
        (r'while\s+true\s+do\s+end', '-- infinite loop removed'),
        (r'while\s+true\s+do\s*\n\s*end', '-- infinite loop removed'),
        (r'game:\w*\s*Shutdown\s*\([^)]*\)', '-- game:Shutdown removed'),
        (r'game\.Shutdown\s*\([^)]*\)', '-- game.Shutdown removed'),
    ]
    for pat, repl in patterns:
        code = re.sub(pat, repl, code)
    return code


def _universal_decode(code: str) -> str:
    """Chain all 5 decoders: char -> hex -> b64 -> nibble -> XOR."""
    lines = []
    lines.append("═" * 60)
    lines.append("  Universal Decoder — decoding all layers")
    lines.append("═" * 60)

    b64 = _b64_extract(code)
    if b64:
        lines.append(f"\n[ Base64 — {len(b64)} found ]")
        for enc, dec in b64[:10]:
            lines.append(f"  {enc[:40]}... → {dec[:80]}")

    hexd = _hex_extract(code)
    if hexd:
        lines.append(f"\n[ Hex escapes — {len(hexd)} found ]")
        for h in hexd[:10]:
            lines.append(f"  → {h[:80]}")

    nib = _nibble_decode(code)
    if nib:
        lines.append(f"\n[ Nibble encoding — {len(nib)} found ]")
        for n in nib[:10]:
            lines.append(f"  → {n[:80]}")

    xor = _xor_bruteforce(code)
    if xor:
        lines.append(f"\n[ XOR — {len(xor)} found ]")
        for key, dec, snippet in xor[:10]:
            lines.append(f"  key={key}: {dec[:80]}")

    ls = _loadstring_unwrap(code)
    if ls:
        lines.append(f"\n[ Loadstring payloads — {len(ls)} found ]")
        for kind, payload in ls[:5]:
            lines.append(f"  [{kind}] {payload[:120]}")

    if not any([b64, hexd, nib, xor, ls]):
        lines.append("\n  No encoded data detected.")

    lines.append("\n" + "═" * 60)
    return "\n".join(lines)


async def _nvidia_query(prompt: str, system: str = "", max_tokens: int = 16384) -> str:
    """Query NVIDIA API via OpenAI client (model from .aicfg panel)."""
    if not NVIDIA_API_KEY:
        return "NVIDIA API key not configured."
    try:
        client = openai.OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=NVIDIA_API_KEY,
        )
        loop = asyncio.get_event_loop()
        completion = await loop.run_in_executor(
            None,
            lambda: client.chat.completions.create(
                model=_ai_cfg["model"],
                messages=[
                    {"role": "system", "content": system or "You are a helpful AI assistant."},
                    {"role": "user", "content": prompt},
                ],
                temperature=_ai_cfg["temperature"],
                top_p=0.95,
                max_tokens=max_tokens or _ai_cfg["max_tokens"],
                extra_body={"chat_template_kwargs": {"thinking": _ai_cfg["thinking"]}},
                stream=False,
            ),
        )
        return completion.choices[0].message.content or ""
    except Exception as ex:
        return f"NVIDIA request failed: {ex}"


async def worker_loop(worker_id: int):
    """Multi-worker processing loop with health tracking."""
    await bot.wait_until_ready()
    WORKER_STATS[worker_id] = WorkerStats(worker_id=worker_id, started_at=time.time())
    while True:
        try:
            _priority, _job_stamp, job = await queue.get()
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(1)
            continue
        
        message, name = job["message"], job["name"]
        user_id = message.author.id
        dumper_type = job.get("dumper", "lune")
        stamp = _stamp()
        in_rel, out_rel = f"bot_tmp/{stamp}.lua", f"bot_tmp/{stamp}_out.lua"
        in_path, out_path = ROOT / in_rel, ROOT / out_rel
        
        # Update worker stats
        ws = WORKER_STATS[worker_id]
        ws.current_job = name
        ws.last_activity = time.time()
        job_start = time.time()
        
        await unreact(message, "🕓"); await react(message, "⏳")
        try:
            src_bytes = await fetch_source(job)
            
            # Source validation — only for URL sources, skip for file attachments
            source_url = job.get("url", "")
            if source_url and not job.get("att"):
                ok, reason = _validate_source_url(source_url)
                if not ok:
                    raise ValueError(reason)
            
            ok, reason = _check_size_limit(src_bytes, ENGINE_CFG.max_source_size, "Source")
            if not ok:
                raise ValueError(reason)
            
            TMP.mkdir(exist_ok=True)
            in_path.write_bytes(src_bytes)
            
            # Compute fingerprint and analysis
            fingerprint = _compute_fingerprint(src_bytes)
            analysis = _analyze_source(src_bytes.decode("utf-8", errors="ignore"))
            
            ok = False
            reason = "unknown error"
            data = None
            took = 0.0
            is_admin = _is_admin(message.author)
            max_to = ADMIN_TIMEOUT if is_admin else USER_TIMEOUT
            job_timeout = max_to

            # Route to appropriate dumper
            if dumper_type == "revea":
                ok, result, took = await asyncio.to_thread(_run_revea, src_bytes, name, _cfg_to_args(_get_cfg(message.channel.id)))
                if ok:
                    data = result.decode("utf-8", errors="ignore") if isinstance(result, bytes) else result
                    reason = None
                else:
                    reason = str(result) if result else "revea error"
            elif dumper_type == "6vdumper":
                ok, result, took = await asyncio.to_thread(_run_6vdumper, src_bytes, name, _cfg_to_args(_get_cfg(message.channel.id)))
                if ok and isinstance(result, dict):
                    lua_str = result["lua"].decode("utf-8", errors="ignore") if isinstance(result["lua"], bytes) else result["lua"]
                    txt_str = result["txt"].decode("utf-8", errors="ignore") if isinstance(result["txt"], bytes) else result["txt"]
                    lua_str = _stamp_output(lua_str)
                    txt_str = _stamp_output(txt_str)
                    lua_name = _rand_name("6vdumper.lua")
                    txt_name = _rand_name("6vdumper.txt")
                    raw_lua = await pastefy.upload(http, lua_name, lua_str)
                    raw_txt = await pastefy.upload(http, txt_name, txt_str)
                    e = discord.Embed(color=GOOD, timestamp=datetime.now())
                    e.description = (
                        f"**`{name}`**\n"
                        f"`{lua_str.count(chr(10))+1:,} lines` · `{len(lua_str)/1024:.1f} KB` · `{took:.2f}s`"
                    )
                    if raw_lua: e.description += f"\n{raw_lua}"
                    if raw_txt: e.description += f"\n{raw_txt}"
                    e.set_footer(text="6Vms")
                    tmp_lua = TMP / f"__6vdumper_lua_{_stamp()}.lua"
                    tmp_txt = TMP / f"__6vdumper_txt_{_stamp()}.txt"
                    tmp_lua.write_bytes(lua_str.encode())
                    tmp_txt.write_bytes(txt_str.encode())
                    try:
                        await message.reply(
                            content=message.author.mention, embed=e,
                            files=[discord.File(str(tmp_lua), filename=lua_name),
                                   discord.File(str(tmp_txt), filename=txt_name)],
                            mention_author=True,
                        )
                    finally:
                        try: tmp_lua.unlink()
                        except OSError: pass
                        try: tmp_txt.unlink()
                        except OSError: pass
                    await unreact(message, "⏳"); await react(message, "✅")
                    continue
                elif ok:
                    data = result.decode("utf-8", errors="ignore") if isinstance(result, bytes) else result
                    reason = None
                else:
                    reason = str(result) if result else "6vdumper error"
            elif dumper_type == "aspect":
                ok, result, took = await asyncio.to_thread(_run_aspect, src_bytes, name)
                if ok:
                    data = result.decode("utf-8", errors="ignore") if isinstance(result, bytes) else result
                    reason = None
                else:
                    reason = str(result) if result else "aspect error"
            elif dumper_type == "6vms":
                ch_cfg = _get_cfg(message.channel.id)
                cfg_args = _cfg_to_args(ch_cfg)
                runtime = ch_cfg.get("_runtime", "lune")
                job_timeout = min(int(ch_cfg.get("_timeout", 180)), max_to)
                ok, reason, took = await asyncio.to_thread(
                    _lune_dump, in_rel, out_rel, cfg_args, runtime, job_timeout, "6vms/main.luau"
                )
                if ok:
                    data = out_path.read_text(errors="ignore") if out_path.exists() else ""
                    if not data:
                        ok = False
                        reason = "engine produced empty output"
            else:
                ch_cfg = _get_cfg(message.channel.id)
                cfg_args = _cfg_to_args(ch_cfg)
                runtime = ch_cfg.get("_runtime", "lune")
                job_timeout = min(int(ch_cfg.get("_timeout", 180)), max_to)
                ok, reason, took = await asyncio.to_thread(
                    _lune_dump, in_rel, out_rel, cfg_args, runtime, job_timeout
                )
                if ok:
                    data = out_path.read_text(errors="ignore") if out_path.exists() else ""
                    if not data:
                        ok = False
                        reason = "engine produced empty output"

            # Apply redaction and output size check
            if ok and data:
                if ENGINE_CFG.enable_redaction:
                    data = _redact_secrets(data)
                data = _stamp_output(data)
                ok, reason = _check_size_limit(data.encode() if isinstance(data, str) else data, ENGINE_CFG.max_output_size, "Output")
                if not ok:
                    data = None
                    reason = "output too large"
            
            if ok and data:
                out_name = _sanitize_filename(_rand_name(f"{dumper_type}.lua"))
                raw_url = await pastefy.upload(http, out_name, data)
                e = discord.Embed(color=GOOD, timestamp=datetime.now())
                desc = (
                    f"**`{name}`**\n"
                    f"`{data.count(chr(10))+1:,} lines` · `{len(data)/1024:.1f} KB` · `{took:.2f}s`"
                )
                if ENGINE_CFG.enable_fingerprint and fingerprint:
                    desc += f"\n`SHA256: {fingerprint}`"
                if ENGINE_CFG.enable_analysis and analysis:
                    desc += f"\n`Funcs: {analysis.get('functions',0)} | URLs: {analysis.get('urls',0)} | Requires: {analysis.get('requires',0)} | Loadstrings: {analysis.get('loadstrings',0)}`"
                if raw_url: desc += f"\n{raw_url}"
                e.description = desc
                e.set_footer(text="6Vms")
                data_bytes = data.encode() if isinstance(data, str) else data
                tmp_file = TMP / f"__{dumper_type}_{_stamp()}.lua"
                tmp_file.write_bytes(data_bytes)
                try:
                    await message.reply(
                        content=message.author.mention, embed=e,
                        file=discord.File(str(tmp_file), filename=out_name),
                        mention_author=True,
                    )
                finally:
                    try: tmp_file.unlink()
                    except OSError: pass
                await unreact(message, "⏳"); await react(message, "✅")
                QUEUE_HEALTH["total_processed"] += 1
                ws.jobs_processed += 1
            else:
                if reason == "timeout":
                    label = f"skipped — took over {job_timeout}s"
                    colour = WARN
                    emoji = "⏱️"
                else:
                    label = _redact(str(reason)) if reason else "unknown error"
                    colour = BAD
                    emoji = "❌"
                e = discord.Embed(color=colour, timestamp=datetime.now())
                e.description = f"**`{name}`**\n{label}"
                e.set_footer(text="6Vms")
                await message.reply(content=message.author.mention, embed=e, mention_author=True)
                await unreact(message, "⏳"); await react(message, emoji)
                QUEUE_HEALTH["total_failed"] += 1
                ws.jobs_failed += 1

        except (KeyboardInterrupt, SystemExit):
            raise
        except asyncio.CancelledError:
            break
        except Exception as ex:
            print(f"[worker_{worker_id}] unhandled exception for {name!r}: {ex}")
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"**`{name}`**\ncouldn't grab that — {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            try: await message.reply(content=message.author.mention, embed=e, mention_author=True)
            except discord.HTTPException: pass
            await unreact(message, "⏳"); await react(message, "❌")
            QUEUE_HEALTH["total_failed"] += 1
            ws.jobs_failed += 1
        finally:
            _release_user_slot(user_id)
            ws.current_job = None
            ws.last_activity = time.time()
            ws.total_time += time.time() - job_start
            for p in (in_path, out_path):
                try: p.unlink()
                except OSError: pass
            queue.task_done()

async def _spawn_workers():
    """Spawn the configured number of workers."""
    for i in range(WORKER_COUNT):
        if i not in WORKER_POOL or WORKER_POOL[i].done():
            t = asyncio.create_task(worker_loop(i))
            t.set_name(f"worker_{i}")
            WORKER_POOL[i] = t

async def _shutdown_workers():
    """Gracefully shut down all workers."""
    for t in WORKER_POOL.values():
        if not t.done():
            t.cancel()
    await asyncio.gather(*WORKER_POOL.values(), return_exceptions=True)
    WORKER_POOL.clear()

# ── events ────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    global http
    if http is None:
        http = aiohttp.ClientSession()
    # Load persistent data
    _load_token_data()
    _load_blacklist_data()
    _load_give_cd()
    _load_keys()
    _load_tos()
    # Spawn worker pool (guarded against duplicate spawns on reconnect)
    await _spawn_workers()
    # Booster refund background task
    async def _booster_refund_loop():
        await bot.wait_until_ready()
        while not bot.is_closed():
            await asyncio.sleep(60)
            try:
                for guild in bot.guilds:
                    for member in guild.members:
                        if _has_role(member, BOOSTER_ROLE_ID):
                            _maybe_refund_booster(member)
            except: pass
        t2 = asyncio.create_task(_booster_refund_loop())
        t2.set_name("booster_refund")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, name="real skids love 6Vms"))
    print(f"[6Vms] online as {bot.user} · channels {CHANNEL_IDS}")

# ── TosView: one-time Terms of Service acceptance button ─────────────────────
class TosView(View):
    def __init__(self, author_id: int):
        super().__init__(timeout=180)
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    @discord.ui.button(label="✅ Accept Terms of Service", style=ButtonStyle.success)
    async def accept_btn(self, interaction: discord.Interaction, button: Button):
        _accept_tos(interaction.user.id)
        await interaction.response.send_message(
            "✅ **Terms accepted!** Re-run your command to use the bot.",
            ephemeral=True,
        )

# ── HelpView: paginated help embed with Previous/Next buttons ─────────────────
class HelpView(View):
    def __init__(self, author_id: int):
        super().__init__(timeout=180)  # 3 minutes
        self.author_id = author_id
        self.page = 0
        self.max_pages = 2

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except Exception:
            pass

    def _build_embed(self, page: int) -> discord.Embed:
        if page == 0:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.title = "6Vms — Command Reference (1/2)"
            e.description = (
                "Attach a **file**, paste a **raw URL**, or drop a **code block** with any command.\n"
                "Panel commands 🔎 open an interactive UI.\n\u200b"
            )
            e.add_field(name="📤 Dumpers", value=(
                "` .l` — Lune env dump (full static analysis)\n"
                "` .6vms` — 6Vms V2 env dump\n"
                "` .l2` — 6VDumper env dump (alternative dumper engine)\n"
                "` .r` — Revea env dump (environment extractor)\n"
                "` .r2` — Aspect env dump (better Revea, advanced sandbox)\n"
                "` .unveilr` — UnveilR env logger\n"
                "` .25ms` — 25ms full env dump (luraph_dumper.luau)"
            ), inline=False)
            e.add_field(name="🔎 Deobfuscators", value=(
                "` .d` 🔎 — panel: Luraph String Dumper · LPH V2 · Luraph Dumper · UnveilR\n"
                "\u2003\u2003\u2003\u2003\u2003"
                "LPH · Luraph · IronBrew2 · Prometheus/Moonsec · Prometheus/Moonsec V2\n"
                "\u2003\u2003\u2003\u2003\u2003"
                "MoonSec Disassembler · MoonSec Disassembly\n"
                "` .detect` — obfuscator detection (regex + 1xayd1 AI)\n"
                "` .relua` — universal Lua deobfuscation via RELUA API (1xayd1)\n"
                "` .relua2` — alternative RELUA backend (ngrok)\n"
                "` .lph` — AI-powered Luraph devirtualizer (deobfuscator.py)\n"
                "` .lphv2` — Luraph V2 anti-tamper / VM dumper (main.luau engine)\n"
                "` .lphv5` — 6Vms Luraph 14.7/14.8 decryptor (Roblox executor injector)\n"
                "` .luraphdeobf2` — static Luraph constant dumper (**premium**) — 6Vms luraph-constants.lua layout, payload never runs\n"
                "` .luarmor` — 6Vms Luarmor v1/v2/v3 HTTP logger (Roblox executor injector)\n"
                "` .luarmor2` — 6Vms Luarmor 2 logger/dumper (**premium**) — hooks loadstring/request/API + tutorial; loadstring via text\n"
                "` .funcdumper` — 6Vms Function Dumper (Roblox executor injector, `Funcdump(fn)`)\n"
                "` .simplespy` — 6Vms SimpleSpy RemoteSpy + tutorial (inject, logs RemoteEvents/Functions)\n"
                "` .status` — Queue & worker health dashboard\n"
                "` .relua2_cfg` — view / toggle relua2 settings\n"
                "` .unluac` — decompile Lua 5.1 bytecode → source (Java)\n"
                "` .ironveil` — deobfuscate IronVeil-obfuscated scripts (Node.js)\n"
                "` .d` — pick 77fuscator Deobfuscator (0.6.1 → Lua 5.1 .luac) from the menu"
            ), inline=False)
            e.set_footer(text="6Vms · discord.gg/XEP4KMaCVH · Page 1/2")
            return e
        else:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.title = "6Vms — Command Reference (2/2)"
            e.add_field(name="⚙️ Code Tools", value=(
                "` .darklua` — DarkLua panel (rules · generator · column span)\n"
                "` .minify` — dense minify via DarkLua\n"
                "` .beautify` — readable beautify via DarkLua\n"
                "` .rename` — contextual variable rename via API\n"
                "` .obf` — obfuscate Lua via Goofyscator API\n"
                "` .obf2` — Prometheus-style obfuscation (max security Goofyscator)\n"
                "` .obf_cfg` — Goofyscator config panel (toggles + generator selector)\n"
                "` .cfg` — Dumpers Config: toggle settings + pick `.l` runtime (LUNE/LUTE) + set timeout\n"
                "` .lua` — Lua Analysis: String Dumper + Lua Tracer\n"
                "` .convert` — Lua 5.1 → Luau syntax conversion"
            ), inline=False)
            e.add_field(name="🔓 Decoders", value=(
                "` .decode` — universal decoder (char + hex + b64 + nibble + XOR)\n"
                "` .xor` — XOR brute-force (tries 256 keys on string.char)\n"
                "` .b64` — base64 string finder & decoder\n"
                "` .hex` — hex escape (`\\xNN`) decoder\n"
                "` .anti` — anti-tamper remover (getinfo, os.exit, crashes)\n"
                "` .loadstring` — loadstring payload extractor (b64/hex/raw)\n"
                "` .analyze` — deep 6-point report + recommended tool\n"
                "` .diff` — unified diff between two attached scripts"
            ), inline=False)
            e.add_field(name="🤖 AI Tools", value=(
                "` .ai2 <prompt>` — Ollama AI chat (attach files for context)\n"
                "` .ai <prompt>` — Groq AI chat (attach files for context)\n"
                "` .explain` — AI explains what the code does\n"
                "` .fix` — AI fixes broken/deobfuscated code\n"
                "` .rewrite` — AI rewrites obfuscated code to clean form\n"
                "` .rename` — AI contextual variable renaming\n"
                "` .deep` — multi-pass: decode → strip → beautify → AI clean\n"
                "` .patch` — AI strips protections, watermarks, phone-home\n"
                "` .dump` — full forensic dump + AI summary\n"
                "` .scan` — security/malware scanner + AI analysis\n"
                "` .wat` — ultra-concise one-line AI summary\n"
                "` .ultra` — Aspect + 2× AI polish\n"
                "` .ultra2` — Aspect + String Dumper + VM scan → JSON report + AI\n"
                "` .deobf` — Aspect deobfuscation + AI logic summary"
            ), inline=False)
            e.add_field(name="🔬 Advanced Deobfuscation", value=(
                "` .vm` — VM bytecode devirtualizer (opcode lifting + AI)\n"
                "` .chain` — Aspect/r2 primary · fallback dumpers · AI picks best\n"
                "` .bulk` — batch up to 5 scripts through Aspect\n"
                "` .mega` — BETA · batch unlimited files through Aspect\n"
                "` .megalune` — Lune dump up to 3 files with inline `-- [[ logic ]]` annotations"
            ), inline=False)
            e.set_footer(text="6Vms · discord.gg/XEP4KMaCVH · Page 2/2")
            return e

    @discord.ui.button(label="⬅️ Previous", style=ButtonStyle.secondary, disabled=True)
    async def prev_btn(self, interaction: discord.Interaction, button: Button):
        self.page -= 1
        if self.page == 0:
            button.disabled = True
        self.children[1].disabled = False
        await interaction.response.edit_message(embed=self._build_embed(self.page), view=self)

    @discord.ui.button(label="Next ➡️", style=ButtonStyle.primary)
    async def next_btn(self, interaction: discord.Interaction, button: Button):
        self.page += 1
        if self.page == self.max_pages - 1:
            button.disabled = True
        self.children[0].disabled = False
        await interaction.response.edit_message(embed=self._build_embed(self.page), view=self)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    # Allow main channel and DMs
    if not (message.channel.id in CHANNEL_IDS or isinstance(message.channel, discord.DMChannel)):
        return

    content = message.content.strip()

    def starts(pfx):
        return content == pfx or content.lower().startswith(pfx + " ") or content.lower().startswith(pfx + "\n")

    first_word = content.split()[0].lower() if content else ""

    # ── blacklist check ──────────────────────────────────────────────────────
    bl_exp = _blacklist_expiry(message.author.id)
    if bl_exp:
        rem = int(bl_exp - time.time())
        e = discord.Embed(color=BAD, timestamp=datetime.now())
        e.description = f"🚫 You're blacklisted — expires in `{rem}s`"
        e.set_footer(text="6Vms")
        await message.reply(embed=e, mention_author=False, delete_after=10)
        return

    # ── Terms of Service gate: first-time free users must accept ───────────────
    if _tos_gated(message.author) and content.startswith(".") and not _has_accepted_tos(message.author.id):
        e = discord.Embed(color=ACCENT, timestamp=datetime.now())
        e.title = "📜 Terms of Service — required to use the bot"
        e.description = (
            "```diff\n"
            "+ hey, quick heads up — the bot just updated!\n"
            "\n"
            "+ [NEW] one-time Terms of Service acceptance\n"
            "- [REMOVED] status/invite promo requirement\n"
            "  (no more forcing invites onto your profile)\n"
            "\n"
            "+ You still get the same commands, same limits.\n"
            "+ Premium, boosters and admins are unaffected.\n"
            "```\n"
            "**What you're agreeing to:**\n"
            "```diff\n"
            "+ [ RULES] You own or have permission to deobfuscate the scripts you submit.\n"
            "+ [ RULES] The bot is for security research / personal use only.\n"
            "+ [ RULES] You may not resell, redistribute or claim the bot's output as your own.\n"
            "+ [ RULES] We are not responsible for how you use the results.\n"
            "+ [ RULES] Breaking these terms may result in a blacklist.\n"
            "```\n"
            "Press **Accept** below to continue — required **once**."
        )
        e.set_footer(text="6Vms · premium/boosters/admins are exempt")
        await message.reply(
            embed=e, view=TosView(message.author.id),
            mention_author=False, delete_after=180,
        )
        return

    # ── token check for paid commands ────────────────────────────────────────
    if first_word in TOKEN_COMMANDS and not _is_premium(message.author):
        if not _spend_token(message.author.id):
            e = discord.Embed(color=WARN, timestamp=datetime.now())
            e.description = "⏳ No tokens left — wait up to 30 minutes for a refill"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False, delete_after=15)
            return

    # ── .give  — premium token gifting ──────────────────────────────────────
    if starts(".give"):
        if not _is_premium(message.author):
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = "Only premium users can use `.give`"
            await message.reply(embed=e, mention_author=False)
            return
        uid = str(message.author.id)
        now = time.time()
        cd = _give_cd_store.get(uid, 0)
        if now < cd:
            rem = int(cd - now)
            e = discord.Embed(color=WARN, timestamp=datetime.now())
            e.description = f"⏳ `.give` cooldown — wait `{rem}s`"
            await message.reply(embed=e, mention_author=False, delete_after=10)
            return
        parts = content.split()
        if len(parts) < 3 or not message.mentions:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Usage: `.give @user <amount>` (max 4)"
            await message.reply(embed=e, mention_author=False)
            return
        try:
            amount = int(parts[-1])
        except ValueError:
            amount = 1
        amount = max(1, min(amount, GIVE_MAX))
        target = message.mentions[0]
        if target.bot:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = "Can't give tokens to bots"
            await message.reply(embed=e, mention_author=False)
            return
        # Check target won't exceed max
        t_uid = str(target.id)
        t_e = _token_store.get(t_uid, {})
        t_now = time.time()
        if t_now - t_e.get("reset_at", 0) >= TOKEN_RESET_INTERVAL:
            t_e["tokens"] = TOKENS_PER_RESET; t_e["reset_at"] = t_now
        current = t_e.get("tokens", TOKENS_PER_RESET)
        if current + amount > MAX_TOKENS:
            e = discord.Embed(color=WARN, timestamp=datetime.now())
            e.description = f"Target would exceed `{MAX_TOKENS}` tokens — gift cancelled (your tokens refunded)"
            await message.reply(embed=e, mention_author=False)
            return
        # Apply
        t_e["tokens"] = current + amount
        _token_store[t_uid] = t_e
        _save_token_data()
        _give_cd_store[uid] = now + GIVE_COOLDOWN
        _save_give_cd()
        e = discord.Embed(color=GOOD, timestamp=datetime.now())
        e.description = f"✅ Gave `{amount}` token(s) to {target.mention}"
        await message.reply(embed=e, mention_author=False)
        return

    # ── .whitelist — grant premium (multi-user, with optional duration) ────
    if starts(".whitelist"):
        if not _is_admin(message.author):
            e = discord.Embed(color=BAD); e.description = "Admin only"
            await message.reply(embed=e, mention_author=False); return
        parts = content.split()
        mentions = message.mentions
        if not mentions:
            e = discord.Embed(color=ACCENT); e.description = "Usage: `.whitelist @user1 @user2 ... [duration]`\ne.g. `.whitelist @user 7d` / `perm`"
            await message.reply(embed=e, mention_author=False); return

        dur = None
        dur_str = parts[-1]
        if not dur_str.startswith("<@") and not dur_str.startswith("<!"):
            dur_sec = _parse_duration(dur_str)
            if dur_sec is not None or dur_str.lower() in ("perm", "permanent", "inf", "infinity", "forever", "0"):
                dur = dur_sec
                mentions = [m for m in mentions if str(m.id) != dur_str]
        if not mentions:
            e = discord.Embed(color=ACCENT); e.description = "No valid users mentioned."
            await message.reply(embed=e, mention_author=False); return

        results = []
        for target in mentions:
            uid = str(target.id)
            dur_label = "permanent" if dur is None else f"{dur}s"
            try:
                role = message.guild.get_role(PREMIUM_ROLE_ID) if message.guild else None
                if role and isinstance(target, discord.Member):
                    await target.add_roles(role, reason="Whitelisted")
            except Exception:
                pass
            results.append(f"✅ {target.mention} — premium ({dur_label})")
        e = discord.Embed(color=GOOD); e.description = "\n".join(results)
        await message.reply(embed=e, mention_author=False)
        return

    # ── .revoke — remove premium ────────────────────────────────────────────
    if starts(".revoke"):
        if not _is_admin(message.author):
            e = discord.Embed(color=BAD); e.description = "Admin only"
            await message.reply(embed=e, mention_author=False); return
        if not message.mentions:
            e = discord.Embed(color=ACCENT); e.description = "Usage: `.revoke @user`"
            await message.reply(embed=e, mention_author=False); return
        results = []
        for target in message.mentions:
            uid = str(target.id)
            try:
                role = message.guild.get_role(PREMIUM_ROLE_ID) if message.guild else None
                if role and isinstance(target, discord.Member):
                    await target.remove_roles(role, reason="Revoked")
            except Exception:
                pass
            results.append(f"✅ Premium revoked for {target.mention}")
        e = discord.Embed(color=GOOD); e.description = "\n".join(results)
        await message.reply(embed=e, mention_author=False)
        return

    # ── .genkey — generate redeem keys (owner only) ────────────────────────
    if starts(".genkey"):
        if not _is_owner(message.author):
            e = discord.Embed(color=BAD); e.description = "Owner only"
            await message.reply(embed=e, mention_author=False); return
        parts = content.split()
        if len(parts) < 2:
            e = discord.Embed(color=ACCENT); e.description = "Usage:\n`.genkey token <keys_amount> <token_amount>` — e.g. `.genkey token 5 50`\n`.genkey premium <dur> [count]` — e.g. `.genkey premium 7d 10`"
            await message.reply(embed=e, mention_author=False); return
        kind = parts[1].lower()
        count = 1
        if kind == "token":
            if len(parts) < 4:
                e = discord.Embed(color=ACCENT); e.description = "Usage: `.genkey token <keys_amount> <token_amount>`\nMax 100 tokens per key.\nExample: `.genkey token 5 50` (5 keys, 50 tokens each)"
                await message.reply(embed=e, mention_author=False); return
            try:
                count = max(1, int(parts[2]))
                token_amt = max(1, min(100, int(parts[3])))
            except ValueError:
                e = discord.Embed(color=BAD); e.description = "Invalid amount. Usage: `.genkey token <keys_amount> <token_amount>`"
                await message.reply(embed=e, mention_author=False); return
            keys = [_gen_key(token_amt, None, "token") for _ in range(count)]
            lines = [f"🔑 **Token Keys ({count})**\nTokens per key: `{token_amt}` (max 100)"]
            for i, k in enumerate(keys, 1):
                lines.append(f"`{i:>3}.` `{k}`")
            msg = "\n".join(lines)
        elif kind == "premium":
            if len(parts) < 3:
                e = discord.Embed(color=ACCENT); e.description = "Usage: `.genkey premium <duration> [count]`"
                await message.reply(embed=e, mention_author=False); return
            dur = _parse_duration(parts[2])
            if len(parts) > 3:
                try: count = max(1, int(parts[3]))
                except: pass
            dur_label = "permanent" if dur is None else f"{dur}s"
            keys = [_gen_key(0, dur, "premium") for _ in range(count)]
            lines = [f"🔑 **Premium Keys ({count})**\nDuration: `{dur_label}`"]
            for i, k in enumerate(keys, 1):
                lines.append(f"`{i:>3}.` `{k}`")
            msg = "\n".join(lines)
            if count >= 5:
                key_list = "\n".join(keys)
                try:
                    (ROOT / "premium_keys.txt").write_text(key_list)
                except Exception:
                    pass
        else:
            e = discord.Embed(color=BAD); e.description = "Use `token` or `premium`."
            await message.reply(embed=e, mention_author=False); return
        try:
            if len(msg) > 1900:
                out = ROOT / f"keys_{_stamp()}.txt"
                out.write_text(msg)
                await message.author.send(file=discord.File(str(out)))
                try: out.unlink()
                except: pass
            else:
                await message.author.send(msg)
            sent = f"✅ `{count}` key(s) sent to your DMs"
            if count > 25:
                sent += f" (also saved to `premium_keys.txt`)"
            e = discord.Embed(color=GOOD); e.description = sent
            await message.reply(embed=e, mention_author=False)
        except discord.Forbidden:
            e = discord.Embed(color=WARN); e.description = f"❌ Couldn't DM you — keys dropped in #console"
            await message.reply(embed=e, mention_author=False)
        return

    # ── .redeem — redeem a key ─────────────────────────────────────────────
    if starts(".redeem"):
        parts = content.split()
        if len(parts) < 2:
            e = discord.Embed(color=ACCENT); e.description = "Usage: `.redeem <key>`"
            await message.reply(embed=e, mention_author=False); return
        key = parts[1].strip().upper()
        err = _redeem_key(key, str(message.author.id))
        if err:
            e = discord.Embed(color=BAD); e.description = f"❌ {err}"
            await message.reply(embed=e, mention_author=False)
        else:
            k = _key_store.get(key, {})
            dur_label = "permanent" if k.get("duration_sec") is None else f"{k['duration_sec']}s"
            is_premium = k.get("type") == "premium"
            tokens_line = "" if is_premium else f"Tokens: `{k.get('tokens', 0)}`\n"
            e = discord.Embed(color=GOOD); e.description = (
                f"✅ **Key redeemed!**\n"
                f"{tokens_line}"
                f"Premium duration: `{dur_label}`"
            )
            await message.reply(embed=e, mention_author=False)
            try:
                role = message.guild.get_role(PREMIUM_ROLE_ID) if message.guild else None
                if role and isinstance(message.author, discord.Member):
                    await message.author.add_roles(role, reason="Key redeemed")
            except Exception:
                pass
        return

    # ── .blacklist — ban user from bot ──────────────────────────────────────
    if starts(".blacklist"):
        if not _is_admin(message.author):
            e = discord.Embed(color=BAD); e.description = "Admin only"
            await message.reply(embed=e, mention_author=False); return
        parts = content.split()
        if not message.mentions:
            e = discord.Embed(color=ACCENT); e.description = "Usage: `.blacklist @user <seconds>` (max 3600)"
            await message.reply(embed=e, mention_author=False); return
        try:
            dur = min(int(parts[-1]), BLACKLIST_MAX) if len(parts) > 2 else 3600
        except ValueError:
            dur = 3600
        uid = str(message.mentions[0].id)
        _blacklist_store[uid] = time.time() + dur
        _save_blacklist_data()
        e = discord.Embed(color=WARN)
        e.description = f"🚫 {message.mentions[0].mention} blacklisted for `{dur}s`"
        await message.reply(embed=e, mention_author=False)
        return

    # ── .darklua  — DarkLua interactive panel ────────────────────────────────
    if starts(".darklua"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.darklua`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"Couldn't fetch `{job['name']}` — {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓")
        panel = DarkluaPanel(src_bytes, job["name"], message)
        panel_msg = await message.reply(embed=panel.build_embed(), view=panel, mention_author=False)
        panel.message = panel_msg
        return

    # ── .cfg  — 25ms settings panel ──────────────────────────────────────────
    if starts(".cfg"):
        panel = CfgPanel(message.channel.id)
        panel_msg = await message.reply(embed=panel.build_embed(), view=panel, mention_author=False)
        panel.message = panel_msg
        return

    # ── .restart  — graceful bot restart ─────────────────────────────────────
    if starts(".restart"):
        if message.author.id != 1409833451049324584:
            await message.reply("no", mention_author=False)
            return
        e = discord.Embed(color=WARN, timestamp=datetime.now())
        e.description = "🔄 Restarting bot..."
        e.set_footer(text="6Vms")
        await message.reply(embed=e, mention_author=False)
        try:
            await bot.close()
        except Exception:
            pass
        os._exit(42)

    # ── .d  — deobfuscator panel ──────────────────────────────────────────────
    if starts(".d"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = (
                "Attach a `.lua`/`.txt` file or paste a raw URL after `.d`.\n"
                "I'll show you a menu to pick the deobfuscator."
            )
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return

        # Only handle first job (one panel at a time)
        job = jobs[0]
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"Couldn't fetch `{job['name']}` — {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "🕓")
            return

        await unreact(message, "🕓")
        view = DeobfPanel(src_bytes, job["name"], message)
        e = discord.Embed(color=ACCENT, timestamp=datetime.now())
        e.description = (
            f"**`{job['name']}`** ready — `{len(src_bytes)/1024:.1f} KB`\n"
            "Pick a deobfuscator below:"
        )
        e.set_footer(text="6Vms · panel expires in 2 min")
        panel_msg = await message.reply(embed=e, view=view, mention_author=False)
        view.message = panel_msg
        return

    # ── .6vms  — 6Vms V2 environment dumper ────────────────────────────────────
    if starts(".6vms"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt`, drop a raw link, or reply to one with `.6vms`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        await react(message, "🕓")
        for j in jobs:
            j["dumper"] = "6vms"
            ahead = _queue_put(j, message)
            if isinstance(ahead, str):
                await message.reply(f"❌ {ahead}", mention_author=False, delete_after=10)
                return
        note = f"queued `{len(jobs)}`" + (f" · `{ahead}` ahead" if ahead else "")
        try: await message.reply(note, mention_author=False, delete_after=6)
        except discord.HTTPException: pass
        return

    # ── .lua  — Lua Analysis: String Dumper + Lua Tracer ──────────────────────
    if starts(".lua"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = (
                "Attach a `.lua`/`.txt`, drop a raw link, or reply to one with `.lua`.\n"
                "🔎 Lua Analysis: String Dumper (extract strings/opcodes) + Lua Tracer (trace functions/globals)"
            )
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"Couldn't fetch `{job['name']}` — {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓"); await react(message, "⏳")
        try:
            ok, result, took = await asyncio.to_thread(_run_lua_analysis, src_bytes, job["name"])
            if ok:
                data_str   = result.decode() if isinstance(result, bytes) else result
                data_bytes = data_str.encode()
                out_fname  = _rand_name("analysis.txt")
                raw_url    = await pastefy.upload(http, out_fname, data_str)
                e = discord.Embed(color=GOOD, timestamp=datetime.now())
                e.description = (
                    f"🔎 **Lua Analysis** — `{job['name']}`\n"
                    f"`{data_str.count(chr(10))+1:,} lines` · `{len(data_bytes)/1024:.1f} KB` · `{took:.2f}s`"
                )
                if raw_url: e.description += f"\n{raw_url}"
                e.set_footer(text="6Vms")
                tmp = TMP / f"__lua_analysis_{_stamp()}.txt"
                tmp.write_bytes(data_bytes)
                try:
                    await message.reply(content=message.author.mention, embed=e,
                                        file=discord.File(str(tmp), filename=out_fname), mention_author=True)
                finally:
                    try: tmp.unlink()
                    except OSError: pass
                await unreact(message, "⏳"); await react(message, "✅")
            else:
                err = str(result)[:300]
                e = discord.Embed(color=BAD, timestamp=datetime.now())
                e.description = f"🔎 **Lua Analysis** — `{job['name']}`\n```\n{err}\n```"
                e.set_footer(text="6Vms")
                await message.reply(embed=e, mention_author=False)
                await unreact(message, "⏳"); await react(message, "❌")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "⏳"); await react(message, "❌")
        return

    # ── .decode  — Universal Decoder (char + hex + b64 + nibble + XOR) ──────
    if starts(".decode"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.decode`.\nUniversal decoder — tries all encoding layers."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src = (await fetch_source(job)).decode("utf-8", errors="replace")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        result = _universal_decode(src)
        out = TMP / f"{_stamp()}_decode.txt"
        out.write_text(result)
        try:
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"🔓 **Decode** — `{job['name']}`\n`{result.count(chr(10))+1} lines`"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(out), filename="decoded.txt"), mention_author=True)
        finally:
            try: out.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .xor  — XOR brute-force decoder ─────────────────────────────────────
    if starts(".xor"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.xor`.\nXOR brute-force — tries 256 keys on string.char patterns."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src = (await fetch_source(job)).decode("utf-8", errors="replace")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        xor = _xor_bruteforce(src)
        if xor:
            lines = [f"key={k}: {d}" for k, d, _ in xor]
            out = "\n".join(lines)
        else:
            out = "No XOR-encoded string.char patterns found."
        tmp = TMP / f"{_stamp()}_xor.txt"
        tmp.write_text(out)
        try:
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"🔑 **XOR** — `{job['name']}` — `{len(xor)}` keys found"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="xor.txt"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .b64  — Base64 finder & decoder ─────────────────────────────────────
    if starts(".b64"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.b64`.\nBase64 string finder & decoder."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src = (await fetch_source(job)).decode("utf-8", errors="replace")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        b64 = _b64_extract(src)
        if b64:
            lines = [f"{e} → {d}" for e, d in b64]
            out = "\n".join(lines)
        else:
            out = "No base64 strings found."
        tmp = TMP / f"{_stamp()}_b64.txt"
        tmp.write_text(out)
        try:
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"🔤 **Base64** — `{job['name']}` — `{len(b64)}` decoded"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="b64.txt"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .hex  — Hex escape decoder ──────────────────────────────────────────
    if starts(".hex"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.hex`.\nDecodes \\xNN hex escape sequences."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src = (await fetch_source(job)).decode("utf-8", errors="replace")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        hexd = _hex_extract(src)
        if hexd:
            out = "\n".join(hexd)
        else:
            out = "No hex-encoded strings found."
        tmp = TMP / f"{_stamp()}_hex.txt"
        tmp.write_text(out)
        try:
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"#️⃣ **Hex** — `{job['name']}` — `{len(hexd)}` decoded"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="hex.txt"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .anti  — Anti-tamper pattern remover ────────────────────────────────
    if starts(".anti"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.anti`.\nRemoves getinfo, os.exit, infinite loops, crash triggers."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src = (await fetch_source(job)).decode("utf-8", errors="replace")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        cleaned = _anti_tamper_strip(src)
        tmp = TMP / f"{_stamp()}_anti.lua"
        tmp.write_text(cleaned)
        try:
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            stripped = len(src) - len(cleaned)
            e.description = f"🛡️ **Anti-Tamper** — `{job['name']}` — removed `{stripped}` bytes"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="cleaned.lua"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .loadstring  — Loadstring payload extractor ─────────────────────────
    if starts(".loadstring"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.loadstring`.\nExtracts loadstring payloads (base64/hex/raw)."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src = (await fetch_source(job)).decode("utf-8", errors="replace")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        ls = _loadstring_unwrap(src)
        if ls:
            lines = [f"[{kind}]\n{payload}" for kind, payload in ls]
            out = "\n\n".join(lines)
        else:
            out = "No loadstring payloads detected."
        tmp = TMP / f"{_stamp()}_loadstring.txt"
        tmp.write_text(out)
        try:
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"📦 **Loadstring** — `{job['name']}` — `{len(ls)}` payloads"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="loadstring.txt"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .diff  — Compare two attached scripts ───────────────────────────────
    if starts(".diff"):
        jobs = await gather_jobs(message)
        if len(jobs) < 2:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach **two** `.lua`/`.txt` files, or reply to two messages with `.diff`.\nUnified diff between the two scripts."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        await react(message, "🕓")
        try:
            src_a = (await fetch_source(jobs[0])).decode("utf-8", errors="replace")
            src_b = (await fetch_source(jobs[1])).decode("utf-8", errors="replace")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        import difflib
        diff = difflib.unified_diff(src_a.splitlines(keepends=True), src_b.splitlines(keepends=True), fromfile=jobs[0]["name"], tofile=jobs[1]["name"])
        out = "".join(diff)
        if not out.strip():
            out = "Files are identical."
        tmp = TMP / f"{_stamp()}_diff.txt"
        tmp.write_text(out)
        try:
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"📊 **Diff** — `{jobs[0]['name']}` vs `{jobs[1]['name']}`"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="diff.txt"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .convert  — Lua 5.1 ↔ Luau converter ────────────────────────────────
    if starts(".convert"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.convert`.\nConverts between Lua 5.1 and Luau syntax."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src = (await fetch_source(job)).decode("utf-8", errors="replace")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        converted = src
        converted = re.sub(r'local\s+([a-zA-Z_]\w*)\s*=\s*function\s*\(', r'local function \1(', converted)
        converted = converted.replace("~=", "~=") if "~=" in converted else converted.replace("~=", "~=")
        if "-- Luau" in src or "::" in src:
            converted = re.sub(r'(\w+)\s*::\s*\w+', r'\1', converted)
            converted = re.sub(r'export\s+type\s+\w+\s*=', '', converted)
        tmp = TMP / f"{_stamp()}_converted.lua"
        tmp.write_text(converted)
        try:
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"🔄 **Convert** — `{job['name']}` — `{len(converted)}` bytes"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="converted.lua"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .analyze  — Deeper analysis report ───────────────────────────────────
    if starts(".analyze"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.analyze`.\nDeep 6-point analysis report + recommended tool."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src = (await fetch_source(job)).decode("utf-8", errors="replace")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        name = job["name"]
        lines = []
        lines.append("═" * 60)
        lines.append(f"  Analysis Report — {name}")
        lines.append("═" * 60)
        lines.append(f"  Size: {len(src)} bytes")
        lines.append(f"  Lines: {src.count(chr(10)) + 1}")
        lines.append("")
        s = _run_string_dumper(src.encode(), name)
        sd = s[1].decode() if isinstance(s[1], bytes) else str(s[1])
        t = _run_lua_tracer(src.encode(), name)
        tr = t[1].decode() if isinstance(t[1], bytes) else str(t[1])
        lines.append(sd)
        lines.append("")
        lines.append(tr)
        b64 = _b64_extract(src)
        if b64:
            lines.append(f"\n[ Base64: {len(b64)} strings ]")
        xor = _xor_bruteforce(src)
        if xor:
            lines.append(f"[ XOR: {len(xor)} keys found ]")
        ls = _loadstring_unwrap(src)
        if ls:
            lines.append(f"[ Loadstring: {len(ls)} payloads ]")

        obf = _detect_obfuscators(src)
        if obf:
            lines.append(f"\n[ Detection ]\n  {obf}")

        lines.append("\n[ Recommended Tool ]")
        if "Luraph" in obf:
            lines.append("  → .lph (AI Luraph devirtualizer)")
        elif "MoonSec" in obf:
            lines.append("  → .d → MoonSec Disassembler")
        elif "IronBrew" in obf:
            lines.append("  → .d → IronBrew2")
        elif "Prometheus" in obf:
            lines.append("  → .promdeobf or .promdeobf2")
        else:
            lines.append("  → .d deobfuscator panel, or .decode / .anti")

        lines.append("\n" + "═" * 60)
        out = "\n".join(lines)
        tmp = TMP / f"{_stamp()}_analyze.txt"
        tmp.write_text(out)
        try:
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"📋 **Analysis** — `{name}` — `{len(out)}` chars"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="analysis.txt"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── AI-powered commands (Ollama) ─────────────────────────────────────────
    # .explain, .fix, .rewrite, .wat, .dump, .scan, .patch, .deep, .ai2

    if starts(".explain"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.explain`.\nAI explains what the code does."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src = (await fetch_source(job)).decode("utf-8", errors="replace")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        reply = await _ollama_query(f"Explain this Lua code concisely, highlighting obfuscation, purpose, and key techniques:\n\n{src[:4000]}", system="You are a Lua deobfuscation expert. Explain clearly and concisely.", max_tokens=1000)
        if len(reply) > 1900:
            raw_url = await pastefy.upload(http, "explain.txt", reply)
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"💡 **Explain** — `{job['name']}`\n{raw_url}" if raw_url else f"💡 **Explain** — too long"
        else:
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"💡 **Explain** — `{job['name']}`\n{reply}"
        e.set_footer(text="6Vms")
        await message.reply(content=message.author.mention, embed=e, mention_author=True)
        await unreact(message, "⏳"); await react(message, "✅")
        return

    if starts(".fix"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.fix`.\nAI attempts to fix broken/deobfuscated code."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src = (await fetch_source(job)).decode("utf-8", errors="replace")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        reply = await _ollama_query(f"Fix this Lua code. Remove errors, fix syntax, complete missing parts. Return ONLY the fixed code without markdown fences:\n\n{src[:6000]}", system="You are a Lua code repair expert. Fix syntax errors, complete broken patterns, return clean working code only.", max_tokens=4096)
        tmp = TMP / f"{_stamp()}_fix.lua"
        reply_clean = re.sub(r'^```lua\s*|^```\s*|```\s*$', '', reply, flags=re.MULTILINE).strip()
        tmp.write_text(reply_clean)
        try:
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"🔧 **Fix** — `{job['name']}` — `{len(reply_clean)}` bytes"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="fixed.lua"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    if starts(".rewrite"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.rewrite`.\nAI rewrites obfuscated code to clean readable form."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src = (await fetch_source(job)).decode("utf-8", errors="replace")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        reply = await _ollama_query(f"Rewrite this obfuscated Lua code to clean, readable form. Deobfuscate variable names, simplify control flow. Return ONLY the clean code:\n\n{src[:4000]}", system="You are a Lua deobfuscation specialist. Return clean, readable, well-named code only.", max_tokens=4096)
        reply_clean = re.sub(r'^```lua\s*|^```\s*|```\s*$', '', reply, flags=re.MULTILINE).strip()
        tmp = TMP / f"{_stamp()}_rewritten.lua"
        tmp.write_text(reply_clean)
        try:
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"✏️ **Rewrite** — `{job['name']}` — `{len(reply_clean)}` bytes"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="rewritten.lua"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    if starts(".wat"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.wat`.\nUltra-concise one-line AI summary."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src = (await fetch_source(job)).decode("utf-8", errors="replace")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        reply = await _ollama_query(f"Summarize this Lua code in ONE concise sentence (max 200 chars):\n\n{src[:2000]}", system="You are a Lua expert. Respond with a single short sentence only.", max_tokens=100)
        e = discord.Embed(color=GOOD, timestamp=datetime.now())
        e.description = f"❓ **Wat** — `{job['name']}`\n{reply[:500]}"
        e.set_footer(text="6Vms")
        await message.reply(content=message.author.mention, embed=e, mention_author=True)
        await unreact(message, "⏳"); await react(message, "✅")
        return

    if starts(".dump"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.dump`.\nFull forensic dump: strings, functions, services, loadstring payloads + AI summary."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src = (await fetch_source(job)).decode("utf-8", errors="replace")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        sd = _run_string_dumper(src.encode(), job["name"])
        sd_text = sd[1].decode() if isinstance(sd[1], bytes) else str(sd[1])
        tr = _run_lua_tracer(src.encode(), job["name"])
        tr_text = tr[1].decode() if isinstance(tr[1], bytes) else str(tr[1])
        ai = await _ollama_query(f"Analyze this Lua script for forensics: identify obfuscation, purpose, hidden behavior, network calls, and any suspicious patterns:\n\n{src[:3000]}", system="You are a Lua forensics expert. Provide concise analysis.", max_tokens=1000)
        lines = [sd_text, "", tr_text, "", "═" * 60, "  AI Forensics Summary", "═" * 60, "", ai]
        out = "\n".join(lines)
        tmp = TMP / f"{_stamp()}_dump.txt"
        tmp.write_text(out)
        try:
            raw_url = await pastefy.upload(http, "dump.txt", out)
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"🗂️ **Dump** — `{job['name']}` — `{len(out)}` chars"
            if raw_url: e.description += f"\n{raw_url}"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="dump.txt"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    if starts(".scan"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.scan`.\nSecurity/malware scanner — backdoors, data exfil, persistence."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src = (await fetch_source(job)).decode("utf-8", errors="replace")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        threats = []
        if re.search(r'game:HttpGet\s*\(|game:HttpPost\s*\(', src):
            threats.append("🌐 HTTP requests (possible data exfil)")
        if re.search(r'writefile|write_file|savestring', src):
            threats.append("💾 File write operations")
        if re.search(r'loadstring\s*\(', src):
            threats.append("📦 Dynamic code loading (loadstring)")
        if re.search(r'syn\.request|http_request|request\s*\(', src):
            threats.append("📡 External request API")
        if re.search(r'getfenv|setfenv|getgenv', src):
            threats.append("⚙️ Environment manipulation")
        if re.search(r'os\.execute|io\.popen', src):
            threats.append("🚨 Shell execution")
        if re.search(r'Instance\.new\s*\(\s*["\']RemoteEvent|["\']RemoteFunction', src):
            threats.append("🔌 Remote event creation")
        ai = await _ollama_query(f"Security scan this Lua script. List any malware indicators, backdoors, data theft, privilege escalation, or persistence mechanisms:\n\n{src[:3000]}", system="You are a Lua malware analyst. List threats concisely with severity.", max_tokens=1000)
        lines = ["═" * 60, f"  Security Scan — {job['name']}", "═" * 60, ""]
        if threats:
            lines.append(f"[ Automated Detection: {len(threats)} ]")
            lines.extend(f"  {t}" for t in threats)
        else:
            lines.append("[ Automated Detection: no obvious threats ]")
        lines.extend(["", "─" * 60, "  AI Analysis", "─" * 60, "", ai])
        out = "\n".join(lines)
        tmp = TMP / f"{_stamp()}_scan.txt"
        tmp.write_text(out)
        try:
            e = discord.Embed(color=WARN if threats else GOOD, timestamp=datetime.now())
            e.description = f"🔬 **Scan** — `{job['name']}` — `{len(threats)}` automated flags"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="scan.txt"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    if starts(".patch"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.patch`.\nAI strips protections, watermarks, phone-home calls, HWID checks."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src = (await fetch_source(job)).decode("utf-8", errors="replace")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        cleaned = _anti_tamper_strip(src)
        reply = await _ollama_query(f"Remove all protections, watermarks, phone-home calls, HWID checks, and anti-tamper from this Lua code. Return ONLY the clean stripped code:\n\n{cleaned[:6000]}", system="You are a Lua protection remover. Strip all licensing, HWID checks, watermarks, phone-home, and anti-tamper. Return only clean code.", max_tokens=4096)
        reply_clean = re.sub(r'^```lua\s*|^```\s*|```\s*$', '', reply, flags=re.MULTILINE).strip()
        tmp = TMP / f"{_stamp()}_patched.lua"
        tmp.write_text(reply_clean)
        try:
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"🩹 **Patch** — `{job['name']}` — `{len(reply_clean)}` bytes"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="patched.lua"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    if starts(".deep"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.deep`.\nMulti-pass: decode → strip → beautify → AI clean + rename."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src = (await fetch_source(job)).decode("utf-8", errors="replace")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        code = src
        code = _anti_tamper_strip(code)
        b64 = _b64_extract(code)
        if b64:
            for enc, dec in b64:
                code = code.replace(enc, dec[:len(enc)] if len(dec) > 3 else enc)
        reply = await _ollama_query(f"Clean and deobfuscate this Lua code. Rename variables meaningfully, simplify control flow, beautify. Return ONLY the clean code:\n\n{code[:4000]}", system="You are a Lua deobfuscation specialist. Return clean, readable, well-named code only.", max_tokens=4096)
        reply_clean = re.sub(r'^```lua\s*|^```\s*|```\s*$', '', reply, flags=re.MULTILINE).strip()
        tmp = TMP / f"{_stamp()}_deep.lua"
        tmp.write_text(reply_clean)
        try:
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"🔬 **Deep** — `{job['name']}` — `{len(reply_clean)}` bytes"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="deep_cleaned.lua"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    if starts(".ai2"):
        parts = content.split(None, 1)
        prompt = parts[1].strip() if len(parts) > 1 else ""
        files_text = ""
        if message.attachments:
            for att in message.attachments:
                if att.size > 1024 * 1024:
                    continue
                try:
                    data = await att.read()
                    files_text += f"\n--- {att.filename} ---\n{data.decode('utf-8', errors='ignore')[:30000]}\n"
                except Exception:
                    pass
        if not prompt and not files_text:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Usage: `.ai2 <prompt>` — optionally attach files for context.\nAI-powered via Ollama."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        await react(message, "🕓")
        full_prompt = prompt
        if files_text:
            full_prompt += f"\n\nContext from attached files:\n{files_text}"
        reply = await _ollama_query(full_prompt, system="You are a helpful AI assistant integrated into a Roblox Lua deobfuscation Discord bot.", max_tokens=2048)
        await unreact(message, "🕓"); await react(message, "✅")
        if len(reply) > 1900:
            raw_url = await pastefy.upload(http, "ai2_response.txt", reply)
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"🤖 **AI2** — response too long\n{raw_url}" if raw_url else "🤖 **AI2** — response too long"
        else:
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"🤖 **AI2**\n{reply}"
        e.set_footer(text="6Vms")
        await message.reply(embed=e, mention_author=False)
        return

    # ── .deobf  — Aspect-powered deobfuscator + AI analysis ──────────────────
    if starts(".deobf"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.deobf`.\nAspect dumper + AI analysis."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            raw = await fetch_source(job)
            src = raw.decode("utf-8", errors="replace")
            name = job["name"]
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        ok, result, took = await asyncio.to_thread(_run_aspect, raw, name)
        if ok:
            aspect_out = result.decode("utf-8", errors="ignore") if isinstance(result, bytes) else result
        else:
            aspect_out = src[:3000]
        summary = await _nvidia_query(
            f"Analyze this deobfuscated Lua code: what does it do, key logic, potential obfuscation left. Be concise:\n\n{aspect_out[:3000]}",
            system="You are a Lua code analyst. Summarize functionality and logic concisely.",
            max_tokens=1024,
        )
        tmp = TMP / f"{_stamp()}_deobf.lua"
        tmp.write_text(aspect_out)
        try:
            raw_url = await pastefy.upload(http, "deobfuscated.lua", aspect_out)
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"🧬 **Deobf** — `{name}` — `{len(aspect_out)}` bytes\n🤖 {summary[:300]}"
            if raw_url: e.description += f"\n{raw_url}"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="deobfuscated.lua"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .ultra  — Aspect dumper + AI polish ──────────────────────────────────
    if starts(".ultra"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.ultra`.\nAspect dumper + AI polish."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            raw = await fetch_source(job)
            src = raw.decode("utf-8", errors="replace")
            name = job["name"]
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        ok, result, took = await asyncio.to_thread(_run_aspect, raw, name)
        if ok:
            code = result.decode("utf-8", errors="ignore") if isinstance(result, bytes) else result
        else:
            code = src[:4000]
        code = _anti_tamper_strip(code)
        for i in range(2):
            chunk = code[:4000]
            reply = await _nvidia_query(
                f"Clean up this Lua code: fix formatting, rename unclear vars, remove dead code. Return ONLY the cleaned code:\n\n{chunk}",
                system="Clean up Lua code, fix formatting, return clean code only.",
                max_tokens=4096,
            )
            code = re.sub(r'^```lua\s*|^```\s*|```\s*$', '', reply, flags=re.MULTILINE).strip()
            if not code:
                code = chunk
        tmp = TMP / f"{_stamp()}_ultra.lua"
        tmp.write_text(code)
        try:
            raw_url = await pastefy.upload(http, "ultra.lua", code)
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"⚡ **Ultra** — `{name}` — `{len(code)}` bytes (Aspect + 2× AI)"
            if raw_url: e.description += f"\n{raw_url}"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="ultra.lua"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return
        try:
            raw_url = await pastefy.upload(http, "ultra_deobf.lua", code)
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"⚡ **Ultra** — `{job['name']}` — `{len(code)}` bytes (3 AI passes)"
            if raw_url: e.description += f"\n{raw_url}"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="ultra_deobf.lua"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .ultra2  — Aspect + String Dumper + VM JSON + AI summary ────────────
    if starts(".ultra2"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.ultra2`.\nAspect env log + String Dumper + VM scan → full JSON report + AI summary."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            raw = await fetch_source(job)
            src = raw.decode("utf-8", errors="replace")
            name = job["name"]
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")

        # ── 1. Aspect dumper ──
        aspect_text = ""
        ok, result, took = await asyncio.to_thread(_run_aspect, raw, name)
        if ok:
            aspect_text = result.decode("utf-8", errors="ignore") if isinstance(result, bytes) else result

        # ── 2. String dumper ──
        sd_text = ""
        ok, result, took = await asyncio.to_thread(_run_string_dumper, raw, name)
        if ok:
            sd_text = result.decode("utf-8", errors="ignore") if isinstance(result, bytes) else result

        # ── 3. Lua tracer ──
        tr_text = ""
        ok, result, took = await asyncio.to_thread(_run_lua_tracer, raw, name)
        if ok:
            tr_text = result.decode("utf-8", errors="ignore") if isinstance(result, bytes) else result

        # ── 4. VM / opcode extraction ──
        opcodes = list(set(re.findall(r'(OP_\w+|opcode|op_\d+|OP_\d+|bytecode|op_table|vm_dispatch|vm_exec)', src, re.IGNORECASE)))
        vm_refs = list(set(re.findall(r'(VM|vm|VirtualMachine|dispatch|opcode_handler)', src)))

        # ── 5. AI summary ──
        ai_prompt = f"Analyze this Lua script. Summarize: what it does, obfuscation methods, VM usage, anti-tamper.\n\n{src[:3000]}"
        if aspect_text:
            ai_prompt += f"\n\nAspect output (first 1500 chars):\n{aspect_text[:1500]}"
        summary = await _nvidia_query(ai_prompt, system="You are a Lua forensics analyst. Be concise.", max_tokens=1024)

        # ── 6. Build JSON ──
        report = _json.dumps({
            "file": name,
            "size_bytes": len(raw),
            "aspect_output": aspect_text[:5000] if aspect_text else "Aspect failed",
            "string_dumper": sd_text[:3000] if sd_text else "",
            "lua_tracer": tr_text[:3000] if tr_text else "",
            "vm_opcodes_found": opcodes,
            "vm_references": vm_refs,
            "ai_summary": summary,
        }, indent=2)

        tmp_json = TMP / f"{_stamp()}_ultra2.json"
        tmp_json.write_text(report)
        tmp_lua = TMP / f"{_stamp()}_ultra2.lua"
        tmp_lua.write_text(aspect_text or src[:3000])
        try:
            raw_url = await pastefy.upload(http, "ultra2_report.json", report)
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"🔬 **Ultra2** — `{name}`\n{len(opcodes)} opcodes · {len(vm_refs)} VM refs · {len(raw)} bytes"
            if raw_url: e.description += f"\n{raw_url}"
            e.set_footer(text="6Vms")
            files = [
                discord.File(str(tmp_json), filename="ultra2_report.json"),
                discord.File(str(tmp_lua), filename="aspect_output.lua"),
            ] if aspect_text else [discord.File(str(tmp_json), filename="ultra2_report.json")]
            await message.reply(content=message.author.mention, embed=e, files=files, mention_author=True)
        finally:
            try: tmp_json.unlink()
            except: pass
            try: tmp_lua.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .vm  — VM / bytecode devirtualizer (opcode lifting) ───────────────────
    if starts(".vm"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a VM-obfuscated `.lua`/`.txt` or paste a raw URL after `.vm`.\nBytecode devirtualizer — extracts and lifts VM opcodes."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            raw = await fetch_source(job)
            src = raw.decode("utf-8", errors="replace")
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        vm_report = []
        vm_report.append(f"═" * 60)
        vm_report.append(f"  VM Devirtualization Report — {job['name']}")
        vm_report.append(f"═" * 60)
        vm_report.append(f"  Input: {len(src)} chars")
        opcodes = re.findall(r'(OP_\w+|opcode|op_\d+|OP_\d+|bytecode|op_table)', src, re.IGNORECASE)
        vm_report.append(f"  Opcode references found: {len(set(opcodes))}")
        vm_report.append(f"  Unique opcodes: {', '.join(sorted(set(opcodes)))[:200]}")
        vm_report.append("")
        ai = await _nvidia_query(
            f"This Lua code uses a bytecode VM. Analyze and devirtualize it. Identify the VM dispatch loop, extract opcodes, and reconstruct the original code. Here is the script:\n\n{src[:4000]}",
            system="You are a Lua VM devirtualization expert. Reconstruct original code from VM bytecode. Explain the VM structure then give the deobfuscated code.",
            max_tokens=4096,
        )
        vm_report.append(ai)
        out = "\n".join(vm_report)
        tmp = TMP / f"{_stamp()}_vm.txt"
        tmp.write_text(out)
        try:
            raw_url = await pastefy.upload(http, "vm_report.txt", out)
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"🔮 **VM Devirt** — `{job['name']}` — `{len(set(opcodes))}` unique opcodes"
            if raw_url: e.description += f"\n{raw_url}"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="vm_report.txt"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .chain  — Aspect/r2 primary, fallback dumpers, AI picks best ─────────
    if starts(".chain"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.chain`.\nAspect/r2 primary → fallback dumpers → AI picks best."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            raw = await fetch_source(job)
            src = raw.decode("utf-8", errors="replace")
            name = job["name"]
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓"); await react(message, "⏳")
        results = {}

        # ── 1. Aspect (primary) ──
        try:
            ok, result, took = await asyncio.to_thread(_run_aspect, raw, name)
            if ok:
                text = result.decode("utf-8", errors="ignore") if isinstance(result, bytes) else result
                results["aspect"] = text
        except: pass

        # ── 2. main.luau ──
        try:
            stamp = _stamp()
            in_rel = f"bot_tmp/{stamp}.lua"
            out_rel = f"bot_tmp/{stamp}_out.lua"
            in_path, out_path = ROOT / in_rel, ROOT / out_rel
            in_path.write_bytes(raw)
            ok, reason, took = await asyncio.to_thread(_lune_dump, in_rel, out_rel, None, "lune", 60)
            if ok:
                results["main.luau"] = out_path.read_text(errors="ignore")
            for p in [in_path, out_path]:
                try: p.unlink()
                except: pass
        except: pass

        # ── 3. 45ms ──
        try:
            ok, result, took = await asyncio.to_thread(_run_45ms, raw, name)
            if ok:
                text = result.decode("utf-8", errors="ignore") if isinstance(result, bytes) else result
                results["45ms"] = text
        except: pass

        # ── Pick best: prefer aspect if it produced output ──
        best_text = results.get("aspect") or results.get("main.luau") or results.get("45ms") or src[:3000]
        best_name = "aspect" if "aspect" in results else (list(results.keys()) + ["source"])[0]

        ai = await _nvidia_query(
            f"Briefly analyze this deobfuscated Lua code. What does it do? Any obfuscation remaining? Then return the code as-is at the end:\n\n{best_text[:3000]}",
            system="You are a Lua analyst. Give a short analysis then return the code.",
            max_tokens=2048,
        )
        final = ai
        if len(best_text) > 100:
            final += f"\n\n-- Original output ({best_name}):\n" + best_text[:2000]
        tmp = TMP / f"{_stamp()}_chain.lua"
        tmp.write_text(final)
        try:
            raw_url = await pastefy.upload(http, "chain_result.lua", final)
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"⛓️ **Chain** — `{name}` — `{len(results)}` engines · Winner: `{best_name}`"
            if raw_url: e.description += f"\n{raw_url}"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="chain_result.lua"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .bulk  — Batch up to 5 scripts through Aspect ─────────────────────────
    if starts(".bulk"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach up to 5 `.lua`/`.txt` files or paste raw URLs after `.bulk`.\nBatch deobfuscation via Aspect dumper."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        if len(jobs) > 5:
            await message.reply("❌ Maximum 5 files per batch.", mention_author=False)
            return
        await react(message, "🕓")
        results_bulk = []
        for idx, job in enumerate(jobs):
            await unreact(message, "🕓"); await react(message, "⏳")
            try:
                raw = await fetch_source(job)
                name = job["name"]
            except Exception as ex:
                results_bulk.append(f"--- #{idx+1}: {job['name']} ---\n-- Error: {_redact(str(ex))}\n")
                continue
            ok, result, took = await asyncio.to_thread(_run_aspect, raw, name)
            if ok:
                text = result.decode("utf-8", errors="ignore") if isinstance(result, bytes) else result
            else:
                text = raw.decode("utf-8", errors="replace")[:3000]
            text = _anti_tamper_strip(text)
            results_bulk.append(f"--- #{idx+1}: {name} ---\n{text}\n")
        out_bulk = "\n".join(results_bulk)
        tmp = TMP / f"{_stamp()}_bulk.lua"
        tmp.write_text(out_bulk)
        try:
            raw_url = await pastefy.upload(http, "bulk_deobf.lua", out_bulk)
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"📦 **Bulk** — `{len(jobs)}` files — `{len(out_bulk)}` total bytes"
            if raw_url: e.description += f"\n{raw_url}"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="bulk_deobf.lua"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .mega  — Batch deobf (beta, unlimited files) ─────────────────────────
    if starts(".mega"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach `.lua`/`.txt` files or paste raw URLs after `.mega`.\n**BETA** — batch deobfuscation via Aspect. May take long or crash with many files."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        await react(message, "🕓")
        if len(jobs) > 10:
            await message.reply("⚠️ **BETA WARNING**: More than 10 files may crash or take very long. Proceeding anyway...", mention_author=False)
        warn = await message.reply(f"⚠️ **BETA** — Processing `{len(jobs)}` files through Aspect dumper. This may take a while or crash if files are large.", mention_author=False)
        results_mega = []
        for idx, job in enumerate(jobs):
            try:
                raw = await fetch_source(job)
                name = job["name"]
            except Exception as ex:
                results_mega.append(f"--- #{idx+1}: {job['name']} ---\n-- Error: {_redact(str(ex))}\n")
                continue
            ok, result, took = await asyncio.to_thread(_run_aspect, raw, name)
            if ok:
                text = result.decode("utf-8", errors="ignore") if isinstance(result, bytes) else result
            else:
                text = f"-- Aspect failed for {name}\n"
            text = _anti_tamper_strip(text)
            results_mega.append(f"--- #{idx+1}: {name} ---\n{text}\n")
        out_mega = "\n".join(results_mega)
        tmp = TMP / f"{_stamp()}_mega.lua"
        tmp.write_text(out_mega)
        try:
            raw_url = await pastefy.upload(http, "mega_deobf.lua", out_mega)
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"📦 **Mega (BETA)** — `{len(jobs)}` files — `{len(out_mega)}` total bytes"
            if raw_url: e.description += f"\n{raw_url}"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="mega_deobf.lua"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        try: await warn.delete()
        except: pass
        await unreact(message, "🕓"); await react(message, "✅")
        return

    # ── .megalune  — Lune dump 3 files with inline logic comments ────────────
    if starts(".megalune"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach up to 3 `.lua`/`.txt` files or raw URLs after `.megalune`.\nLune dumper with inline `-- [[ logic ]]` annotations on every line."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        if len(jobs) > 3:
            await message.reply("❌ Max 3 files for `.megalune`.", mention_author=False)
            return
        await react(message, "🕓")
        sections = []
        for idx, job in enumerate(jobs):
            await unreact(message, "🕓"); await react(message, "⏳")
            try:
                raw = await fetch_source(job)
                name = job["name"]
            except Exception as ex:
                sections.append(f"-- #{idx+1}: {job['name']} — Error: {_redact(str(ex))}\n")
                continue
            stamp = _stamp()
            in_rel = f"bot_tmp/{stamp}.lua"
            out_rel = f"bot_tmp/{stamp}_out.lua"
            in_path, out_path = ROOT / in_rel, ROOT / out_rel
            in_path.write_bytes(raw)
            ok, reason, took = await asyncio.to_thread(_lune_dump, in_rel, out_rel, None, "lune", 60)
            dumped = ""
            if ok:
                dumped = out_path.read_text(errors="ignore")
            for p in [in_path, out_path]:
                try: p.unlink()
                except: pass
            if not dumped:
                dumped = f"-- main.luau produced no output for {name}\n"
            lines = dumped.splitlines()
            if len(lines) > 60:
                lines = lines[:60]
                lines.append("-- ... (truncated)")
            chunk = "\n".join(lines)
            annotated = await _nvidia_query(
                f"Annotate each line of this Lua code with `-- [[ short explanation ]]` inline. "
                f"Return the SAME code with comments added. Be concise per line:\n\n{chunk}",
                system="Add inline -- [[ logic ]] comments to each line. Return only the annotated code.",
                max_tokens=4096,
            )
            annotated = re.sub(r'^```lua\s*|^```\s*|```\s*$', '', annotated, flags=re.MULTILINE).strip()
            sections.append(f"-- ═══ #{idx+1}: {name} ═══\n{annotated}\n")
        out = "\n".join(sections)
        tmp = TMP / f"{_stamp()}_megalune.lua"
        tmp.write_text(out)
        try:
            raw_url = await pastefy.upload(http, "megalune.lua", out)
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"🌙 **Megalune** — `{len(jobs)}` files — `{len(out)}` bytes"
            if raw_url: e.description += f"\n{raw_url}"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(tmp), filename="megalune.lua"), mention_author=True)
        finally:
            try: tmp.unlink()
            except: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .obf2  — Prometheus-style obfuscation panel ──────────────────────────
    if starts(".obf2"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` file or raw URL after `.obf2`.\nOpens an interactive obfuscation panel with preset selector + beautify toggle."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            raw = await fetch_source(job)
            src = raw.decode("utf-8", errors="replace")
            name = job["name"]
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False); await unreact(message, "🕓"); return
        await unreact(message, "🕓")
        panel = Obf2Panel(src, name, message)
        e = panel.build_embed()
        view_msg = await message.reply(content=message.author.mention, embed=e, view=panel, mention_author=False)
        panel.message = view_msg
        return

    # ── .antienv  — Anti-tamper / anti-debug environment script ───────────────
    if starts(".antienv"):
        antienv_path = ROOT / "antienv.lua"
        if not antienv_path.exists():
            await message.reply("❌ `antienv.lua` not found.", mention_author=False)
            return
        txt = antienv_path.read_text(encoding="utf-8")
        await react(message, "🕓")
        try:
            raw_url = await pastefy.upload(http, "antienv.lua", txt)
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"🛡️ **Anti-Env** — `{len(txt)}` bytes — Roblox anti-tamper / anti-debug script"
            if raw_url: e.description += f"\n{raw_url}"
            e.set_footer(text="6Vms")
            await message.reply(content=message.author.mention, embed=e, file=discord.File(str(antienv_path), filename="antienv.lua"), mention_author=True)
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False)
        await unreact(message, "🕓"); await react(message, "✅")
        return

    # ── .l  — Lune script dumper ──────────────────────────────────────────────
    # ── .l  — 3‑output deobfuscation (main.luau + aspect-native + aspect main) ──
    if starts(".l"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt`, drop a raw link, or reply to one with `.l`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        name = job["name"]
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"Couldn't fetch `{name}` — {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "🕓"); await react(message, "❌")
            return
        await unreact(message, "🕓"); await react(message, "⏳")
        ch_cfg = _get_cfg(message.channel.id)
        cfg_args = _cfg_to_args(ch_cfg)
        runtime = ch_cfg.get("_runtime", "lune")
        is_admin = _is_admin(message.author)
        job_timeout = min(int(ch_cfg.get("_timeout", 180)), ADMIN_TIMEOUT if is_admin else USER_TIMEOUT)
        results = []

        # ── Dumper 1: main.luau (lune) ──
        d1_st = time.perf_counter()
        d1_s = _stamp()
        d1_in, d1_out = f"bot_tmp/{d1_s}_l1_in.lua", f"bot_tmp/{d1_s}_l1_out.lua"
        (ROOT / d1_in).write_bytes(src_bytes)
        try:
            ok1, reason1, t1 = await asyncio.to_thread(_lune_dump, d1_in, d1_out, cfg_args, runtime, job_timeout)
        except Exception as e:
            ok1, reason1, t1 = False, str(e), time.perf_counter() - d1_st
        out1 = (ROOT / d1_out).read_text(errors="replace") if ok1 else f"-- [main.luau FAILED: {reason1}] --\n"
        results.append(("main-luau", out1, ok1, t1 if ok1 else time.perf_counter() - d1_st))
        for p in (ROOT / d1_in, ROOT / d1_out):
            try: p.unlink()
            except OSError: pass

        # ── Dumper 2: aspect-native ──
        ok2, data2, t2 = await asyncio.to_thread(_run_aspect_native, src_bytes, name)
        out2 = data2.decode("utf-8", "replace") if isinstance(data2, bytes) else f"-- [aspect-native FAILED: {data2}] --\n"
        results.append(("aspect-native", out2, ok2, t2))

        # ── Dumper 3: aspect (mods/aspect.luau) ──
        ok3, data3, t3 = await asyncio.to_thread(_run_aspect, src_bytes, name)
        out3 = data3.decode("utf-8", "replace") if isinstance(data3, bytes) else f"-- [aspect FAILED: {data3}] --\n"
        results.append(("aspect-main", out3, ok3, t3))

        # ── Build reply ──
        e = discord.Embed(color=GOOD, timestamp=datetime.now())
        desc = f"**`{name}`** — 3 deobfuscations\n"
        files = []
        tmp_files = []
        for label, text, ok, ti in results:
            s = "✅" if ok else "❌"
            lc = text.count(chr(10)) + 1
            desc += f"{s} **{label}**: `{lc:,} lines` · `{len(text)/1024:.1f}KB` · `{ti:.2f}s`\n"
            raw_url = await pastefy.upload(http, f"{label}.lua", text)
            if raw_url:
                desc += f"  ↳ {raw_url}\n"
            fname = _rand_name(f"{label}.lua")
            tmp = TMP / f"__l_{_stamp()}_{label}.lua"
            tmp.write_text(text, encoding="utf-8", errors="replace")
            files.append(discord.File(str(tmp), filename=fname))
            tmp_files.append(tmp)
        e.description = desc
        e.set_footer(text="6Vms · https://pastefy.app/pAhNz8FU/raw")
        try:
            await message.reply(content=message.author.mention, embed=e, files=files, mention_author=True)
        finally:
            for p in tmp_files:
                try: p.unlink()
                except OSError: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .r  — Revea environment dumper ────────────────────────────────────────
    if starts(".r"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt`, drop a raw link, or reply to one with `.r`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        await react(message, "🕓")
        for j in jobs:
            j["dumper"] = "revea"
            ahead = _queue_put(j, message)
            if isinstance(ahead, str):
                await message.reply(f"❌ {ahead}", mention_author=False, delete_after=10)
                return
        note = f"queued `{len(jobs)}`" + (f" · `{ahead}` ahead" if ahead else "")
        try: await message.reply(note, mention_author=False, delete_after=6)
        except discord.HTTPException: pass
        return

    # ── .r2  — Aspect environment dumper (better Revea) ──────────────────────
    if starts(".r2"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt`, drop a raw link, or reply to one with `.r2`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        await react(message, "🕓")
        for j in jobs:
            j["dumper"] = "aspect"
            ahead = _queue_put(j, message)
            if isinstance(ahead, str):
                await message.reply(f"❌ {ahead}", mention_author=False, delete_after=10)
                return
        note = f"queued `{len(jobs)}`" + (f" · `{ahead}` ahead" if ahead else "")
        try: await message.reply(note, mention_author=False, delete_after=6)
        except discord.HTTPException: pass
        return

    # ── .l2  — 6VDumper environment dumper ────────────────────────────────────
    if starts(".l2"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt`, drop a raw link, or reply to one with `.l2`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        await react(message, "🕓")
        for j in jobs:
            j["dumper"] = "6vdumper"
            ahead = _queue_put(j, message)
            if isinstance(ahead, str):
                await message.reply(f"❌ {ahead}", mention_author=False, delete_after=10)
                return
        note = f"queued `{len(jobs)}`" + (f" · `{ahead}` ahead" if ahead else "")
        try: await message.reply(note, mention_author=False, delete_after=6)
        except discord.HTTPException: pass
        return

    # ── .unveilr  — UnveilR environment logger ────────────────────────────────
    if starts(".unveilr"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt`, drop a raw link, or reply to one with `.unveilr`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"Couldn't fetch `{job['name']}` — {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓"); await react(message, "⏳")
        try:
            cfg_args = _cfg_to_args(_get_cfg(message.channel.id))
            ok, result, took = await asyncio.to_thread(_run_unveilr, src_bytes, job["name"], cfg_args)
            if ok:
                data_bytes = result if isinstance(result, bytes) else result.encode()
                data_str   = _stamp_output(data_bytes.decode("utf-8", errors="ignore"))
                data_bytes = data_str.encode("utf-8")
                out_fname  = _rand_name("unveilr.lua")
                raw_url    = await pastefy.upload(http, out_fname, data_str)
                e = discord.Embed(color=GOOD, timestamp=datetime.now())
                e.description = (
                    f"🔍 **UnveilR** — `{job['name']}`\n"
                    f"`{len(data_str.splitlines()):,} lines` · `{len(data_bytes)/1024:.1f} KB` · `{took:.2f}s`"
                )
                if raw_url: e.description += f"\n{raw_url}"
                e.set_footer(text="6Vms")
                tmp = TMP / f"__unveilr_{_stamp()}.lua"
                tmp.write_bytes(data_bytes)
                try:
                    await message.reply(content=message.author.mention, embed=e,
                                        file=discord.File(str(tmp), filename=out_fname), mention_author=True)
                finally:
                    try: tmp.unlink()
                    except OSError: pass
                await unreact(message, "⏳"); await react(message, "✅")
            else:
                e = discord.Embed(color=BAD, timestamp=datetime.now())
                e.description = f"🔍 **UnveilR** — `{job['name']}`\n{result}"
                e.set_footer(text="6Vms")
                await message.reply(content=message.author.mention, embed=e, mention_author=True)
                await unreact(message, "⏳"); await react(message, "❌")
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🔍 **UnveilR** — `{job['name']}`\nerror: {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            try: await message.reply(content=message.author.mention, embed=e, mention_author=True)
            except discord.HTTPException: pass
            await unreact(message, "⏳"); await react(message, "❌")
        return

    # ── .25ms  — 25ms Dumper ──────────────────────────────────────────────────
    if starts(".25ms"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt`, drop a raw link, or reply to one with `.25ms`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        # Sanitise the display name — strip every accumulated extension and re-add .lua
        raw_name = re.sub(r"(\.(lua|txt|luau))+$", "", job["name"], flags=re.I) or "script"
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"Couldn't fetch `{raw_name}` — {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓"); await react(message, "⏳")
        try:
            cfg_args = _cfg_to_args(_get_cfg(message.channel.id))
            ok, result, took = await asyncio.to_thread(_run_25ms, src_bytes, raw_name, cfg_args)
            if ok:
                data_bytes = result if isinstance(result, bytes) else result.encode()
                data_str   = _stamp_output(data_bytes.decode("utf-8", errors="ignore"))
                data_bytes = data_str.encode("utf-8")
                out_fname  = _rand_name("25ms.lua")
                raw_url    = await pastefy.upload(http, out_fname, data_str)
                e = discord.Embed(color=GOOD, timestamp=datetime.now())
                e.description = (
                    f"🟣 **25ms Dumper** — `{raw_name}`\n"
                    f"`{len(data_str.splitlines()):,} lines` · `{len(data_bytes)/1024:.1f} KB` · `{took:.2f}s`"
                )
                if raw_url: e.description += f"\n{raw_url}"
                e.set_footer(text="6Vms")
                tmp = TMP / f"__25ms_{_stamp()}.lua"
                tmp.write_bytes(data_bytes)
                try:
                    await message.reply(content=message.author.mention, embed=e,
                                        file=discord.File(str(tmp), filename=out_fname), mention_author=True)
                finally:
                    try: tmp.unlink()
                    except OSError: pass
                await unreact(message, "⏳"); await react(message, "✅")
            else:
                e = discord.Embed(color=BAD, timestamp=datetime.now())
                e.description = f"🟣 **25ms Dumper** — `{raw_name}`\n{result}"
                e.set_footer(text="6Vms")
                await message.reply(content=message.author.mention, embed=e, mention_author=True)
                await unreact(message, "⏳"); await react(message, "❌")
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🟣 **25ms Dumper** — `{raw_name}`\nerror: {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            try: await message.reply(content=message.author.mention, embed=e, mention_author=True)
            except discord.HTTPException: pass
            await unreact(message, "⏳"); await react(message, "❌")
        return

    # ── .get  — fetch content from a URL and return as file ──────────────────
    if starts(".get"):
        parts = content.split(None, 1)
        url   = parts[1].strip() if len(parts) > 1 else ""
        if not url.startswith("http"):
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Usage: `.get <url>` — fetches content and returns it as a file."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        await react(message, "🕓")
        try:
            async with http.get(url, timeout=aiohttp.ClientTimeout(total=30),
                                headers={"User-Agent": "Roblox/WinInetRobloxApp"}) as r:
                r.raise_for_status()
                raw = await r.read()
        except Exception as ex:
            await message.reply(f"❌ fetch failed — `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓")
        fname = url.split("?")[0].rstrip("/").split("/")[-1] or "script.lua"
        if not fname.lower().endswith((".lua", ".txt")):
            fname += ".lua"
        raw_url = await pastefy.upload(http, fname, raw.decode("utf-8", errors="ignore"))
        e = discord.Embed(color=GOOD, timestamp=datetime.now())
        e.description = f"`{fname}` · `{len(raw)/1024:.1f} KB`"
        if raw_url: e.description += f"\n{raw_url}"
        e.set_footer(text="6Vms")
        buf = discord.File(io.BytesIO(raw), filename=fname)
        await message.reply(embed=e, file=buf, mention_author=False)
        return

    # ── .luaprot  — fetch a LuaProt protected payload through its loader ──────
    if starts(".luaprot"):
        parts = content.split(None, 1)
        arg   = parts[1].strip() if len(parts) > 1 else ""
        if not arg:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = ("Usage: `.luaprot <scriptId|loader-url>`\n"
                             "Fetches the LuaProt V2 protected payload via the loader "
                             "(`/api/v2/loader/get`), with HWID + Roblox session headers.")
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        await react(message, "🕓")
        loader_url = None
        script_id  = None
        lp_key     = "x"
        try:
            if arg.isdigit():
                script_id = arg
                loader_url = f"https://luaprot.net/api/v2/loaders/get/{script_id}"
            elif arg.startswith("http"):
                async with http.get(arg, timeout=aiohttp.ClientTimeout(total=30),
                                    headers={"User-Agent": "Roblox/WinInet"}) as r:
                    r.raise_for_status()
                    entry = await r.text()
                m = re.search(r"https?://luaprot\.net/api/v2/loaders/get/(\d+)", entry)
                if m:
                    script_id = m.group(1)
                    loader_url = m.group(0)
                elif re.search(r"/api/v2/loaders/get/(\d+)", arg):
                    script_id = re.search(r"/api/v2/loaders/get/(\d+)", arg).group(1)
                    loader_url = arg
                else:
                    raise ValueError("couldn't find a LuaProt loader URL in that page")
            else:
                raise ValueError("pass a scriptId (digits) or a loader/entrypoint URL")

            async with http.get(loader_url, timeout=aiohttp.ClientTimeout(total=30),
                                headers={"User-Agent": "Roblox/WinInet"}) as r:
                r.raise_for_status()
                loader_txt = await r.text()
            if not script_id:
                m = re.search(r"scriptId\s*=\s*[\"']?(\d+)", loader_txt)
                if not m:
                    m = re.search(r"/api/v2/loaders/get/(\d+)", loader_txt)
                if not m:
                    m = re.search(r'="(\d{10,})"', loader_txt)
                if m:
                    script_id = m.group(1)
            m = re.search(r"lp_key\s*=\s*(?:lp_key\s*or\s*)?[\"']?([A-Za-z0-9_-]+)", loader_txt)
            if m:
                lp_key = m.group(1)
            if not script_id:
                raise ValueError("couldn't extract scriptId from the LuaProt loader")

            # prefer the node served by /api/v1/nodes/get, else try the usual list
            nodes = []
            try:
                async with http.get("https://eu-1.luaprot.net/api/v1/nodes/get",
                                    timeout=aiohttp.ClientTimeout(total=15),
                                    headers={"User-Agent": "Roblox/WinInet"}) as r:
                    r.raise_for_status()
                    node_json = await r.json()
                if node_json.get("node"):
                    nodes.append(node_json["node"])
            except Exception:
                pass
            nodes += [n for n in ("us-1", "eu-1", "eu-2") if n not in nodes]

            job = str(uuid.uuid4())
            hwid = hashlib.sha256(b"6vms-luaprot-static-hwid").hexdigest().upper()
            session_hdr = _json.dumps({"GameId": job, "PlaceId": "0"})
            hdrs = {
                "User-Agent": "Roblox/WinInet",
                "Roblox-Game-Id": job,
                "Roblox-Session-Id": session_hdr,
                "HWID": hwid,
            }

            best_body, best_node, best_code, best_err = None, None, 0, ""
            for node in nodes:
                url = (f"https://{node}.luaprot.net/api/v2/loader/get"
                       f"?key={urllib.parse.quote(lp_key)}&scriptId={script_id}")
                try:
                    async with http.get(url, timeout=aiohttp.ClientTimeout(total=40),
                                        headers=hdrs) as r:
                        code = r.status
                        body = await r.text()
                except Exception as ex:
                    best_err = f"{node}: {_redact(str(ex))}"
                    continue
                if code not in (200, 201):
                    best_err = f"{node}: HTTP {code}"
                    continue
                kick = ("Hwid not found" in body or "Invalid key" in body
                        or "Missing parameters" in body)
                if kick or len(body) < 1000:
                    best_err = f"{node}: kick stub ({len(body)} B)"
                    continue
                if not best_body or len(body) > len(best_body):
                    best_body, best_node, best_code = body, node, code

            if not best_body:
                raise ValueError("no node returned the payload — last error: %s" % (best_err or "unknown"))

            raw = best_body.encode("utf-8", errors="ignore")
        except Exception as ex:
            await message.reply(f"❌ luaprot fetch failed — `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓")
        fname = f"luaprot_{script_id}.lua"
        raw_url = await pastefy.upload(http, fname, raw.decode("utf-8", errors="ignore"))
        e = discord.Embed(color=GOOD, timestamp=datetime.now())
        e.description = (f"🔒 **LuaProt payload** — `{script_id}`\n"
                         f"`{best_node}` · `{len(raw)/1024:.1f} KB` · key `{lp_key}`")
        if raw_url: e.description += f"\n{raw_url}"
        e.description += ("\n-# Still LuaProt/Lura.ph protected — deobfuscation is a "
                          "separate step.")
        e.set_footer(text="6Vms")
        tmp = TMP / f"__luaprot_{_stamp()}.lua"
        tmp.write_bytes(raw)
        try:
            await message.reply(embed=e, file=discord.File(str(tmp), filename=fname), mention_author=False)
        finally:
            try: tmp.unlink()
            except OSError: pass
        await react(message, "✅")
        return

    # ── .junkie  — fetch a free jnkie public script through its download API ──
    if starts(".junkie"):
        parts = content.split(None, 1)
        arg   = parts[1].strip() if len(parts) > 1 else ""
        if not arg:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = ("Usage: `.junkie <url|script-hash>`\n"
                             "Downloads a jnkie script through the Delta-style "
                             "`/api/v1/luascripts/delivery/<hash>?v=2` handshake.")
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        await react(message, "🕓")
        payload = None
        meta = None
        try:
            payload, meta = await jnkie.fetch_free(arg)
        except jnkie.FetchError as ex:
            await message.reply(f"❌ jnkie fetch failed — `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "🕓")
            return
        except Exception as ex:
            await message.reply(f"❌ jnkie fetch failed — `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓")
        fname = f"jnkie_{meta['hash']}.lua"
        raw = payload.encode("utf-8", errors="ignore")
        raw_url = await pastefy.upload(http, fname, payload)
        e = discord.Embed(color=GOOD, timestamp=datetime.now())
        e.description = (f"📥 **jnkie script** — `{meta['hash']}`\n"
                         f"`{meta['size']/1024:.1f} KB` · {meta['elapsed']:.1f}s"
                         + (" · 🔒 Luraph-protected block" if meta["luraph"] else ""))
        if raw_url: e.description += f"\n{raw_url}"
        if meta["luraph"]:
            e.description += "\n-# Trimmed to the Luraph-protected block — still needs deobi."
        e.set_footer(text="6Vms")
        tmp = TMP / f"__junkie_{_stamp()}.lua"
        tmp.write_bytes(raw)
        try:
            await message.reply(embed=e, file=discord.File(str(tmp), filename=fname), mention_author=False)
        finally:
            try: tmp.unlink()
            except OSError: pass
        await react(message, "✅")
        return

    # ── .obscura  — Obscura-protected payload: executor-workspace capture or
    #                (pre-v3.4.9 tokens only) direct handshake fetch ────────────
    if starts(".obscura"):
        parts = content.split(None, 1)
        arg   = parts[1].strip() if len(parts) > 1 else ""
        jobs = await gather_jobs(message)
        if not arg and not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = ("Usage: `.obscura <url|attachment>`\n\n"
                             "Obscura v3.4.9's anti-HTTP-spy guard blocks bot-side "
                             "fetches, so the reliable way is a **workspace capture**:\n"
                             "1️⃣ Run the loadstring in any executor/roblox game:\n"
                             "```\nloadstring(game:HttpGet(\"https://protected.obscuravm.com/<SERVICE>/<TOKEN>/download\"))()\n```\n"
                             "2️⃣ The loader caches the payload as `obscura_<TOKEN>.bin` "
                             "in your executor's **workspace folder**.\n"
                             "3️⃣ Attach that file to `.obscura` (or reply to this with it).\n\n"
                             "Passing a URL still tries the older direct handshake for "
                             "pre-v3.4.9 tokens.")
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        await react(message, "🕓")
        payload = None
        meta = None
        try:
            if jobs:
                src_bytes = await fetch_source(jobs[0])
                payload = src_bytes.decode("utf-8", errors="replace")
                meta = {"project": jobs[0].get("name", "capture"),
                        "size": len(payload), "elapsed": 0.0}
            else:
                payload, meta = await obscura.fetch_free(arg)
        except obscura.FetchError as ex:
            await message.reply(f"❌ obscura fetch failed — `{_redact(str(ex))}`\n"
                                f"-# Direct bot fetch is blocked by v3.4.9's anti-HTTP-spy "
                                f"guard — use the workspace capture workflow above instead.",
                                mention_author=False)
            await unreact(message, "🕓")
            return
        except Exception as ex:
            await message.reply(f"❌ obscura fetch failed — `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓")
        fname = f"obscura_{os.path.splitext(meta['project'])[0].replace('obscura_', '')}.lua"
        raw = payload.encode("utf-8", errors="ignore")
        raw_url = await pastefy.upload(http, fname, payload)
        e = discord.Embed(color=GOOD, timestamp=datetime.now())
        e.description = (f"🕸️ **Obscura payload** — `{meta['project']}`\n"
                         f"`{meta['size']/1024:.1f} KB` · {meta['elapsed']:.1f}s")
        if raw_url: e.description += f"\n{raw_url}"
        e.description += ("\n-# Still Obscura-protected — deobfuscation is a "
                          "separate step.")
        e.set_footer(text="6Vms")
        tmp = TMP / f"__obscura_{_stamp()}.lua"
        tmp.write_bytes(raw)
        try:
            await message.reply(embed=e, file=discord.File(str(tmp), filename=fname), mention_author=False)
        finally:
            try: tmp.unlink()
            except OSError: pass
        await react(message, "✅")
        return

    # ── .keyforge  — KeyForge (ForgeVM) VM-trace deobfuscator ────────────────
    if starts(".keyforge"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.keyforge`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            await message.reply(f"❌ couldn't fetch — `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "🕓")
            return
        if not keyforge.is_keyforge(src_bytes.decode("utf-8", errors="replace")):
            await message.reply("❌ not a KeyForge payload — missing the ForgeVM marker",
                                mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓"); await react(message, "⏳")
        lune_bin = str(LUNE) if LUNE.exists() else "lune"
        ok, result, took = await asyncio.to_thread(keyforge.run_deobf, src_bytes, lune_bin, 60)
        if not ok:
            await message.reply(f"❌ keyforge failed — `{result}`", mention_author=False)
            await unreact(message, "⏳"); await react(message, "❌")
            return
        data_str = _stamp_output(result)
        data_bytes = data_str.encode("utf-8")
        out_fname = f"keyforge_{_stamp()}.lua"
        raw_url = await pastefy.upload(http, out_fname, data_str)
        e = discord.Embed(color=GOOD, timestamp=datetime.now())
        e.description = (
            f"⚒️ **KeyForge deobf** — `{job['name']}`\n"
            f"`{len(data_bytes)/1024:.1f} KB` · `{took:.1f}s`\n"
            "-# VM-trace breakdown, not full source — clean up the register "
            "pseudo-code by hand."
        )
        if raw_url: e.description += f"\n{raw_url}"
        e.set_footer(text="6Vms")
        tmp = TMP / f"__keyforge_{_stamp()}.lua"
        tmp.write_bytes(data_bytes)
        try:
            await message.reply(embed=e, file=discord.File(str(tmp), filename=out_fname), mention_author=False)
        finally:
            try: tmp.unlink()
            except OSError: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .upload  — upload an attached file to pastefy and return raw URL ─────
    if starts(".upload"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.upload`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            await message.reply(f"❌ couldn't fetch — `{_redact(str(ex))}`", mention_author=False)
            return
        raw_url = await pastefy.upload(http, job["name"], src_bytes.decode("utf-8", errors="ignore"))
        if raw_url:
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = (
                f"`{job['name']}` uploaded\n{raw_url}\n\n"
                f"`loadstring(game:HttpGet('{raw_url}'))()`"
            )
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
        else:
            await message.reply("❌ upload failed — pastefy returned nothing", mention_author=False)
        return

    # ── .minify  — minify via DarkLua dense generator ────────────────────────
    if starts(".minify"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.minify`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            await message.reply(f"❌ couldn't fetch — `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓"); await react(message, "⏳")
        MINIFY_RULES = [
            "convert_index_to_field", "compute_expression",
            "filter_after_early_return", "group_local_assignment",
            "remove_comments", "remove_method_definition",
            "remove_nil_declaration", "remove_spaces", "remove_types",
            "remove_unused_if_branch", "remove_unused_variable",
            "remove_unused_while", "remove_function_call_parens",
        ]
        ok, result, took = await asyncio.to_thread(
            _run_darklua, src_bytes, MINIFY_RULES, "dense", True
        )
        if ok:
            data_bytes = result if isinstance(result, bytes) else result.encode()
            data_str   = _stamp_output(data_bytes.decode("utf-8", errors="ignore"))
            data_bytes = data_str.encode("utf-8")
            out_fname  = _rand_name("min.lua")
            raw_url    = await pastefy.upload(http, out_fname, data_str)
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = (
                f"⚙️ **Minify** — `{job['name']}`\n"
                f"`{len(data_bytes)/1024:.1f} KB` · `{took:.2f}s`"
            )
            if raw_url: e.description += f"\n{raw_url}"
            e.set_footer(text="6Vms")
            tmp = TMP / f"__min_{_stamp()}.lua"
            tmp.write_bytes(data_bytes)
            try:
                await message.reply(embed=e, file=discord.File(str(tmp), filename=out_fname), mention_author=False)
            finally:
                try: tmp.unlink()
                except OSError: pass
            await unreact(message, "⏳"); await react(message, "✅")
        else:
            await message.reply(f"❌ minify failed — `{result}`", mention_author=False)
            await unreact(message, "⏳"); await react(message, "❌")
        return

    # ── .beautify  — beautify via DarkLua readable generator ─────────────────
    if starts(".beautify"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.beautify`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            await message.reply(f"❌ couldn't fetch — `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓"); await react(message, "⏳")
        ok, result, took = await asyncio.to_thread(
            _run_darklua, src_bytes,
            ["compute_expression", "convert_index_to_field"],
            "readable", True
        )
        if ok:
            data_bytes = result if isinstance(result, bytes) else result.encode()
            data_str   = _stamp_output(data_bytes.decode("utf-8", errors="ignore"))
            data_bytes = data_str.encode("utf-8")
            out_fname  = _rand_name("beautified.lua")
            raw_url    = await pastefy.upload(http, out_fname, data_str)
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = (
                f"✨ **Beautify** — `{job['name']}`\n"
                f"`{len(data_str.splitlines()):,} lines` · `{len(data_bytes)/1024:.1f} KB` · `{took:.2f}s`"
            )
            if raw_url: e.description += f"\n{raw_url}"
            e.set_footer(text="6Vms")
            tmp = TMP / f"__beau_{_stamp()}.lua"
            tmp.write_bytes(data_bytes)
            try:
                await message.reply(embed=e, file=discord.File(str(tmp), filename=out_fname), mention_author=False)
            finally:
                try: tmp.unlink()
                except OSError: pass
            await unreact(message, "⏳"); await react(message, "✅")
        else:
            await message.reply(f"❌ beautify failed — `{result}`", mention_author=False)
            await unreact(message, "⏳"); await react(message, "❌")
        return

    # ── .rename  — variable rename via renamer-api.vercel.app ────────────────
    if starts(".rename"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.rename`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            await message.reply(f"❌ couldn't fetch — `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓"); await react(message, "⏳")
        try:
            async with http.post(
                "https://renamer-api.vercel.app/api/rename",
                json={"code": src_bytes.decode("utf-8", errors="replace")},
                headers={"x-api-key": "33ms-DHJHS-24633", "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                data = await r.json()
            renamed = data.get("renamedCode") if isinstance(data, dict) else None
        except Exception as ex:
            renamed = None
        if not renamed:
            await message.reply("❌ rename failed — API returned nothing", mention_author=False)
            await unreact(message, "⏳"); await react(message, "❌")
            return
        data_str   = _stamp_output(renamed)
        data_bytes = data_str.encode("utf-8")
        out_fname  = _rand_name("renamed.lua")
        raw_url    = await pastefy.upload(http, out_fname, data_str)
        e = discord.Embed(color=GOOD, timestamp=datetime.now())
        e.description = f"🔡 **Rename** — `{job['name']}`\n`{len(data_bytes)/1024:.1f} KB`"
        if raw_url: e.description += f"\n{raw_url}"
        e.set_footer(text="6Vms")
        tmp = TMP / f"__ren_{_stamp()}.lua"
        tmp.write_bytes(data_bytes)
        try:
            await message.reply(embed=e, file=discord.File(str(tmp), filename=out_fname), mention_author=False)
        finally:
            try: tmp.unlink()
            except OSError: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .byp  — bypass a link via fi8.bot-hosting.net ───────────────────────
    if starts(".byp"):
        parts = content.split(None, 1)
        url   = parts[1].strip() if len(parts) > 1 else ""
        if not url.startswith("http"):
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Usage: `.byp <url>` — bypasses Linkvertise / Lootlink / etc."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return

        # ── rate-limit guard: 4 requests per 10s per user, no same-link spam ─
        now      = time.monotonic()
        uid      = message.author.id
        win_key  = f"byp_win_{uid}"
        url_key  = f"byp_url_{uid}"

        # sliding window: keep only timestamps within the last 10s
        win: list = _byp_state.get(win_key, [])
        win = [t for t in win if now - t < 10.0]

        if len(win) >= 4:
            wait_s = round(10.0 - (now - win[0]), 1)
            e = discord.Embed(color=WARN, timestamp=datetime.now())
            e.description = f"⏱️ slow down — bypass rate limit hit. try again in `{wait_s}s`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return

        # same-link spam guard: track per-url hit count in the same window
        url_hits: dict = _byp_state.get(url_key, {})
        url_hits = {u: (c, t) for u, (c, t) in url_hits.items() if now - t < 10.0}
        hit_count, _ = url_hits.get(url, (0, now))
        if hit_count >= 4:
            e = discord.Embed(color=WARN, timestamp=datetime.now())
            e.description = "⏱️ same link requested too many times — wait a few seconds."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return

        # update state
        win.append(now)
        url_hits[url] = (hit_count + 1, now)
        _byp_state[win_key] = win
        _byp_state[url_key] = url_hits
        # ─────────────────────────────────────────────────────────────────────

        await react(message, "⏳")
        result = None
        time_taken = None
        try:
            async with http.get(
                "http://fi8.bot-hosting.net:21163/freeapibypass",
                params={"url": url},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                data = await r.json(content_type=None)
            if isinstance(data, dict) and data.get("status") == "success":
                result     = data.get("result", "").strip()
                time_taken = data.get("time_taken")
        except Exception:
            result = None

        if result:
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"✅ **bypassed!**\n{result}"
            if time_taken:
                e.description += f"\n-# took {time_taken} · powered by BananaApi"
            else:
                e.description += f"\n-# powered by BananaApi"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "⏳"); await react(message, "✅")
        else:
            await message.reply(
                "❌ bypass failed or link not supported.",
                mention_author=False,
            )
            await unreact(message, "⏳"); await react(message, "❌")
        return

    # ── .delta  — Delta link bypass (zeox.xyz) ─────────────────────────────
    if starts(".delta"):
        parts = content.split(None, 1)
        url   = parts[1].strip() if len(parts) > 1 else ""
        if not url.startswith("http"):
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Usage: `.delta <url>` — bypasses loot.link / etc."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return

        now      = time.monotonic()
        uid      = message.author.id
        win_key  = f"delta_win_{uid}"
        url_key  = f"delta_url_{uid}"
        win: list = _delta_state.get(win_key, [])
        win = [t for t in win if now - t < 10.0]
        if len(win) >= 4:
            wait_s = round(10.0 - (now - win[0]), 1)
            e = discord.Embed(color=WARN, timestamp=datetime.now())
            e.description = f"⏱️ slow down — `.delta` rate limit. try again in `{wait_s}s`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        url_hits: dict = _delta_state.get(url_key, {})
        url_hits = {u: (c, t) for u, (c, t) in url_hits.items() if now - t < 10.0}
        hit_count, _ = url_hits.get(url, (0, now))
        if hit_count >= 4:
            e = discord.Embed(color=WARN, timestamp=datetime.now())
            e.description = "⏱️ same link requested too many times."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        win.append(now)
        url_hits[url] = (hit_count + 1, now)
        _delta_state[win_key] = win
        _delta_state[url_key] = url_hits

        await react(message, "⏳")
        try:
            result = await _run_delta(url)
        except Exception as ex:
            result = f"error: {ex}"

        if result and not result.startswith("bypass fail") and not result.startswith("error"):
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"✅ **bypassed!**\n{result}\n-# 6Vms Delta Bypass"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "⏳"); await react(message, "✅")
        else:
            err = _redact(str(result or "no response"))[:500]
            await message.reply(f"❌ {err}", mention_author=False)
            await unreact(message, "⏳"); await react(message, "❌")
        return

    # ── .detect  — obfuscator detection ──────────────────────────────────────
    if starts(".detect"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.detect`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "⏳")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            await message.reply(f"❌ couldn't fetch — `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "⏳")
            return
        code = src_bytes.decode("utf-8", errors="ignore")
        # Quick regex heuristics (compiled once, see _OBF_SAMPLES)
        det = _detect_obfuscators(code)
        quick_hit = _OBF_SAMPLES[det[0]][0] if det else None

        # Hit the detector API
        result_lines: list[str] = []
        if quick_hit:
            result_lines.append(f"**Quick detect:** likely `{quick_hit}`")
        try:
            async with http.post(
                "https://detector.lua.cz/detect",
                json={"text": code[:30000]},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                data = await r.json()
            if data.get("ok"):
                probs = sorted(
                    ((item["label"], item["probability"]) for item in data.get("top_4", [])),
                    key=lambda x: x[1], reverse=True,
                )
                hits = [(k, v) for k, v in probs if v >= 0.20] or probs[:1]
                result_lines.append(
                    "**1xayd1 AI:**\n" + "\n".join(f"`{v*100:.1f}%` {k}" for k, v in hits)
                )
        except Exception:
            result_lines.append("*1xayd1 API unavailable*")

        # 6Vms-API fallback
        try:
            async with http.post(
                "https://verilux-tau.vercel.app/api/identify",
                json={"code": code[:50000]},
                timeout=aiohttp.ClientTimeout(total=25),
            ) as r2:
                vdata = await r2.json()
            if vdata.get("topMatch"):
                tm = vdata["topMatch"]
                vm = vdata.get("codeStats", {})
                vlines = [
                    "**6Vms-API:**",
                    "`%s` — %.1f%% confidence" % (tm["displayName"], tm.get("confidence", 0)),
                ]
                if vdata.get("accuracy"):
                    vlines.append("Accuracy: `%s`" % vdata["accuracy"])
                if vm.get("totalLines"):
                    vlines.append("Stats: %d lines, %d chars" % (vm.get("totalLines", 0), vm.get("totalChars", 0)))
                if vdata.get("warnings"):
                    vlines.append("Warnings: `%s`" % "; ".join(vdata["warnings"][:3]))
                result_lines.append("\n".join(vlines))
        except Exception:
            pass

        # Sample repo reference — link any detected family to its labelled sample
        _matched: list[str] = []
        _matched_urls: set[str] = set()

        def _add_sample(folder: str) -> None:
            _url = "https://github.com/terrorlua/obfuscator-samples/tree/main/" + folder
            if _url in _matched_urls:
                return
            _matched_urls.add(_url)
            _label = folder.replace("IronBrew2", "IronBrew 2").replace("Ironbrew", "Ironbrew ").replace("ClosedBeta", "Closed Beta")
            _matched.append("[%s](%s)" % (_label, _url))

        for _k in det:
            _add_sample(_OBF_SAMPLES[_k][0])
        _detected_names = []
        if quick_hit:
            _detected_names.append(quick_hit)
        if 'data' in dir() and data.get("ok"):
            for item in data.get("top_4", []):
                _detected_names.append(item.get("label", ""))
        if 'vdata' in dir() and vdata.get("topMatch"):
            _detected_names.append(vdata["topMatch"].get("displayName", ""))
        _haystack = " ".join(_detected_names).lower()
        for _key, _folder in _OBF_SAMPLES.items():
            if _key in _haystack or _folder[0].lower() in _haystack:
                _add_sample(_folder[0])

        e = discord.Embed(color=ACCENT, timestamp=datetime.now())
        e.title = f"🔬 Detect — `{job['name']}`"
        e.description = "\n\n".join(result_lines) or "No results."
        if _matched:
            e.add_field(
                name="📁 Compare with samples",
                value="\n".join(_matched),
                inline=False,
            )
        e.set_footer(text="6Vms")
        await message.reply(embed=e, mention_author=False)
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .relua  — universal Lua deobfuscation via RELUA API ─────────────────
    if starts(".relua"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = (
                "Attach a `.lua`/`.txt` or paste a raw URL after `.relua`.\n"
                "Universal Lua deobfuscator."
            )
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            await message.reply(f"❌ couldn't fetch — `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓"); await react(message, "⏳")
        code = src_bytes.decode("utf-8", errors="ignore")

        cfg = _get_relua2_cfg(message.channel.id)
        payload = {"code": code} | cfg

        result = None
        last_err = "no response"
        for attempt in range(5):
            try:
                async with http.post(
                    "https://backshift-striking-tall.ngrok-free.dev/api/process",
                    headers={
                        "X-API-Key": "K7xP92mQa8VdL3sZ0nY5RtF1cW6uB4eH",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as r:
                    data = await r.json()
                if isinstance(data, dict):
                    if data.get("success") and data.get("output"):
                        result = data["output"]
                        result = re.sub(
                            r"(?s)^--\s*Korvahub[^\n]*\n.*?^--\s*\}\s*\n",
                            "", result, count=1, flags=re.MULTILINE,
                        )
                        break
                    last_err = data.get("output") or data.get("error") or str(data)
                    last_err = re.sub(r"\x1b\[[0-9;]*m", "", last_err)
                    last_err = last_err.replace("\x00", "").strip()[:1500]
                else:
                    last_err = "unexpected response format"
                break
            except Exception as ex:
                last_err = str(ex)[:200]
                break

        if not result:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🧩 **RELua** — `{job['name']}`\n```\n{last_err}\n```"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "⏳"); await react(message, "❌")
            return

        data_str   = _stamp_output(result)
        data_bytes = data_str.encode("utf-8")
        out_fname  = _rand_name("relua.lua")
        raw_url    = await pastefy.upload(http, out_fname, data_str)
        e = discord.Embed(color=GOOD, timestamp=datetime.now())
        e.description = (
            f"🧩 **RELua** — `{job['name']}`\n"
            f"`{len(data_str.splitlines()):,} lines` · `{len(data_bytes)/1024:.1f} KB`"
        )
        if raw_url: e.description += f"\n{raw_url}"
        e.set_footer(text="6Vms")
        tmp = TMP / f"__relua_{_stamp()}.lua"
        tmp.write_bytes(data_bytes)
        try:
            await message.reply(
                content=message.author.mention, embed=e,
                file=discord.File(str(tmp), filename=out_fname), mention_author=True,
            )
        finally:
            try: tmp.unlink()
            except OSError: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .relua2_cfg  — view / toggle relua2 settings ────────────────────────
    if starts(".relua2_cfg"):
        cfg = _get_relua2_cfg(message.channel.id)
        parts = content.split()
        if len(parts) == 1:
            lines = "\n".join(f"`{k}` = `{v}`" for k, v in cfg.items())
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.title = "RELua2 Config"
            e.description = lines or "_(defaults)_"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        key = parts[1].lower().replace("-", "_")
        if key not in RELUA2_DEFAULT_CFG:
            e = discord.Embed(color=WARN)
            e.description = f"Unknown key `{key}`"
            await message.reply(embed=e, mention_author=False)
            return
        if len(parts) >= 3:
            val = parts[2].lower()
            if isinstance(RELUA2_DEFAULT_CFG[key], bool):
                cfg[key] = val in ("true", "1", "on", "yes")
            else:
                try:
                    cfg[key] = type(RELUA2_DEFAULT_CFG[key])(val)
                except:
                    cfg[key] = RELUA2_DEFAULT_CFG[key]
        else:
            cfg[key] = not cfg[key]
        e = discord.Embed(color=GOOD, timestamp=datetime.now())
        e.description = f"`.relua2` `{key}` → `{cfg[key]}`"
        e.set_footer(text="6Vms")
        await message.reply(embed=e, mention_author=False)
        return

    # ── .relua2  — alternative RELUA API (ngrok) ────────────────────────────
    if starts(".relua2"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = (
                "Attach a `.lua`/`.txt` or paste a raw URL after `.relua2`.\n"
                "Alternative RELUA backend (ngrok)."
            )
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            await message.reply(f"❌ couldn't fetch — `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓"); await react(message, "⏳")
        code = src_bytes.decode("utf-8", errors="ignore")

        # Build request payload with per-channel config merged
        cfg = _get_relua2_cfg(message.channel.id)
        payload = {"code": code} | cfg

        result = None
        last_err = "no response"
        for attempt in range(5):
            try:
                async with http.post(
                    "https://backshift-striking-tall.ngrok-free.dev/api/process",
                    headers={
                        "X-API-Key": "K7xP92mQa8VdL3sZ0nY5RtF1cW6uB4eH",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as r:
                    data = await r.json()
                if isinstance(data, dict):
                    if data.get("success") and data.get("output"):
                        result = data["output"]
                        # Strip Korvahub header comment block
                        result = re.sub(
                            r"(?s)^--\s*Korvahub[^\n]*\n.*?^--\s*\}\s*\n",
                            "", result, count=1, flags=re.MULTILINE,
                        )
                        break
                    last_err = data.get("output") or data.get("error") or str(data)
                    # Strip ANSI / control chars
                    last_err = re.sub(r"\x1b\[[0-9;]*m", "", last_err)
                    last_err = last_err.replace("\x00", "").strip()[:1500]
                else:
                    last_err = "unexpected response format"
                break
            except Exception as ex:
                last_err = str(ex)[:200]
                break

        if not result:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🧩 **RELua2** — `{job['name']}`\n```\n{last_err}\n```"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "⏳"); await react(message, "❌")
            return

        data_str   = _stamp_output(result)
        data_bytes = data_str.encode("utf-8")
        out_fname  = _rand_name("relua2.lua")
        raw_url    = await pastefy.upload(http, out_fname, data_str)
        e = discord.Embed(color=GOOD, timestamp=datetime.now())
        e.description = (
            f"🧩 **RELua2** — `{job['name']}`\n"
            f"`{len(data_str.splitlines()):,} lines` · `{len(data_bytes)/1024:.1f} KB`\n"
            "-# 6Vms RELua2"
        )
        if raw_url: e.description += f"\n{raw_url}"
        e.set_footer(text="6Vms")
        tmp = TMP / f"__relua2_{_stamp()}.lua"
        tmp.write_bytes(data_bytes)
        try:
            await message.reply(
                content=message.author.mention, embed=e,
                file=discord.File(str(tmp), filename=out_fname), mention_author=True,
            )
        finally:
            try: tmp.unlink()
            except OSError: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .lph  — LPH AI Devirtualizer (deobfuscator.py) ─────────────────────
    if starts(".lph ") or starts(".lph\n") or message.content.strip() == ".lph":

        uid = message.author.id
        now_lph = time.time()
        last_lph = _LPH_COOLDOWNS.get(uid, 0)
        if now_lph - last_lph < 30:
            rem = int(30 - (now_lph - last_lph))
            e = discord.Embed(color=WARN, timestamp=datetime.now())
            e.description = f"⏳ `.lph` on cooldown — wait `{rem}s`"
            await message.reply(embed=e, mention_author=False, delete_after=10)
            return
        _LPH_COOLDOWNS[uid] = now_lph
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = (
                "Attach a `.lua`/`.txt` or paste a raw URL after `.lph`.\n"
                "AI-powered Luraph devirtualization (deobfuscator.py)."
            )
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            await message.reply(f"❌ couldn't fetch — `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓"); await react(message, "⏳")

        result, took = None, 0.0
        try:
            ok, data, took = await asyncio.to_thread(_run_lph_py, src_bytes, job["name"])
            if ok and isinstance(data, dict):
                result = data
        except Exception as ex:
            await message.reply(f"❌ `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "⏳"); await react(message, "❌")
            return

        if not result:
            err = str(data)[:300] if not ok else "no output"
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🧩 **LPH** — `{job['name']}`\n```\n{err}\n```"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "⏳"); await react(message, "❌")
            return

        # Build reply with up to 3 files
        files = []
        for fname in ("dumped.lua", "env.json", "summary.txt"):
            if fname in result:
                p = TMP / f"{_stamp()}_{fname}"
                p.write_bytes(result[fname])
                files.append(discord.File(str(p), filename=fname))
        if not files:
            await message.reply("❌ no output files", mention_author=False)
            await unreact(message, "⏳"); await react(message, "❌")
            return
        e = discord.Embed(color=GOOD, timestamp=datetime.now())
        e.description = (
            f"🧩 **LPH** — `{job['name']}`\n"
            f"`{took:.2f}s` · AI-powered Luraph devirtualization\n"
            "-# 6Vms LPH AI Devirtualizer"
        )
        e.set_footer(text="6Vms")
        await message.reply(content=message.author.mention, embed=e, files=files, mention_author=True)
        for p in files:
            try: p.unlink()
            except OSError: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return
    # ── .45ms  — 45ms dumper ─────────────────────────────────────────────────
    if starts(".45ms"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = (
                "Attach a `.lua`/`.txt` or paste a raw URL after `.45ms`.\n"
                "45ms Lua dumper."
            )
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            await message.reply(f"❌ couldn't fetch — `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓"); await react(message, "⏳")

        ok, data, took = await asyncio.to_thread(_run_45ms, src_bytes, job["name"])
        if ok:
            text = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else data
            text = _stamp_output(text)
            out_name = _rand_name("45ms.lua")
            raw_url  = await pastefy.upload(http, out_name, text)
            MAX_INLINE = 1500
            display = text[:MAX_INLINE]
            if len(text) > MAX_INLINE:
                display = display.rsplit("\n", 1)[0] + "\n-- ... truncated"
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = (
                f"**45ms** — `{job['name']}`\n"
                f"`{text.count(chr(10))+1:,} lines` · `{len(text)/1024:.1f} KB` · `{took:.2f}s`\n"
                f"```lua\n{display}\n```"
            )
            if raw_url: e.description += f"\n{raw_url}"
            e.set_footer(text="6Vms")
            tmp = TMP / f"__45ms_{_stamp()}.lua"
            tmp.write_bytes(text.encode() if isinstance(text, str) else text)
            try:
                await message.reply(
                    content=message.author.mention, embed=e,
                    file=discord.File(str(tmp), filename=out_name), mention_author=True,
                )
            finally:
                try: tmp.unlink()
                except OSError: pass
            await unreact(message, "⏳"); await react(message, "✅")
        else:
            err = str(data)[:300] if data else "45ms error"
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"**45ms** — `{job['name']}`\n```\n{err}\n```"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "⏳"); await react(message, "❌")
        return

    # ── .ironveil  — IronVeil deobfuscator (Node.js) ─────────────────────────
    if starts(".ironveil"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.ironveil`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            await message.reply(f"❌ couldn't fetch — `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓"); await react(message, "⏳")
        ok, result, took = await asyncio.to_thread(_run_ironveil, src_bytes, job["name"])
        if not ok:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🛡️ **IronVeil** — `{job['name']}`\n```\n{result.decode() if isinstance(result, bytes) else result}\n```"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "⏳"); await react(message, "❌")
            return
        data_str   = _stamp_output(result.decode() if isinstance(result, bytes) else result)
        data_bytes = data_str.encode()
        out_fname  = _rand_name("ironveil.lua")
        raw_url    = await pastefy.upload(http, out_fname, data_str)
        e = discord.Embed(color=GOOD, timestamp=datetime.now())
        e.description = (
            f"🛡️ **IronVeil** — `{job['name']}`\n"
            f"`{data_str.count(chr(10))+1:,} lines` · `{len(data_str)/1024:.1f} KB` · `{took:.2f}s`"
        )
        if raw_url: e.description += f"\n{raw_url}"
        e.set_footer(text="6Vms")
        tmp = TMP / f"__ironveil_{_stamp()}.lua"
        tmp.write_bytes(data_bytes)
        try:
            await message.reply(
                content=message.author.mention, embed=e,
                file=discord.File(str(tmp), filename=out_fname), mention_author=True,
            )
        finally:
            try: tmp.unlink()
            except OSError: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .lphv2  — Luraph V2 dumper (main.luau engine) ────────────────────────
    if starts(".lphv2"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt`, drop a raw link, or reply to one with `.lphv2`.\nLuraph V2 anti-tamper & VM dumper."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        raw_name = re.sub(r"(\.(lua|txt|luau))+$", "", job["name"], flags=re.I) or "script"
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"Couldn't fetch `{raw_name}` — {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓"); await react(message, "⏳")
        try:
            ok, result, took = await asyncio.to_thread(_run_luraph_v2, src_bytes, raw_name)
            if ok:
                data_bytes = result if isinstance(result, bytes) else result.encode()
                data_str   = data_bytes.decode("utf-8", errors="replace")
                out_fname  = _rand_name("lphv2.lua")
                raw_url    = await pastefy.upload(http, out_fname, data_str)
                e = discord.Embed(color=GOOD, timestamp=datetime.now())
                e.description = (
                    f"🔷 **LPH V2 Dumper** — `{raw_name}`\n"
                    f"`{len(data_str.splitlines()):,} lines` · `{len(data_bytes)/1024:.1f} KB` · `{took:.2f}s`\n"
                    "-# Inject this script in a Roblox executor to dump strings at runtime."
                )
                if raw_url: e.description += f"\n{raw_url}"
                e.set_footer(text="6Vms")
                tmp = TMP / f"__lphv2_{_stamp()}.lua"
                tmp.write_bytes(data_bytes)
                try:
                    await message.reply(content=message.author.mention, embed=e,
                                        file=discord.File(str(tmp), filename=out_fname), mention_author=True)
                finally:
                    try: tmp.unlink()
                    except OSError: pass
                await unreact(message, "⏳"); await react(message, "✅")
            else:
                e = discord.Embed(color=BAD, timestamp=datetime.now())
                e.description = f"🔷 **LPH V2 Dumper** — `{raw_name}`\n{result}"
                e.set_footer(text="6Vms")
                await message.reply(content=message.author.mention, embed=e, mention_author=True)
                await unreact(message, "⏳"); await react(message, "❌")
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🔷 **LPH V2 Dumper** — `{raw_name}`\nerror: {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            try: await message.reply(content=message.author.mention, embed=e, mention_author=True)
            except discord.HTTPException: pass
            await unreact(message, "⏳"); await react(message, "❌")
        return

    # ── .lphv5  — 6Vms Luraph 14.7/14.8 decryptor ────────────────────────────
    if starts(".lphv5"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt`, drop a raw link, or reply to one with `.lphv5`.\n6Vms Luraph 14.7/14.8 decryptor — generates a Roblox executor injection script."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        raw_name = re.sub(r"(\.(lua|txt|luau))+$", "", job["name"], flags=re.I) or "script"
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"Couldn't fetch `{raw_name}` — {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓"); await react(message, "⏳")
        try:
            ok, result, took = await asyncio.to_thread(_run_luraph_v5, src_bytes, raw_name)
            if ok:
                data_bytes = result if isinstance(result, bytes) else result.encode()
                data_str   = data_bytes.decode("utf-8", errors="replace")
                out_fname  = _rand_name("lphv5.lua")
                raw_url    = await pastefy.upload(http, out_fname, data_str)
                e = discord.Embed(color=GOOD, timestamp=datetime.now())
                e.description = (
                    f"🔓 **6Vms LPH V5** — `{raw_name}`\n"
                    f"`{len(data_str.splitlines()):,} lines` · `{len(data_bytes)/1024:.1f} KB` · `{took:.2f}s`\n"
                    "-# 6Vms Luraph 14.7/14.8 decryptor — inject in a Roblox executor."
                )
                if raw_url: e.description += f"\n{raw_url}"
                e.set_footer(text="6Vms")
                tmp = TMP / f"__lphv5_{_stamp()}.lua"
                tmp.write_bytes(data_bytes)
                try:
                    await message.reply(content=message.author.mention, embed=e,
                                        file=discord.File(str(tmp), filename=out_fname), mention_author=True)
                finally:
                    try: tmp.unlink()
                    except OSError: pass
                await unreact(message, "⏳"); await react(message, "✅")
            else:
                e = discord.Embed(color=BAD, timestamp=datetime.now())
                e.description = f"🔓 **6Vms LPH V5** — `{raw_name}`\n{result}"
                e.set_footer(text="6Vms")
                await message.reply(content=message.author.mention, embed=e, mention_author=True)
                await unreact(message, "⏳"); await react(message, "❌")
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🔓 **6Vms LPH V5** — `{raw_name}`\nerror: {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            try: await message.reply(content=message.author.mention, embed=e, mention_author=True)
            except discord.HTTPException: pass
            await unreact(message, "⏳"); await react(message, "❌")
        return

    # ── .luarmor  — 6Vms Luarmor v1/v2/v3 HTTP logger ───────────────────────
    if starts(".luarmor"):
        await react(message, "🕓"); await react(message, "⏳")
        try:
            ok, result, took = await asyncio.to_thread(_run_luarmor, "luarmor")
            if ok:
                data_bytes = result if isinstance(result, bytes) else result.encode()
                data_str   = data_bytes.decode("utf-8", errors="replace")
                out_fname  = _rand_name("luarmor_logger.luau")
                raw_url    = await pastefy.upload(http, out_fname, data_str)
                e = discord.Embed(color=GOOD, timestamp=datetime.now())
                e.description = (
                    f"📡 **6Vms Luarmor Logger**\n"
                    f"`{len(data_str.splitlines()):,} lines` · `{len(data_bytes)/1024:.1f} KB` · `{took:.2f}s`\n"
                    "-# Injects into a Roblox executor to intercept Luarmor API calls (v1/v2/v3). Cached files go to `6vms_luarmor_cache/`."
                )
                if raw_url: e.description += f"\n{raw_url}"
                e.set_footer(text="6Vms")
                tmp = TMP / f"__luarmor_{_stamp()}.luau"
                tmp.write_bytes(data_bytes)
                try:
                    await message.reply(content=message.author.mention, embed=e,
                                        file=discord.File(str(tmp), filename=out_fname), mention_author=True)
                finally:
                    try: tmp.unlink()
                    except OSError: pass
                await unreact(message, "⏳"); await react(message, "✅")
            else:
                e = discord.Embed(color=BAD, timestamp=datetime.now())
                e.description = f"📡 **6Vms Luarmor Logger**\n{result}"
                e.set_footer(text="6Vms")
                await message.reply(content=message.author.mention, embed=e, mention_author=True)
                await unreact(message, "⏳"); await react(message, "❌")
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"📡 **6Vms Luarmor Logger**\nerror: {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            try: await message.reply(content=message.author.mention, embed=e, mention_author=True)
            except discord.HTTPException: pass
            await unreact(message, "⏳"); await react(message, "❌")
        return

    # ── .luarmor2  — 6Vms Luarmor 2 logger/dumper (loadstring via text, not file) ──
    if starts(".luarmor2"):
        if not _is_premium(message.author):
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.title = "📡 6Vms Luarmor 2"
            e.description = "`.luarmor2` is **premium only** — the Luarmor 2 logger + source-rebuild path is gated. Upgrade to use it."
            e.set_footer(text="6Vms · premium only")
            await message.reply(embed=e, mention_author=False)
            return
        # return path: user attaches logged.txt (or any .txt) → rebuild source
        jobs = await gather_jobs(message)
        txt_jobs = [j for j in jobs if j["name"].lower().endswith(".txt")]
        if txt_jobs:
            job = txt_jobs[0]
            await react(message, "🕓")
            try:
                src_bytes = await fetch_source(job)
            except Exception as ex:
                await message.reply(f"❌ couldn't fetch — `{_redact(str(ex))}`", mention_author=False)
                await unreact(message, "🕓")
                return
            await unreact(message, "🕓"); await react(message, "⏳")
            data_str = None
            try:
                async with http.post(
                    "https://leakd.up.railway.app/beautify",
                    json={"code": src_bytes.decode("utf-8", errors="replace")},
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as r:
                    resp = await r.json()
                code = resp.get("code") if isinstance(resp, dict) else None
                if isinstance(code, str) and code.strip():
                    data_str = code
            except Exception as ex:
                await message.reply(f"❌ rebuild failed — `{_redact(str(ex))}`", mention_author=False)
                await unreact(message, "⏳"); await react(message, "❌")
                return
            if not data_str:
                await message.reply("❌ rebuild failed — API returned nothing", mention_author=False)
                await unreact(message, "⏳"); await react(message, "❌")
                return
            took = 0.0
            data_str   = _stamp_output(data_str)
            data_bytes = data_str.encode("utf-8")
            out_fname  = _rand_name("deobfuscated.lua")
            raw_url    = await pastefy.upload(http, out_fname, data_str)
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = (
                f"🔓 **6Vms Luarmor 2 — rebuilt source**\n"
                f"`{len(data_str.splitlines()):,} lines` · `{len(data_bytes)/1024:.1f} KB`"
            )
            if raw_url: e.description += f"\n{raw_url}"
            e.set_footer(text="6Vms")
            tmp = TMP / f"__lm2_{_stamp()}.lua"
            tmp.write_bytes(data_bytes)
            try:
                await message.reply(content=message.author.mention, embed=e,
                                    file=discord.File(str(tmp), filename=out_fname), mention_author=True)
            finally:
                try: tmp.unlink()
                except OSError: pass
            await unreact(message, "⏳"); await react(message, "✅")
            return
        payload = None
        parts = content.split(None, 1)
        arg   = parts[1].strip() if len(parts) > 1 else ""
        if arg:
            payload = arg
        else:
            ref = getattr(message, "reference", None)
            if ref and ref.resolved:
                rc = getattr(ref.resolved, "content", "") or ""
                rc = rc.strip().replace("```lua", "```").split("```")
                payload = rc[1].strip() if len(rc) >= 3 else rc[0].strip()
        payload = payload or None
        await react(message, "🕓"); await react(message, "⏳")
        try:
            ok, result, took = await asyncio.to_thread(_run_luarmor2, payload)
            if ok:
                data_bytes = result if isinstance(result, bytes) else result.encode()
                data_str   = data_bytes.decode("utf-8", errors="replace")
                out_fname  = _rand_name("luarmor2_logger.luau")
                raw_url    = await pastefy.upload(http, out_fname, data_str)
                e = discord.Embed(color=GOOD, timestamp=datetime.now())
                tutorial = (
                    "**1.** Inject this script in a Roblox executor.\n"
                    "**2.** It hooks `loadstring`, `request`, `game:HttpGet`, Instance.new, metamethods & more, "
                    "and logs every API call to **`logged.txt`** in your executor's workspace.\n"
                    "**3.** Run your **Luarmor-protected script** (paste it where `--put a luarmor script here` is, "
                    "or run it right after).\n"
                    "**4.** Attach `logged.txt` back to `.luarmor2` so we can rebuild the deobfuscated source.\n"
                    "-# You can also pass the loadstring directly as **text** in the message or reply — "
                    "no file needed."
                )
                if payload:
                    tutorial = (
                        "**1.** Inject this script in a Roblox executor.\n"
                        "**2.** It has your loadstring already embedded and will run it after the hooks are set.\n"
                        "**3.** Every API call is logged to **`logged.txt`** in your executor's workspace.\n"
                        "**4.** Attach `logged.txt` back to `.luarmor2` to rebuild the deobfuscated source."
                    )
                e.description = (
                    f"📡 **6Vms Luarmor 2 Logger**\n"
                    f"`{len(data_str.splitlines()):,} lines` · `{len(data_bytes)/1024:.1f} KB` · `{took:.2f}s`\n"
                    f"{tutorial}"
                )
                if raw_url: e.description += f"\n{raw_url}"
                e.set_footer(text="6Vms")
                tmp = TMP / f"__luarmor2_{_stamp()}.luau"
                tmp.write_bytes(data_bytes)
                try:
                    await message.reply(content=message.author.mention, embed=e,
                                        file=discord.File(str(tmp), filename=out_fname), mention_author=True)
                finally:
                    try: tmp.unlink()
                    except OSError: pass
                await unreact(message, "⏳"); await react(message, "✅")
            else:
                e = discord.Embed(color=BAD, timestamp=datetime.now())
                e.description = f"📡 **6Vms Luarmor 2 Logger**\n{result}"
                e.set_footer(text="6Vms")
                await message.reply(content=message.author.mention, embed=e, mention_author=True)
                await unreact(message, "⏳"); await react(message, "❌")
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"📡 **6Vms Luarmor 2 Logger**\nerror: {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            try: await message.reply(content=message.author.mention, embed=e, mention_author=True)
            except discord.HTTPException: pass
            await unreact(message, "⏳"); await react(message, "❌")
        return

    # ── .funcdumper  — 6Vms Function Dumper (executor script) ───────────────
    if starts(".funcdumper"):
        await react(message, "🕓"); await react(message, "⏳")
        try:
            ok, result, took = await asyncio.to_thread(_run_funcdumper, "funcdumper")
            if ok:
                data_bytes = result if isinstance(result, bytes) else result.encode()
                data_str   = data_bytes.decode("utf-8", errors="replace")
                out_fname  = _rand_name("funcdumper.luau")
                raw_url    = await pastefy.upload(http, out_fname, data_str)
                e = discord.Embed(color=GOOD, timestamp=datetime.now())
                e.description = (
                    f"🧬 **6Vms Function Dumper**\n"
                    f"`{len(data_str.splitlines()):,} lines` · `{len(data_bytes)/1024:.1f} KB` · `{took:.2f}s`\n"
                    "-# Injects into a Roblox executor, then run `Funcdump(SomeFunction)` to decompile it. Bundled FunctionDecompiler (xAPI fork)."
                )
                if raw_url: e.description += f"\n{raw_url}"
                e.set_footer(text="6Vms")
                tmp = TMP / f"__funcdumper_{_stamp()}.luau"
                tmp.write_bytes(data_bytes)
                try:
                    await message.reply(content=message.author.mention, embed=e,
                                        file=discord.File(str(tmp), filename=out_fname), mention_author=True)
                finally:
                    try: tmp.unlink()
                    except OSError: pass
                await unreact(message, "⏳"); await react(message, "✅")
            else:
                e = discord.Embed(color=BAD, timestamp=datetime.now())
                e.description = f"🧬 **6Vms Function Dumper**\n{result}"
                e.set_footer(text="6Vms")
                await message.reply(content=message.author.mention, embed=e, mention_author=True)
                await unreact(message, "⏳"); await react(message, "❌")
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🧬 **6Vms Function Dumper**\nerror: {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            try: await message.reply(content=message.author.mention, embed=e, mention_author=True)
            except discord.HTTPException: pass
            await unreact(message, "⏳"); await react(message, "❌")
        return

    # ── .funcdumper2  — 6Vms Game Structure Dumper ──────────────────────────────
    if starts(".funcdumper2"):
        await react(message, "🕓"); await react(message, "⏳")
        try:
            ok, result, took = await asyncio.to_thread(_run_funcdumper2, "funcdumper2")
            if ok:
                data_bytes = result if isinstance(result, bytes) else result.encode()
                data_str   = data_bytes.decode("utf-8", errors="replace")
                out_fname  = _rand_name("funcdumper2.luau")
                raw_url    = await pastefy.upload(http, out_fname, data_str)
                e = discord.Embed(color=GOOD, timestamp=datetime.now())
                e.description = (
                    f"🗺️ **6Vms Game Structure Dumper**\n"
                    f"`{len(data_str.splitlines()):,} lines` · `{len(data_bytes)/1024:.1f} KB` · `{took:.2f}s`\n"
                    "-# Injects into a Roblox executor: dumps the full game tree + vulnerability "
                    "analysis (WRITABLE_VALUE, CALLABLE_RF, API_SURFACE, NAKED_REMOTE, "
                    "REMOTE_VALUE_PAIR, READABLE_MODULE, ANTICHEAT) with inline verification."
                )
                if raw_url: e.description += f"\n{raw_url}"
                e.set_footer(text="6Vms")
                tmp = TMP / f"__funcdumper2_{_stamp()}.luau"
                tmp.write_bytes(data_bytes)
                try:
                    await message.reply(content=message.author.mention, embed=e,
                                        file=discord.File(str(tmp), filename=out_fname), mention_author=True)
                finally:
                    try: tmp.unlink()
                    except OSError: pass
                await unreact(message, "⏳"); await react(message, "✅")
            else:
                e = discord.Embed(color=BAD, timestamp=datetime.now())
                e.description = f"🗺️ **6Vms Game Structure Dumper**\n{result}"
                e.set_footer(text="6Vms")
                await message.reply(content=message.author.mention, embed=e, mention_author=True)
                await unreact(message, "⏳"); await react(message, "❌")
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🗺️ **6Vms Game Structure Dumper**\nerror: {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            try: await message.reply(content=message.author.mention, embed=e, mention_author=True)
            except discord.HTTPException: pass
            await unreact(message, "⏳"); await react(message, "❌")
        return

    # ── .simplespy  — 6Vms SimpleSpy RemoteSpy + tutorial ──────────────────────
    if starts(".simplespy"):
        await react(message, "🕓"); await react(message, "⏳")
        try:
            ok, result, took = await asyncio.to_thread(_run_simplespy, "simplespy")
            if ok:
                data_bytes = result if isinstance(result, bytes) else result.encode()
                data_str   = data_bytes.decode("utf-8", errors="replace")
                out_fname  = _rand_name("simplespy.luau")
                raw_url    = await pastefy.upload(http, out_fname, data_str)
                e = discord.Embed(color=GOOD, timestamp=datetime.now())
                e.description = (
                    f"🕵️ **6Vms SimpleSpy (RemoteSpy)**\n"
                    f"`{len(data_str.splitlines()):,} lines` · `{len(data_bytes)/1024:.1f} KB` · `{took:.2f}s`\n"
                    "-# Inject into a Roblox executor to log all RemoteEvents & RemoteFunctions.\n"
                    "Includes full tutorial with usage examples."
                )
                if raw_url: e.description += f"\n{raw_url}"
                e.set_footer(text="6Vms")
                tmp = TMP / f"__simplespy_{_stamp()}.luau"
                tmp.write_bytes(data_bytes)
                try:
                    await message.reply(content=message.author.mention, embed=e,
                                        file=discord.File(str(tmp), filename=out_fname), mention_author=True)
                finally:
                    try: tmp.unlink()
                    except OSError: pass
                await unreact(message, "⏳"); await react(message, "✅")
            else:
                e = discord.Embed(color=BAD, timestamp=datetime.now())
                e.description = f"🕵️ **6Vms SimpleSpy**\n{result}"
                e.set_footer(text="6Vms")
                await message.reply(content=message.author.mention, embed=e, mention_author=True)
                await unreact(message, "⏳"); await react(message, "❌")
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🕵️ **6Vms SimpleSpy**\nerror: {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            try: await message.reply(content=message.author.mention, embed=e, mention_author=True)
            except discord.HTTPException: pass
            await unreact(message, "⏳"); await react(message, "❌")
        return


    # ── .status  — Queue & worker health ────────────────────────────────────────
    if starts(".status"):
        health = _queue_health_check()
        e = discord.Embed(color=ACCENT, timestamp=datetime.now())
        e.title = "📊 6Vms Queue & Worker Status"
        desc = (
            f"**Queue:** `{health['queue_size']}/{health['capacity']}` ({health['utilization']})\n"
            f"**Uptime:** {health['uptime']}\n"
            f"**Workers:** {health['workers']}/{WORKER_COUNT}\n"
            f"**Queued:** {health['total_queued']} | **Processed:** {health['total_processed']} | **Failed:** {health['total_failed']} | **Rejected:** {health['total_rejected']}\n"
        )
        if health['worker_stats']:
            desc += "\n**Worker Details:**\n"
            for wid, ws in health['worker_stats'].items():
                desc += f"  `worker_{wid}`: {ws['jobs']} done, {ws['failed']} failed, current: {ws['current'] or 'idle'}\n"
        if health['per_user_active']:
            desc += "\n**Active per-user:**\n"
            for uid, cnt in health['per_user_active'].items():
                if cnt > 0:
                    desc += f"  `<@{uid}>`: {cnt}\n"
        e.description = desc
        e.set_footer(text="6Vms")
        await message.reply(embed=e, mention_author=False)
        return

    # ── .aicfg  — AI model / settings panel ──────────────────────────────────
    if starts(".aicfg"):
        parts = content.split(None, 1)
        arg = parts[1].strip().lower() if len(parts) > 1 else ""
        if arg:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            if arg in ("list", "ls", "models"):
                lines = []
                for m in _AI_MODELS:
                    mark = "▶ " if m == _ai_cfg["model"] else "• "
                    lines.append(f"{mark}`{m}`")
                e.title = "🤖 NVIDIA Models"
                e.description = (
                    f"Current: `{_ai_cfg['model']}`\n\n"
                    + "\n".join(lines)
                    + "\n\nUse `.aicfg <model-id>` to set any model, or `.aicfg` for the panel."
                )
            elif arg in ("reset", "default", "resetdefault"):
                _ai_cfg["model"] = "deepseek-ai/deepseek-v4-flash-0731"
                _save_ai_cfg()
                e.description = f"✅ Model reset to `{_ai_cfg['model']}`"
            else:
                model_id = parts[1].strip()
                if not model_id.replace("/", "").replace(".", "").replace("-", "").replace("_", "").isalnum():
                    e.description = "Invalid model ID — e.g. `.aicfg meta/llama-3.3-70b-instruct`"
                else:
                    _ai_cfg["model"] = model_id
                    _save_ai_cfg()
                    e.color = GOOD
                    e.description = (
                        f"✅ Model set to `{_ai_cfg['model']}`\n"
                        f"Use `.aicfg list` to see presets, or `.aicfg` for the full panel."
                    )
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        panel = AICfgPanel()
        panel_msg = await message.reply(embed=panel.build_embed(), view=panel, mention_author=False)
        panel.message = panel_msg
        return

    # ── .luraphdeobf  — Luau VMP / Luraph v14.x devirtualizer (BETA) ──────────
    if starts(".luraphdeobf"):
        uid = message.author.id
        if not _is_premium(message.author):
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.title = "🔐 LuraphDeobf — BETA"
            e.description = "`.luraphdeobf` is **premium only** — upgrade to use it."
            e.set_footer(text="6Vms · premium only")
            await message.reply(embed=e, mention_author=False)
            return
        now_lv = time.time()
        last_lv = _LURAPH_COOLDOWNS.get(uid, 0)
        cooldown = 300
        if now_lv - last_lv < cooldown:
            rem = int(cooldown - (now_lv - last_lv))
            e = discord.Embed(color=WARN, timestamp=datetime.now())
            e.title = "🧬 LuraphDeobf — BETA"
            e.description = f"⏳ `.luraphdeobf` on cooldown — wait `{rem}s`"
            e.set_footer(text="6Vms · premium 5 min")
            await message.reply(embed=e, mention_author=False, delete_after=10)
            return
        _LURAPH_COOLDOWNS[uid] = now_lv
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.title = "🧬 LuraphDeobf — BETA"
            e.description = (
                "Attach a `.lua`/`.txt` or paste a raw URL after `.luraphdeobf`.\n"
                "🧪 **Beta** — Luraph v14.x VM devirtualization via luau-vmp-deobf."
            )
            e.set_footer(text="6Vms · premium only")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        fetched = []
        for j in jobs:
            try:
                fetched.append((j, await fetch_source(j)))
            except Exception as ex:
                e = discord.Embed(color=BAD, timestamp=datetime.now())
                e.title = "🔓 LuraphDeobf — BETA"
                e.description = f"❌ couldn't fetch `{j['name']}` — `{_redact(str(ex))}`"
                e.set_footer(text="6Vms · premium only")
                await message.reply(embed=e, mention_author=False)
                await unreact(message, "🕓")
                return
        if not fetched:
            await unreact(message, "🕓")
            return

        # ── runtime dump return path: protos.tsv + strings.txt pair ───────
        proto_item = strings_item = None
        for j, b in fetched:
            head = b[:512].decode("utf-8", "replace").lstrip("\ufeff \t\r\n")
            if proto_item is None and head.startswith("codefield\t"):
                proto_item = (j, b)
            elif strings_item is None and re.match(r"^\s*\[\d+\]\s*\[string\]", head):
                strings_item = (j, b)
        if proto_item is not None and strings_item is not None and proto_item[0] is not strings_item[0]:
            await unreact(message, "🕓"); await react(message, "⏳")
            ok, result, took = await asyncio.to_thread(
                _run_luraph_dumpdevirt, proto_item[1], strings_item[1], "runtime_dump"
            )
            if not ok:
                e = discord.Embed(color=WARN, timestamp=datetime.now())
                e.title = "🔓 LuraphDeobf — Runtime Devirt"
                e.description = (
                    f"**`{proto_item[0]['name']}` + `{strings_item[0]['name']}`** — `{took:.1f}s`\n"
                    f"```\n{result.decode() if isinstance(result, bytes) else result}\n```\n"
                    "-# Dumps parsed, but no VM instructions were recovered from them."
                )
                e.set_footer(text="6Vms · premium only")
                await message.reply(embed=e, mention_author=False)
                await unreact(message, "⏳"); await react(message, "❌")
                return
            files = result["files"]
            best = result.get("best") or files[0]["name"]
            best_bytes = next((f["bytes"] for f in files if f["name"] == best), files[0]["bytes"])
            best_str = _stamp_output(best_bytes.decode("utf-8", errors="replace"))
            raw_url = await pastefy.upload(http, _rand_name("luraphdeobf.luau"), best_str)
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.title = "🔓 LuraphDeobf — Runtime Devirt"
            e.description = (
                f"✅ **runtime devirtualization complete** — `{took:.1f}s`\n"
                f"**`{proto_item[0]['name']}` + `{strings_item[0]['name']}`** → **{len(files)} file(s)**\n"
                "-# Extracted from live VM memory — works on **any** Luraph version/build."
            )
            if raw_url: e.description += f"\n{raw_url}"
            e.set_footer(text="6Vms · premium only")
            tmp_files = []
            for i, f in enumerate(files):
                if len(f["bytes"]) > 25 * 1024 * 1024:
                    continue
                tmp_path = TMP / f"__luraphdeobf_{_stamp()}_{i}__{f['name'].split('/')[-1]}"
                tmp_path.write_bytes(f["bytes"])
                tmp_files.append((tmp_path, f["name"].split("/")[-1]))
            try:
                await message.reply(
                    content=message.author.mention, embed=e,
                    files=[discord.File(str(tf[0]), filename=tf[1]) for tf in tmp_files[:10]],
                    mention_author=True,
                )
                for i in range(10, len(tmp_files), 10):
                    await message.reply(
                        content=message.author.mention,
                        files=[discord.File(str(tf[0]), filename=tf[1]) for tf in tmp_files[i:i + 10]],
                        mention_author=True,
                    )
            finally:
                for tp, _ in tmp_files:
                    try: tp.unlink()
                    except OSError: pass
            await unreact(message, "⏳"); await react(message, "✅")
            return

        job, src_bytes = fetched[0]
        await unreact(message, "🕓"); await react(message, "⏳")
        res = await asyncio.to_thread(_run_luraph_vmp, src_bytes, job["name"])
        ok, result, took = res[0], res[1], res[2]
        if not ok:
            # fail-open: still hand the user the runtime logger + tutorial
            logger = _build_runtime_logger(src_bytes)
            lp = TMP / f"__luraphdeobf_{_stamp()}_logger.luau"
            lp.write_bytes(logger)
            e = discord.Embed(color=WARN, timestamp=datetime.now())
            e.title = "🔓 LuraphDeobf — Runtime Logger"
            e.description = (
                f"**`{job['name']}`** — `{took:.1f}s`\n"
                "Static engine couldn't start, so we handed you the **runtime logger** —\n"
                "extracts the real VM from a live executor. Way better than `.lphv5`.\n\n"
                "**How to run (tutorial):**\n"
                "1️⃣ `6vms_luraphdump.luau` is attached.\n"
                "2️⃣ Open a Roblox executor (Wave, Solara, Xeno, Fluxus…) and inject.\n"
                "3️⃣ Run the script — it executes your file and snapshots the VM from memory.\n"
                "4️⃣ Wait 2–4 min. Console shows `[dump] phase 1 → 6` progress.\n"
                "5️⃣ Dumps are written to your executor workspace:\n"
                "    `luraph_protos_dump.tsv` · `luraph_strings_dump.txt` · `luraph_metadata.txt`\n"
                "6️⃣ Reply here with **both** `luraph_protos_dump.tsv` + `luraph_strings_dump.txt`\n"
                "    and run `.luraphdeobf` again — we devirtualize them into pseudo-source + flow."
            )
            e.set_footer(text="6Vms · premium only · runtime logger v9")
            try:
                await message.reply(
                    content=message.author.mention, embed=e,
                    file=discord.File(str(lp), filename=_rand_name("luraphdump.luau")),
                    mention_author=True,
                )
            finally:
                try: lp.unlink()
                except OSError: pass
            await unreact(message, "⏳"); await react(message, "❌")
            return
        files = result["files"]
        engine_log = re.sub(r"\x1b\[[0-9;]*m", "", result.get("log") or "")
        best = result.get("best") or "program.decompiled.luau"
        partial = bool(result.get("partial"))
        logger = result.get("logger")

        # ── runtime logger mode: static devirt got nothing, hand over the dump script ─
        if partial and best in ("forced.beautified.lua", "original.lua"):
            tmp_files = []
            if logger:
                lp = TMP / f"__luraphdeobf_{_stamp()}_logger.luau"
                lp.write_bytes(logger)
                tmp_files.append((lp, _rand_name("luraphdump.luau")))
            for f in files:
                if f["name"] in ("forced.beautified.lua", "original.lua"):
                    if len(f["bytes"]) > 25 * 1024 * 1024:
                        continue
                    tmp_path = TMP / f"__luraphdeobf_{_stamp()}_{len(tmp_files)}__{f['name'].split('/')[-1]}"
                    tmp_path.write_bytes(f["bytes"])
                    tmp_files.append((tmp_path, f["name"].split("/")[-1]))
            e = discord.Embed(color=WARN, timestamp=datetime.now())
            e.title = "🔓 LuraphDeobf — Runtime Logger"
            e.description = (
                f"**`{job['name']}`** — `{took:.1f}s`\n"
                "This loader didn't decrypt statically, so we switched to **runtime extraction** —\n"
                "works on **ANY** Luraph version, way better than `.lphv5`.\n\n"
                "**How to run (tutorial):**\n"
                "1️⃣ `6vms_luraphdump.luau` is attached.\n"
                "2️⃣ Open a Roblox executor (Wave, Solara, Xeno, Fluxus…) and inject.\n"
                "3️⃣ Run the script — it executes your file and snapshots the VM from memory.\n"
                "4️⃣ Wait 2–4 min. Console shows `[dump] phase 1 → 6` progress.\n"
                "5️⃣ Dumps are written to your executor workspace:\n"
                "    `luraph_protos_dump.tsv` · `luraph_strings_dump.txt` · `luraph_metadata.txt`\n"
                "6️⃣ Reply here with **both** `luraph_protos_dump.tsv` + `luraph_strings_dump.txt`\n"
                "    and run `.luraphdeobf` again — we devirtualize them into pseudo-source + flow.\n\n"
            )
            failed_note = (result.get("failed_note") or "").strip()
            if failed_note:
                e.description += "**engine note:**\n```md\n" + failed_note[:2000] + "\n```\n"
            e.description += "Also attached: beautified copy of your input."
            e.set_footer(text="6Vms · premium only · runtime logger v9")
            try:
                await message.reply(
                    content=message.author.mention, embed=e,
                    files=[discord.File(str(tf[0]), filename=tf[1]) for tf in tmp_files[:10]],
                    mention_author=True,
                )
            finally:
                for tp, _ in tmp_files:
                    try: tp.unlink()
                    except OSError: pass
            await unreact(message, "⏳"); await react(message, "✅")
            return

        # ── full (or partial-with-real-source) run: pastefy best + attach everything ─
        best_bytes = next((f["bytes"] for f in files if f["name"] == best), files[0]["bytes"])
        best_str = _stamp_output(best_bytes.decode("utf-8", errors="replace"))
        raw_url = await pastefy.upload(http, _rand_name("luraphdeobf.luau"), best_str)

        log_lines = [l.strip() for l in engine_log.splitlines() if l.strip()]
        summary = log_lines[-1] if log_lines else ""
        e = discord.Embed(color=WARN if partial else GOOD, timestamp=datetime.now())
        e.title = "🧬 LuraphDeobf — BETA"
        e.description = (
            f"**`{job['name']}`** — `{took:.1f}s` · **{len(files)} output file(s)**\n"
            f"`{summary}`\n"
        )
        failed_note = (result.get("failed_note") or "").strip()
        if failed_note:
            e.description += "\n```md\n" + failed_note[:3000] + "\n```\n"
        if partial:
            e.description += "-# ⚠️ Partial run — engine hit errors but logged everything it captured\n"
        e.description += "-# Beta — Luraph v14.x VM devirtualizer (luau-vmp-deobf)"
        if raw_url: e.description += f"\n{raw_url}"
        e.set_footer(text="6Vms · premium only")

        # full engine log, attached so nothing is lost
        log_txt = "".join(log_line + "\n" for log_line in engine_log.splitlines())
        tmp_files = []
        if log_txt.strip():
            lp = TMP / f"__luraphdeobf_{_stamp()}_pipeline.log.txt"
            lp.write_text(log_txt, encoding="utf-8")
            tmp_files.append((lp, "pipeline.log.txt"))
        if logger:
            lgp = TMP / f"__luraphdeobf_{_stamp()}_logger.luau"
            lgp.write_bytes(logger)
            tmp_files.append((lgp, _rand_name("luraphdump.luau")))
        for i, f in enumerate(files):
            if len(f["bytes"]) > 25 * 1024 * 1024:
                continue  # too big for a Discord attachment
            tmp_path = TMP / f"__luraphdeobf_{_stamp()}_{i}__{f['name'].split('/')[-1]}"
            tmp_path.write_bytes(f["bytes"])
            tmp_files.append((tmp_path, f["name"].split("/")[-1]))

        def _as_file(tf):
            return discord.File(str(tf[0]), filename=tf[1])

        try:
            await message.reply(
                content=message.author.mention, embed=e,
                files=[_as_file(tf) for tf in tmp_files[:10]],
                mention_author=True,
            )
            for i in range(10, len(tmp_files), 10):
                chunk = tmp_files[i:i + 10]
                await message.reply(
                    content=message.author.mention,
                    files=[_as_file(tf) for tf in chunk],
                    mention_author=True,
                )
        finally:
            for tp, _ in tmp_files:
                try: tp.unlink()
                except OSError: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .luraphdeobf2  — static Luraph constants dump (6Vms layout) ──────────
    if starts(".luraphdeobf2"):
        uid = message.author.id
        if not _is_premium(message.author):
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.title = "🔩 Luraph Consts"
            e.description = "`.luraphdeobf2` is **premium only** — the static Luraph constant extractor is gated. Upgrade to use it."
            e.set_footer(text="6Vms · premium only")
            await message.reply(embed=e, mention_author=False)
            return
        now_lc = time.time()
        last_lc = _LURAPH_COOLDOWNS.get(uid, 0)
        cooldown = 60
        if now_lc - last_lc < cooldown:
            rem = int(cooldown - (now_lc - last_lc))
            e = discord.Embed(color=WARN, timestamp=datetime.now())
            e.title = "🔩 Luraph Consts"
            e.description = f"⏳ `.luraphdeobf2` on cooldown — wait `{rem}s`"
            e.set_footer(text="6Vms · premium 1 min")
            await message.reply(embed=e, mention_author=False, delete_after=10)
            return
        _LURAPH_COOLDOWNS[uid] = now_lc
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.title = "🔩 Luraph Consts"
            e.description = (
                "Attach a `.lua`/`.txt` or paste a raw URL after `.luraphdeobf2`.\n"
                "🔩 Extracts per-prototype constants (strings / numbers / booleans / imports / closures) "
                "in the 6Vms `luraph-constants.lua` layout — fully static, payload never runs."
            )
            e.set_footer(text="6Vms · premium only")
            await message.reply(embed=e, mention_author=False)
            return
        await react(message, "🕓")
        fetched = []
        fetch_failed = []
        for j in jobs:
            try:
                fetched.append((j, await fetch_source(j)))
            except Exception as ex:
                fetch_failed.append(f"`{j['name']}` — {_redact(str(ex))}")
        if not fetched:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.title = "🔩 Luraph Consts"
            e.description = "❌ couldn't fetch any attached file" + (
                ":\n" + "\n".join(fetch_failed[:5]) if fetch_failed else "."
            )
            e.set_footer(text="6Vms · premium only")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "🕓")
            return

        # ── runtime dump return path: constants.tsv OR protos.tsv + strings.txt ──
        # Same pair `.luraphdeobf` accepts — when the proto dump is returned the
        # logger's constants rows don't exist, so we recover strings only.
        # The TSV is optional: if only strings.txt comes back, render that.
        constants_item = strings_item = None
        for j, b in fetched:
            head = b[:512].decode("utf-8", "replace").lstrip("\ufeff \t\r\n")
            if constants_item is None and (head.startswith("codefield\t")
                                           or "type\tvalue" in head
                                           or head.startswith("number:")
                                           or head.startswith("string_hex:")):
                constants_item = (j, b)
            elif strings_item is None and re.match(r"^\s*\[\d+\]\s*\[string\]", head):
                strings_item = (j, b)
        if strings_item is not None and (constants_item is None or constants_item[0] is not strings_item[0]):
            await unreact(message, "🕓"); await react(message, "⏳")
            rt_out = TMP / f"__luraphconsts_{_stamp()}_rt.lua"
            c_tmp = TMP / f"__luraphconsts_{_stamp()}_constants.tsv"
            c_tmp.write_bytes(constants_item[1] if constants_item is not None else b"")
            s_tmp = TMP / f"__luraphconsts_{_stamp()}_strings.txt"
            s_tmp.write_bytes(strings_item[1])
            try:
                cmd = [
                    sys.executable, "-m", "luauvmp", "luraph-constants-runtime",
                    str(c_tmp), str(s_tmp),
                    "-o", str(rt_out),
                ]
                ok, log, took = _run_proc(cmd, cwd=ROOT, timeout=120)
                if ok:
                    rt = rt_out
                    if rt.exists():
                        text = rt.read_text(encoding="utf-8", errors="replace")
                        counts = _luraph_consts_counts(text)
                        stamped = _stamp_output(text)
                        out_fname = _rand_name("luraph-constants.lua")
                        raw_url = await pastefy.upload(http, out_fname, stamped)
                        e = discord.Embed(color=GOOD, timestamp=datetime.now())
                        e.title = "🔩 Luraph Consts — runtime"
                        src_name = (constants_item[0]["name"] + " + " if constants_item else "") + strings_item[0]["name"]
                        lines = [
                            f"✅ **runtime constants recovered** — `{took:.1f}s`",
                            f"**`{src_name}`**",
                            f"`{counts['protos']}` protos · `{counts['strings']}` strings · "
                            f"`{counts['numbers']}` numbers · `{counts['imports']}` imports · `{counts['closures']}` closures",
                        ] if counts['strings'] else [
                            f"✅ **runtime constants recovered** — `{took:.1f}s`",
                            f"**`{src_name}`**",
                            "`strings` dump rendered (constants TSV missing or unparseable — strings-only)",
                        ]
                        if constants_item is None:
                            lines[0] = f"✅ **strings-only dump** — `{took:.1f}s` (constants TSV not found)"
                        e.description = "\n".join(lines) + "\n-# Extracted from live VM memory — works on **any** Luraph version/build."
                        if fetch_failed:
                            e.description += "\n-# ⚠️ unreadable attachments: " + "; ".join(fetch_failed[:3])
                        if raw_url: e.description += f"\n{raw_url}"
                        e.set_footer(text="6Vms · premium only")
                        tmp = TMP / f"__luraphconsts_{_stamp()}.lua"
                        tmp.write_text(stamped, encoding="utf-8")
                        try:
                            await message.reply(content=message.author.mention, embed=e,
                                                file=discord.File(str(tmp), filename=out_fname), mention_author=True)
                        finally:
                            try: tmp.unlink()
                            except OSError: pass
                        await unreact(message, "⏳"); await react(message, "✅")
                        return
                e = discord.Embed(color=BAD, timestamp=datetime.now())
                e.title = "🔩 Luraph Consts — runtime"
                e.description = f"```\n{(log or 'dump pair parsed but render failed')[:800]}\n```"
                e.set_footer(text="6Vms · premium only")
                await message.reply(embed=e, mention_author=False)
                await unreact(message, "⏳"); await react(message, "❌")
                return
            finally:
                try: c_tmp.unlink()
                except OSError: pass
                try: s_tmp.unlink()
                except OSError: pass
                try: rt_out.unlink()
                except OSError: pass

        job, src_bytes = fetched[0]
        await unreact(message, "🕓"); await react(message, "⏳")

        # ── local regex identification gate (shared _OBF_SAMPLES detector) ────
        code = src_bytes.decode("utf-8", errors="ignore")
        _det = _detect_obfuscators(code)
        gate_hit = _OBF_SAMPLES["luraph"][0] if "luraph" in _det else None
        if not gate_hit:
            e = discord.Embed(color=WARN, timestamp=datetime.now())
            e.title = "🔩 Luraph Consts"
            e.description = "Local regex `.detect` found **no Luraph markers** in this file — refusing to run the static extractor."
            e.set_footer(text="6Vms · premium only · detect gate")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "⏳"); await react(message, "⛔")
            return
        e = discord.Embed(color=ACCENT, timestamp=datetime.now())
        e.description = f"-# regex detected Luraph (`{gate_hit}`)"
        await message.reply(embed=e, mention_author=False)

        ok, result, took = await asyncio.to_thread(_run_luraph_constants, src_bytes, job["name"])
        if not ok:
            logger = _build_runtime_logger(src_bytes)
            lp = TMP / f"__luraphconsts_{_stamp()}_logger.luau"
            lp.write_bytes(logger)
            e = discord.Embed(color=WARN, timestamp=datetime.now())
            e.title = "🔩 Luraph Consts — Runtime Logger"
            e.description = (
                f"**`{job['name']}`** — `{took:.1f}s`\n"
                f"```\n{result.decode() if isinstance(result, bytes) else result}\n```\n"
                "Static extraction couldn't recover the constants, so here's the **runtime logger** —\n"
                "only the **constants + strings** dumps are needed for this command.\n\n"
                "**How to run:**\n"
                "1️⃣ `6vms_luraphdump.luau` is attached.\n"
                "2️⃣ Open a Roblox executor (Wave, Solara, Xeno, Fluxus…) and inject.\n"
                "3️⃣ Run the script — wait 2–4 min, console shows `[dump] phase 1 → 6`.\n"
                "4️⃣ Reply here with **both**:\n"
                "    `luraph_constants_dump.tsv` · `luraph_strings_dump.txt`\n"
                "    and run `.luraphdeobf2` again — we render the constants dump from them."
            )
            e.set_footer(text="6Vms · premium only · runtime logger v9")
            try:
                await message.reply(
                    content=message.author.mention, embed=e,
                    file=discord.File(str(lp), filename=_rand_name("luraphdump.luau")),
                    mention_author=True,
                )
            finally:
                try: lp.unlink()
                except OSError: pass
            await unreact(message, "⏳"); await react(message, "❌")
            return
        text = result if isinstance(result, str) else result.decode("utf-8", errors="replace")
        counts = _luraph_consts_counts(text)
        stamped = _stamp_output(text)
        out_fname = _rand_name("luraph-constants.lua")
        raw_url = await pastefy.upload(http, out_fname, stamped)
        e = discord.Embed(color=GOOD, timestamp=datetime.now())
        e.title = "🔩 Luraph Consts"
        e.description = (
            f"**`{job['name']}`** — `{took:.1f}s` · `{counts['protos']}` protos · "
            f"`{counts['strings']}` strings · `{counts['numbers']}` numbers · `{counts['imports']}` imports · `{counts['closures']}` closures\n"
            "-# Static extraction (payload never executed) — 6Vms Luraph Consts format."
        )
        if raw_url: e.description += f"\n{raw_url}"
        e.set_footer(text="6Vms · premium only")
        tmp = TMP / f"__luraphconsts_{_stamp()}.lua"
        tmp.write_text(stamped, encoding="utf-8")
        try:
            await message.reply(content=message.author.mention, embed=e,
                                file=discord.File(str(tmp), filename=out_fname), mention_author=True)
        finally:
            try: tmp.unlink()
            except OSError: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .lphv3  — LPH V3 optimized string dumper (pdump) ─────────────────────
    if starts(".lphv3"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt`, drop a raw link, or reply to one with `.lphv3`.\nLPH V3 optimized string dumper."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        raw_name = re.sub(r"(\.(lua|txt|luau))+$", "", job["name"], flags=re.I) or "script"
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"Couldn't fetch `{raw_name}` — {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓"); await react(message, "⏳")
        try:
            ok, result, took = await asyncio.to_thread(_run_pdump, src_bytes, raw_name)
            if ok:
                data_bytes = result if isinstance(result, bytes) else result.encode()
                data_str   = _stamp_output(data_bytes.decode("utf-8", errors="ignore"))
                data_bytes = data_str.encode("utf-8")
                out_fname  = _rand_name("lphv3.lua")
                raw_url    = await pastefy.upload(http, out_fname, data_str)
                e = discord.Embed(color=GOOD, timestamp=datetime.now())
                e.description = (
                    f"📋 **LPH V3 Dumper** — `{raw_name}`\n"
                    f"`{len(data_str.splitlines()):,} lines` · `{len(data_bytes)/1024:.1f} KB` · `{took:.2f}s`"
                )
                if raw_url: e.description += f"\n{raw_url}"
                e.description += (
                    "\n\n**How to use:**\n"
                    "1. Execute the `.lua` file above in your Roblox executor\n"
                    "2. Wait for it to finish running\n"
                    "3. Check your executor's **workspace folder** for `luraph_strings_dump.txt`\n"
                    "4. That file contains all the dumped strings"
                )
                e.set_footer(text="6Vms")
                tmp = TMP / f"__lphv3_{_stamp()}.lua"
                tmp.write_bytes(data_bytes)
                try:
                    await message.reply(content=message.author.mention, embed=e,
                                        file=discord.File(str(tmp), filename=out_fname), mention_author=True)
                finally:
                    try: tmp.unlink()
                    except OSError: pass
                await unreact(message, "⏳"); await react(message, "✅")
            else:
                e = discord.Embed(color=BAD, timestamp=datetime.now())
                e.description = f"📋 **LPH V3 Dumper** — `{raw_name}`\n{result}"
                e.set_footer(text="6Vms")
                await message.reply(content=message.author.mention, embed=e, mention_author=True)
                await unreact(message, "⏳"); await react(message, "❌")
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"📋 **LPH V3 Dumper** — `{raw_name}`\nerror: {_redact(str(ex))}"
            e.set_footer(text="6Vms")
            try: await message.reply(content=message.author.mention, embed=e, mention_author=True)
            except discord.HTTPException: pass
            await unreact(message, "⏳"); await react(message, "❌")
        return

    # ── .unluac  — Decompile Lua 5.1 bytecode via unluac (Java) ──────────────
    if starts(".unluac"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a compiled `.lua`/`.luac` bytecode file or paste a raw URL after `.unluac`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            await message.reply(f"❌ couldn't fetch — `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓"); await react(message, "⏳")
        ok, result, took = await asyncio.to_thread(_run_unluac, src_bytes, job["name"])
        if not ok:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"☕ **unluac** — `{job['name']}`\n```\n{result.decode() if isinstance(result, bytes) else result}\n```"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "⏳"); await react(message, "❌")
            return
        data_str   = _stamp_output(result.decode() if isinstance(result, bytes) else result)
        data_bytes = data_str.encode()
        out_fname  = _rand_name("unluac.lua")
        raw_url    = await pastefy.upload(http, out_fname, data_str)
        e = discord.Embed(color=GOOD, timestamp=datetime.now())
        e.description = (
            f"☕ **unluac** — `{job['name']}`\n"
            f"`{data_str.count(chr(10))+1:,} lines` · `{len(data_str)/1024:.1f} KB` · `{took:.2f}s`"
        )
        if raw_url: e.description += f"\n{raw_url}"
        e.set_footer(text="6Vms")
        tmp = TMP / f"__unluac_{_stamp()}.lua"
        tmp.write_bytes(data_bytes)
        try:
            await message.reply(
                content=message.author.mention, embed=e,
                file=discord.File(str(tmp), filename=out_fname), mention_author=True,
            )
        finally:
            try: tmp.unlink()
            except OSError: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .httpspy  — HTTP Spy / Debug Tool for Roblox ─────────────────────────
    if starts(".httpspy"):
        script = 'loadstring(game:HttpGet("https://pastefy.app/PYMPMLkW/raw"))();'
        e = discord.Embed(color=0x5865F2, timestamp=datetime.now())
        e.title = "🌐 HTTP Spy — Debug Tool"
        e.description = (
            "**HTTP Spy** is a Roblox debugging tool that intercepts and logs HTTP requests "
            "made by scripts. When a script makes an `HttpGet`, `HttpPost`, or similar request, "
            "this tool captures the URL, method, headers, and response — all displayed in a clean UI.\n\n"
            "**How to use:**\n"
            "1. Execute the script below in your executor\n"
            "2. A GUI window will appear. Run any script you want to inspect\n"
            "3. Click on any log entry to copy its details to your clipboard\n"
            "4. Use the filter/search to find specific requests\n\n"
            "**Note:** Originally sourced from ScriptBlox. Revamped with a new UI and additional functionality. "
            "Intended strictly for debugging purposes. Using this to crack key systems or other "
            "scripts carries a high risk of detection — use responsibly."
        )
        e.add_field(name="📜 Script", value=f"```lua\n{script}\n```", inline=False)
        e.set_footer(text="6Vms")
        await message.reply(embed=e, mention_author=False)
        try:
            tmp = TMP / f"__httpspy_{_stamp()}.lua"
            tmp.write_text(script, encoding="utf-8")
            await message.reply(
                file=discord.File(str(tmp), filename="httpspy.lua"),
                mention_author=False,
            )
            try: tmp.unlink()
            except OSError: pass
        except Exception:
            pass
        return

    # ── .mv_moonveil  — MoonVeil phase 1: build trace harness ────────────────
    if starts(".mv_moonveil"):
        parts = content.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else None
        try:
            await _mv.cmd_mv_moonveil(message, arg)
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🌙 **MoonVeil** — error: {_redact(str(ex))}"
            e.set_footer(text="6Vms · MoonVeil")
            try: await message.reply(embed=e, mention_author=False)
            except discord.HTTPException: pass
        return

    # ── .mv_trace  — MoonVeil phase 2: reconstruct from trace ────────────────
    if starts(".mv_trace"):
        parts = content.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else None
        try:
            await _mv.cmd_mv_trace(message, arg)
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🌙 **MoonVeil** — error: {_redact(str(ex))}"
            e.set_footer(text="6Vms · MoonVeil")
            try: await message.reply(embed=e, mention_author=False)
            except discord.HTTPException: pass
        return

    # ── .mv_decompile  — MoonVeil static/partial decompile ───────────────────
    if starts(".mv_decompile"):
        parts = content.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else None
        try:
            await _mv.cmd_mv_decompile(message, arg)
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🌙 **MoonVeil** — error: {_redact(str(ex))}"
            e.set_footer(text="6Vms · MoonVeil")
            try: await message.reply(embed=e, mention_author=False)
            except discord.HTTPException: pass
        return

    # ── .moonveildeobf  — MoonVeil full deobf (ALL artifacts, premium) ───────
    if starts(".moonveildeobf"):
        if not _is_premium(message.author):
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.title = "🌙 MoonVeil deobf — premium"
            e.description = "`.moonveildeobf` is **premium only** — it runs the whole MoonVeil recovery pipeline (strings · opcodes · disasm · decompiled · devirtualized) in one shot. Upgrade to use it."
            e.set_footer(text="6Vms · premium only")
            await message.reply(embed=e, mention_author=False)
            return
        parts = content.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else None
        try:
            await _mv.cmd_mv_alldeobf(message, arg)
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🌙 **MoonVeil deobf** — error: {_redact(str(ex))}"
            e.set_footer(text="6Vms · MoonVeil")
            try: await message.reply(embed=e, mention_author=False)
            except discord.HTTPException: pass
        return

    # ── .mv_cfg  — MoonVeil decompile config panel ───────────────────────────
    if starts(".mv_cfg"):
        parts = content.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else None
        try:
            await _mv.cmd_mv_cfg(message, arg)
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🌙 **MoonVeil cfg** — error: {_redact(str(ex))}"
            e.set_footer(text="6Vms · MoonVeil")
            try: await message.reply(embed=e, mention_author=False)
            except discord.HTTPException: pass
        return

    # ── .mv_status  — MoonVeil session status ────────────────────────────────
    if starts(".mv_status"):
        try:
            await _mv.cmd_mv_status(message)
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🌙 **MoonVeil** — error: {_redact(str(ex))}"
            e.set_footer(text="6Vms · MoonVeil")
            try: await message.reply(embed=e, mention_author=False)
            except discord.HTTPException: pass
        return

    # ── .mv_abort  — MoonVeil cancel pending session ─────────────────────────
    if starts(".mv_abort"):
        try:
            await _mv.cmd_mv_abort(message)
        except Exception as ex:
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🌙 **MoonVeil** — error: {_redact(str(ex))}"
            e.set_footer(text="6Vms · MoonVeil")
            try: await message.reply(embed=e, mention_author=False)
            except discord.HTTPException: pass
        return

    # ── .mv_help  — MoonVeil command reference ───────────────────────────────
    if starts(".mv_help"):
        await message.reply(embed=_mv.mv_help_embed(), mention_author=False)
        return

    # ── .obf  — obfuscate via Goofyscator API ─────────────────────────────────
    if starts(".obf"):
        jobs = await gather_jobs(message)
        if not jobs:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Attach a `.lua`/`.txt` or paste a raw URL after `.obf`."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            await message.reply(f"❌ couldn't fetch — `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓"); await react(message, "⏳")
        source = src_bytes.decode("utf-8", errors="ignore")
        obf_cfg = _get_obf_cfg(message.channel.id)
        try:
            async with http.post(
                "https://goofyscator.lua.cz/obfuscate",
                json={
                    "source": source,
                    "settings": {
                        "dontModifyBytecode": obf_cfg["dontModifyBytecode"],
                        "dontAddAntitamper":  obf_cfg["dontAddAntitamper"],
                        "encodeNumbers":      obf_cfg["encodeNumbers"],
                        "renameGlobals":      obf_cfg["renameGlobals"],
                        "generator":          obf_cfg["_generator"],
                    },
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                data = await r.json()
            obfuscated = data.get("result") if isinstance(data, dict) else None
        except Exception as ex:
            obfuscated = None
        if not obfuscated:
            await message.reply("❌ obfuscation failed — API returned nothing", mention_author=False)
            await unreact(message, "⏳"); await react(message, "❌")
            return
        data_str   = _stamp_output(obfuscated)
        data_bytes = data_str.encode("utf-8")
        out_fname  = _rand_name("obf.lua")
        raw_url    = await pastefy.upload(http, out_fname, data_str)
        e = discord.Embed(color=GOOD, timestamp=datetime.now())
        e.description = f"⚡ **Obfuscate** — `{job['name']}`\n`{len(data_bytes)/1024:.1f} KB`"
        if raw_url: e.description += f"\n{raw_url}"
        e.set_footer(text="6Vms")
        tmp = TMP / f"__obf_{_stamp()}.lua"
        tmp.write_bytes(data_bytes)
        try:
            await message.reply(embed=e, file=discord.File(str(tmp), filename=out_fname), mention_author=False)
        finally:
            try: tmp.unlink()
            except OSError: pass
        await unreact(message, "⏳"); await react(message, "✅")
        return

    # ── .obf_cfg  — obfuscator settings panel ──────────────────────────────────
    if starts(".obf_cfg"):
        panel = ObfCfgPanel(message.channel.id)
        panel_msg = await message.reply(embed=panel.build_embed(), view=panel, mention_author=False)
        panel.message = panel_msg
        return

    # ── .ai  — NVIDIA AI chat (with optional file context) ──────────────────
    if starts(".ai"):
        parts = content.split(None, 1)
        prompt = parts[1].strip() if len(parts) > 1 else ""
        files_text = ""
        if message.attachments:
            for att in message.attachments:
                if att.size > 1024 * 1024:
                    continue
                try:
                    data = await att.read()
                    text = data.decode("utf-8", errors="ignore")[:50000]
                    files_text += f"\n--- {att.filename} ---\n{text}\n"
                except Exception:
                    pass
        if not prompt and not files_text:
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = "Usage: `.ai <prompt>` — optionally attach files for context."
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        await react(message, "🕓")
        try:
            reply = await _nvidia_query(prompt, "You are a helpful AI assistant integrated into a Roblox Lua deobfuscation Discord bot. Answer concisely and accurately.", 2048)
            if reply.startswith("NVIDIA request failed") or reply.startswith("NVIDIA API key"):
                raise RuntimeError(reply)
        except Exception as ex:
            await unreact(message, "🕓"); await react(message, "❌")
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"🤖 **NVIDIA AI** — request failed\n```\n{_redact(str(ex))}\n```"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        await unreact(message, "🕓"); await react(message, "✅")
        if len(reply) > 1900:
            raw_url = await pastefy.upload(http, "ai_response.txt", reply)
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"🤖 **Groq AI** — response too long, uploaded\n{raw_url}" if raw_url else "🤖 **Groq AI** — response too long"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
        else:
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = f"🤖 **Groq AI**\n{reply}"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
        return

    # ── .help3  — alt dumpers / MoonVeil / admin ─────────────────────────────
    if starts(".help3"):
        e = discord.Embed(color=ACCENT, timestamp=datetime.now())
        e.title = "6Vms — Help 3 · Alt Dumpers / MoonVeil / Admin"
        e.description = (
            "```\n"
            "+ .mv_moonveil   - MoonVeil: build trace harness\n"
            "+ .mv_trace      - MoonVeil: rebuild source from trace\n"
            "+ .mv_decompile  - MoonVeil: static/partial decompile\n"
            "+ .moonveildeobf - MoonVeil: full deobf pipeline (premium)\n"
            "+ .mv_cfg        - MoonVeil config panel\n"
            "+ .mv_status     - MoonVeil session status\n"
            "+ .mv_abort      - cancel MoonVeil session\n"
            "+ .mv_help       - MoonVeil reference\n"
            "+ .obf           - obfuscate via Goofyscator API\n"
            "+ .obf2          - Prometheus-style obfuscation panel\n"
            "+ .obf_cfg       - obfuscator settings panel\n"
            "+ .antienv       - share anti-tamper/anti-debug env script\n"
            "+ .luaprot       - fetch LuaProt V2 protected payload\n"
            "+ .kolenv        - Luraph env logger / Kolenv dumper\n"
            "+ .mimic         - Mimic env logger\n"
            "+ .mimic2        - Mimic v2 env logger\n"
            "+ .old45ms       - original 45ms dumper\n"
            "+ .flamecoder    - FlameCoder v3 dumper\n"
            "+ .pengue        - Pengue env logger\n"
            "+ .polyester     - Polyester dumper / env extractor\n"
            "+ .promdeobf     - Prometheus deobfuscator\n"
            "+ .promdeobf2    - Prometheus deobfuscator v2\n"
            "+ .zala          - Zala server dumper\n"
            "+ .oldlarry      - original Larry dumper\n"
            "+ .larryv2       - Larry v2 dumper\n"
            "+ .moondeobf     - MoonSec deobfuscator\n"
            "+ .aspect        - Aspect env dump (native)\n"
            "+ .unveilkitty   - UnveilKitty dumper\n"
            "+ .decompiler    - Luau bytecode decompiler\n"
            "+ .disassembler  - Luau bytecode disassembler\n"
            "+ .devirtualize  - static VM deobfuscator\n"
            "+ .unluac        - decompile Lua 5.1 bytecode\n"
            "+ .give          - gift tokens (premium)\n"
            "+ .whitelist     - grant premium (admin)\n"
            "+ .revoke        - remove premium (admin)\n"
            "+ .genkey        - generate redeem keys (owner)\n"
            "+ .redeem        - redeem a key for tokens/premium\n"
            "+ .blacklist     - ban a user (admin)\n"
            "+ .restart       - restart bot (owner)\n"
            "```"
        )
        e.set_footer(text="6Vms · .help / .help2 for more")
        await message.reply(embed=e, mention_author=False)
        return

    # ── Alternative Dumper Commands ─────────────────────────────────────────
    if first_word in _ALT_DUMPERS:
        jobs = await gather_jobs(message)
        if not jobs:
            entry = _ALT_DUMPERS[first_word]
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.description = (
                f"Attach a `.lua`/`.txt` or paste a raw URL after `{first_word}`.\n"
                f"{entry[3]}"
            )
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            return
        job = jobs[0]
        await react(message, "🕓")
        try:
            src_bytes = await fetch_source(job)
        except Exception as ex:
            await message.reply(f"❌ couldn't fetch — `{_redact(str(ex))}`", mention_author=False)
            await unreact(message, "🕓")
            return
        await unreact(message, "🕓"); await react(message, "⏳")
        ok, data, took = await asyncio.to_thread(_run_alt_dumper, src_bytes, job["name"], first_word)
        if ok:
            text = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else data
            text = _stamp_output(text)
            out_name = _rand_name(f"{first_word[1:]}.lua")
            raw_url  = await pastefy.upload(http, out_name, text)
            MAX_INLINE = 1500
            display = text[:MAX_INLINE]
            if len(text) > MAX_INLINE:
                display = display.rsplit("\n", 1)[0] + "\n-- ... truncated"
            label = _ALT_DUMPERS[first_word][0]
            e = discord.Embed(color=GOOD, timestamp=datetime.now())
            e.description = (
                f"**{label}** — `{job['name']}`\n"
                f"`{text.count(chr(10))+1:,} lines` · `{len(text)/1024:.1f} KB` · `{took:.2f}s`\n"
                f"```lua\n{display}\n```"
            )
            if raw_url: e.description += f"\n{raw_url}"
            e.set_footer(text="6Vms")
            tmp = TMP / f"__alt_{_stamp()}.lua"
            tmp.write_bytes(text.encode() if isinstance(text, str) else text)
            try:
                await message.reply(
                    content=message.author.mention, embed=e,
                    file=discord.File(str(tmp), filename=out_name), mention_author=True,
                )
            finally:
                try: tmp.unlink()
                except OSError: pass
            await unreact(message, "⏳"); await react(message, "✅")
        else:
            err = str(data)[:300] if data else f"{first_word} error"
            e = discord.Embed(color=BAD, timestamp=datetime.now())
            e.description = f"**{first_word}** — `{job['name']}`\n```\n{err}\n```"
            e.set_footer(text="6Vms")
            await message.reply(embed=e, mention_author=False)
            await unreact(message, "⏳"); await react(message, "❌")
        return

    # ── .help  — core command list ────────────────────────────────────────────
    if starts(".help"):
        e = discord.Embed(color=ACCENT, timestamp=datetime.now())
        e.title = "6Vms — Help"
        e.description = (
            "```\n"
            "+ .d        - pick a deobfuscator panel\n"
            "+ .6vms     - 6Vms V2 env dumper (queued)\n"
            "+ .lua      - string dumper + Lua tracer\n"
            "+ .l        - 3-way deobf (main.luau + aspect)\n"
            "+ .r        - Revea env dumper\n"
            "+ .r2       - Aspect env dumper (better Revea)\n"
            "+ .l2       - 6VDumper env dumper\n"
            "+ .25ms     - 25ms env dumper\n"
            "+ .45ms     - 45ms env dumper\n"
            "+ .deobf    - Aspect dumper + AI analysis\n"
            "+ .ultra    - Aspect dumper + 2× AI polish\n"
            "+ .ultra2   - Aspect + string dump + VM scan + AI\n"
            "+ .vm       - VM/bytecode devirt report\n"
            "+ .chain    - run dumpers, AI picks best\n"
            "+ .bulk     - batch deobf up to 5 scripts\n"
            "+ .mega     - beta batch deobf (unlimited)\n"
            "+ .megalune - Lune dump + inline logic comments\n"
            "+ .decode   - universal decoder (all layers)\n"
            "+ .xor      - XOR brute-force decoder\n"
            "+ .b64      - find/decode base64\n"
            "+ .hex      - decode \\xNN hex escapes\n"
            "+ .anti     - strip anti-tamper/crash patterns\n"
            "+ .loadstring - extract loadstring payloads\n"
            "+ .diff     - unified diff between two scripts\n"
            "+ .convert  - Lua 5.1 <-> Luau converter\n"
            "+ .analyze  - deep 6-point analysis + recommendation\n"
            "+ .explain  - AI explains the code\n"
            "+ .fix      - AI fixes broken/deobfuscated code\n"
            "+ .rewrite  - AI rewrites obfuscated code clean\n"
            "+ .wat      - one-line AI summary\n"
            "+ .dump     - full forensic dump + AI\n"
            "+ .scan     - security/malware scanner\n"
            "+ .patch    - AI strips protections/watermarks\n"
            "+ .deep     - multi-pass decode->clean + rename\n"
            "+ .ai2      - generic AI chat (Ollama)\n"
            "+ .ai       - NVIDIA AI chat\n"
            "+ .aicfg    - AI model/temp/tokens panel\n"
            "+ .detect   - detect the obfuscator\n"
            "+ .get      - fetch a URL as file\n"
            "+ .upload   - upload to pastefy -> raw URL\n"
            "+ .minify   - minify via DarkLua\n"
            "+ .beautify - format via DarkLua\n"
            "+ .rename   - rename variables\n"
            "+ .byp      - bypass Linkvertise/Lootlink\n"
            "+ .delta    - bypass loot.link/Delta links\n"
            "+ .tokens   - check token balance\n"
            "+ .status   - queue & worker health\n"
            "```"
        )
        e.set_footer(text="6Vms · .help2 / .help3 · discord.gg/XEP4KMaCVH")
        await message.reply(embed=e, mention_author=False)
        return
    
    if starts(".upd"):
            e = discord.Embed(color=ACCENT, timestamp=datetime.now())
            e.title = "6Vms — Changelog"
            e.description = (
                "6Vms — HUGE UPDATE 🚀\n\n"
                "─ ⚡ LUARPH (LPH) DUMPER\n"
                "  [+] 14.7 & 14.8 luraph dumper\n"
                "  [+] Supporting v15 after update\n"
                "  [+] Fully rewritten opcode mapper for latest bytecode\n"
                "  [+] New extraction pipeline — 2× faster string dumping\n"
                "  [+] Auto anti-tamper strip before every run\n"
                "  [+] Encrypted constant resolver for 14.8\n"
                "  [+] Auto-paste when output gets too long\n\n"
                "─ 🌙 MOONVEIL\n"
                "  [+] Moonveil 1.4.5 & BETA version\n"
                "  [+] New scope tracing engine — cleaner VM output\n"
                "  [+] Auto version detection (no manual config)\n"
                "  [+] Improved bytecode recovery on BETA builds\n\n"
                "─ 🧬 ENHANCED CONSTANT/INSTRUCTION DUMPER\n"
                "  [+] Better sandbox\n"
                "  [+] 163/163 UNC (e-unc)\n"
                "  [+] 100% passed sUNC\n"
                "  [+] Currently undetected (4096 dtc checked bypassed)\n"
                "  [+] CFG/TS/VM Dumper (+more)\n"
                "  [+] New instruction-level constant resolver\n"
                "  [+] Handles nested VM environments\n"
                "  [+] Auto-fallback on unknown constant types\n\n"
                "─ 🤖 AI OVERHAUL\n"
                "  [+] .aicfg — switch NVIDIA models live (deepseek, llama, mistral, qwen, phi...)\n"
                "  [+] Pick model, temperature, max tokens & thinking from a panel\n"
                "  [+] AI devirtualizer + .ultra/.vm/.chain/.bulk use your selected model\n"
                "  [+] Up to 3× NVIDIA AI passes in the .ultra pipeline\n\n"
                "─ 🆕 NEW COMMANDS\n"
                "  [+] .aicfg — AI model switcher panel\n"
                "  [+] .lphv3 — LPH V3 optimized string dumper\n"
                "  [+] .simplespy — SimpleSpy v3 RemoteSpy generator\n"
                "  [+] .funcdumper — FunctionDecompiler (xAPI fork) builder\n"
                "  [+] .ultra .vm .chain .bulk .deobf — full AI deobf pipeline\n\n"
                "─ ⚙️ ENGINE & STABILITY\n"
                "  [+] Multi-worker queue + per-user pacing\n"
                "  [+] HTTPS-only source validation + private IP blocking\n"
                "  [+] Secret redaction + safe filename sanitizing\n"
                "  [+] .status command for queue & worker health\n\n"
                "─ 🔧 FIXES & TOUCH-UPS\n"
                "  [+] Fixed bitwise ops (& | ~ << >>) in zala, oldlarry, polyester\n"
                "  [+] Fixed double-escaped quotes in alt dumpers\n"
                "  [+] IronVeil path resolution fixed\n"
                "  [+] Cfg 'bool has no items' warning fixed\n"
                "  [+] Prometheus V2 pipeline now runs successfully"
            )
            e.set_footer(text="6Vms · discord.gg/XEP4KMaCVH")
            await message.reply(embed=e, mention_author=False)
            return

        # ── .help2  — Luraph / dumper / specialist commands ───────────────────
    if starts(".help2"):
        e = discord.Embed(color=ACCENT, timestamp=datetime.now())
        e.title = "6Vms — Help 2 · Luraph / Specialist"
        e.description = (
            "```\n"
            "+ .lph        - AI Luraph devirtualizer\n"
            "+ .lphv2      - Luraph V2 anti-tamper & VM dumper\n"
            "+ .lphv3      - LPH V3 optimized string dumper\n"
            "+ .lphv5      - Luraph 14.7/14.8 decryptor\n"
            "+ .luarmor    - Luarmor v1/v2/v3 HTTP logger\n"
            "+ .luarmor2   - Luarmor 2 logger + source rebuild\n"
            "+ .luraphdeobf  - Luraph VM devirt + runtime logger (premium)\n"
            "+ .luraphdeobf2 - Luraph static per-proto constants (premium)\n"
            "+ .ironveil   - IronVeil deobfuscator\n"
            "+ .relua      - universal deobf via RELUA API\n"
            "+ .relua2     - deobf via alt RELUA backend\n"
            "+ .relua2_cfg - relua2 per-channel config\n"
            "+ .unveilr    - UnveilR environment logger\n"
            "+ .unluac     - decompile Lua 5.1 bytecode\n"
            "+ .funcdumper - FunctionDecompiler executor script\n"
            "+ .funcdumper2 - game structure & vuln dumper\n"
            "+ .simplespy  - SimpleSpy RemoteSpy generator\n"
            "+ .keyforge   - KeyForge (ForgeVM) VM-trace deobf\n"
            "+ .junkie     - fetch junkie script\n"
            "+ .obscura    - Obscura payload capture\n"
            "+ .luaprot    - fetch LuaProt V2 protected payload\n"
            "+ .obf2       - Prometheus-style obfuscation panel\n"
            "+ .obf        - obfuscate via Goofyscator API\n"
            "+ .obf_cfg    - obfuscator settings panel\n"
            "+ .darklua    - DarkLua processing panel\n"
            "+ .cfg        - 25ms dumper settings panel\n"
            "+ .httpspy    - HTTP Spy debug script\n"
            "+ .antienv    - anti-tamper/anti-debug env script\n"
            "+ .mv_moonveil - MoonVeil trace harness\n"
            "+ .mv_trace   - MoonVeil rebuild from trace\n"
            "+ .moonveildeobf - MoonVeil full deobf pipeline (premium)\n"
            "+ .mv_cfg     - MoonVeil config panel\n"
            "+ .mv_status  - MoonVeil session status\n"
            "+ .mv_abort   - cancel MoonVeil session\n"
            "```"
        )
        e.set_footer(text="6Vms · .help / .help3 · discord.gg/XEP4KMaCVH")
        await message.reply(embed=e, mention_author=False)
        return

    # ── .tokens  — check token balance ──────────────────────────────────────
    if starts(".tokens"):
        target = message.mentions[0] if message.mentions else message.author
        uid = str(target.id)
        tokens = _get_tokens(uid)
        if _is_premium(target):
            desc = f"{target.mention} has **unlimited** tokens (premium)"
        else:
            desc = f"{target.mention} has **{tokens}** token(s)"
        e = discord.Embed(color=ACCENT, timestamp=datetime.now())
        e.description = desc
        e.set_footer(text="6Vms")
        await message.reply(embed=e, mention_author=False)
        return


if __name__ == "__main__":
    if TOKEN == "YOUR_BOT_TOKEN_HERE" or not CHANNEL_IDS:
        print("[6Vms] ERROR: Edit config.py and fill in TOKEN and CHANNEL_IDS before running.")
        input("Press Enter to exit...")
        raise SystemExit(1)
    bot.run(TOKEN)