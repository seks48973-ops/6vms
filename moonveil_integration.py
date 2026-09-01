"""
MoonVeil integration shim for 6Vms bot.
Bridges the moonveilvro logic into the existing bot.py event loop.
All moonveilvro modules are imported from the moonveilvro/ subdirectory.
"""

import asyncio
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

import aiohttp
import discord

# ── point imports at the moonveilvro package ──────────────────────────────────
_MV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "moonveilvro")
if _MV_DIR not in sys.path:
    sys.path.insert(0, _MV_DIR)

# lazy-import so missing deps produce a clear error at call time, not at startup
def _mv_bot():
    import importlib, sys
    if "mv_bot_mod" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "mv_bot_mod", os.path.join(_MV_DIR, "bot.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules["mv_bot_mod"] = mod
    return sys.modules["mv_bot_mod"]


# ── re-export the parts we need with stable names ────────────────────────────
#  (imported lazily to avoid circular issues at startup)
def _load():
    m = _mv_bot()
    return (
        m.handle_moonveil,
        m.handle_trace,
        m.handle_decompile,
        m.load_session,
        m.clear_session,
        m.get_config,
        m.set_config,
        m.DEFAULTS,
        m.OPTIONS,
        m.ALIASES,
        m._resolve_key,
        m._status_embed,
        m._config_embed,
        m.ConfigView,
        m.AbortView,
        m.QUEUE,
        m._looks_url,
        m.fetch_url,
    )


# ── helpers ───────────────────────────────────────────────────────────────────

def _scope(message: discord.Message) -> int:
    """Use the author's user-id as the session scope (same as moonveilvro does)."""
    return message.author.id


async def _resolve_one(message: discord.Message, arg: str | None):
    """Return (filename, data, error) from an attachment, reply, or URL arg."""
    # direct attachment
    if message.attachments:
        att = message.attachments[0]
        return att.filename, await att.read(), None
    # attachment on a replied-to message
    ref = message.reference
    if ref is not None:
        replied = ref.resolved if isinstance(ref.resolved, discord.Message) else None
        if replied is None:
            try:
                replied = await message.channel.fetch_message(ref.message_id)
            except Exception:
                replied = None
        if replied and replied.attachments:
            att = replied.attachments[0]
            return att.filename, await att.read(), None
    # URL passed as argument
    if arg:
        arg = arg.strip()
        fn, data, err = await fetch_url(arg)
        return fn, data, err
    return None, None, None


async def _resolve_all_attachments(message: discord.Message):
    """Return all attachments (own message first, then replied-to)."""
    if message.attachments:
        return list(message.attachments)
    ref = message.reference
    if ref is not None:
        replied = ref.resolved if isinstance(ref.resolved, discord.Message) else None
        if replied is None:
            try:
                replied = await message.channel.fetch_message(ref.message_id)
            except Exception:
                replied = None
        if replied and replied.attachments:
            return list(replied.attachments)
    return []


def fetch_url(url):
    """Thin async wrapper — delegates to moonveilvro's fetch_url."""
    (_, _, _, _, _, _, _, _, _, _, _, _, _, _, _, _, lu, fu) = _load()
    if not lu(url):
        async def _bad():
            return None, None, "not an http(s) URL"
        return _bad()
    return fu(url)


# ── MV config panel (adapted from moonveilvro's ConfigView) ──────────────────

class MvCfgPanel(discord.ui.View):
    """Interactive toggle panel for .mv_cfg — mirrors moonveilvro's ConfigView
    but wrapped so it lives inside the 6Vms bot process."""

    def __init__(self, scope_id: int):
        super().__init__(timeout=300)
        self.scope_id = scope_id
        self.owner_id = scope_id
        self._build()

    def _load_mods(self):
        (_, _, _, _, _, gc, sc, DEFAULTS, OPTIONS, _, _, _, _, CV, _, _, _, _) = _load()
        return gc, sc, DEFAULTS, OPTIONS

    def _build(self):
        self.clear_items()
        gc, sc, DEFAULTS, OPTIONS = self._load_mods()
        cfg = gc(self.scope_id)
        keys = list(OPTIONS.keys())
        for i, key in enumerate(keys):
            on = cfg.get(key)
            row = i // 4
            btn = discord.ui.Button(
                label=key,
                row=min(row, 3),
                style=discord.ButtonStyle.success if on else discord.ButtonStyle.secondary,
            )
            btn.callback = self._toggle_cb(key)
            self.add_item(btn)
        reset = discord.ui.Button(
            label="Reset defaults", style=discord.ButtonStyle.danger, row=4
        )
        reset.callback = self._reset_cb
        self.add_item(reset)

    def _toggle_cb(self, key):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message(
                    "This config panel isn't yours.", ephemeral=True
                )
                return
            gc, sc, DEFAULTS, OPTIONS = self._load_mods()
            sc(self.scope_id, key, not gc(self.scope_id).get(key))
            self._build()
            (_, _, _, _, _, _, _, _, _, _, _, sem, _, _, _, _, _, _) = _load()
            await interaction.response.edit_message(
                embed=self._make_embed(), view=self
            )
        return cb

    async def _reset_cb(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This config panel isn't yours.", ephemeral=True
            )
            return
        gc, sc, DEFAULTS, OPTIONS = self._load_mods()
        for k, v in DEFAULTS.items():
            sc(self.scope_id, k, v)
        self._build()
        await interaction.response.edit_message(embed=self._make_embed(), view=self)

    def _make_embed(self):
        (_, _, _, _, _, gc, _, DEFAULTS, OPTIONS, _, _, sem, _, _, _, _, _, _) = _load()
        cfg = gc(self.scope_id)
        em = discord.Embed(
            title="🌙 MoonVeil .decompile config",
            color=discord.Color.blurple(),
            description="Toggle which artifacts `.mv_decompile` sends back."
        )
        for key, (fname, desc, _t) in OPTIONS.items():
            mark = "✅" if cfg.get(key) else "❌"
            val = ("`%s` — %s" % (fname, desc)) if fname else desc
            em.add_field(name="%s  %s" % (mark, key), value=val, inline=False)
        em.set_footer(text="6Vms · MoonVeil · panel expires in 5 min")
        return em

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


# ── command handlers ──────────────────────────────────────────────────────────

async def cmd_mv_moonveil(message: discord.Message, arg: str | None):
    """`.mv_moonveil` — Phase 1: build a Roblox trace harness."""
    (hm, _, _, _, _, _, _, _, _, _, _, _, _, _, _, _, _, _) = _load()
    filename, data, err = await _resolve_one(message, arg)
    if err:
        em = discord.Embed(color=0xED4245)
        em.description = f"🌙 **MoonVeil** — couldn't fetch URL: {err}"
        em.set_footer(text="6Vms · MoonVeil")
        await message.reply(embed=em, mention_author=False)
        return
    if data is None:
        em = discord.Embed(color=0x5865F2)
        em.description = (
            "🌙 **MoonVeil** — attach a `.lua`/`.luau` file, pass a URL, "
            "or reply to a message that has one."
        )
        em.set_footer(text="6Vms · MoonVeil")
        await message.reply(embed=em, mention_author=False)
        return
    await hm(
        _scope(message), filename, data,
        lambda **kw: message.reply(mention_author=False, **kw),
    )


async def cmd_mv_trace(message: discord.Message, arg: str | None):
    """`.mv_trace` — Phase 2: reconstruct from trace file(s)."""
    (_, ht, _, _, _, _, _, _, _, _, _, _, _, _, _, _, lu, fu) = _load()
    atts = await _resolve_all_attachments(message)
    if atts:
        datas = [await a.read() for a in atts]
        name = atts[0].filename
    elif arg and lu(arg.strip()):
        fn, data, err = await fu(arg.strip())
        if err:
            em = discord.Embed(color=0xED4245)
            em.description = f"🌙 **MoonVeil** — couldn't fetch URL: {err}"
            em.set_footer(text="6Vms · MoonVeil")
            await message.reply(embed=em, mention_author=False)
            return
        datas, name = [data], fn
    else:
        em = discord.Embed(color=0x5865F2)
        em.description = (
            "🌙 **MoonVeil** — attach `moonveil_trace.txt` (or several to merge them), "
            "pass a URL, or reply to a message that has it."
        )
        em.set_footer(text="6Vms · MoonVeil")
        await message.reply(embed=em, mention_author=False)
        return
    await ht(
        _scope(message), name, datas,
        lambda **kw: message.reply(mention_author=False, **kw),
    )


async def cmd_mv_decompile(message: discord.Message, arg: str | None):
    """`.mv_decompile` — static/partial pipeline (no trace needed)."""
    (_, _, hd, _, _, _, _, _, _, _, _, _, _, _, _, _, _, _) = _load()
    filename, data, err = await _resolve_one(message, arg)
    if err:
        em = discord.Embed(color=0xED4245)
        em.description = f"🌙 **MoonVeil** — couldn't fetch URL: {err}"
        em.set_footer(text="6Vms · MoonVeil")
        await message.reply(embed=em, mention_author=False)
        return
    if data is None:
        em = discord.Embed(color=0x5865F2)
        em.description = (
            "🌙 **MoonVeil** — attach a `.lua`/`.luau` file, pass a URL "
            "(`.mv_decompile <url>`), or reply to a message that has one."
        )
        em.set_footer(text="6Vms · MoonVeil")
        await message.reply(embed=em, mention_author=False)
        return
    await hd(
        _scope(message), filename, data,
        lambda **kw: message.reply(mention_author=False, **kw),
    )


async def cmd_mv_alldeobf(message: discord.Message, arg: str | None):
    """`.moonveildeobf` — force EVERY artifact on and run the static pipeline.

    Produces strings / opcodes / disasm / decompiled / devirtualized (structured)
    in one run, unlike `.mv_decompile` which only returns what the user's cfg
    includes.
    """
    (_, _, hd, _, _, gc, sc, _, OPTIONS, _, _, _, _, _, _, _, _, _) = _load()
    filename, data, err = await _resolve_one(message, arg)
    if err:
        em = discord.Embed(color=0xED4245)
        em.description = f"🌙 **MoonVeil** — couldn't fetch URL: {err}"
        em.set_footer(text="6Vms · MoonVeil deobfuscator")
        await message.reply(embed=em, mention_author=False)
        return
    if data is None:
        em = discord.Embed(color=0x5865F2)
        em.description = (
            "🌙 **MoonVeil deobf** — attach a `.lua`/`.luau` file, pass a URL "
            "(`.moonveildeobf <url>`), or reply to a message that has one.\n"
            "Sends **all** recoverable outputs in one run: strings, opcodes, "
            "disasm, decompiled + devirtualized (structured) source."
        )
        em.set_footer(text="6Vms · MoonVeil deobfuscator")
        await message.reply(embed=em, mention_author=False)
        return
    scope = _scope(message)
    for key in OPTIONS:
        sc(scope, key, True)
    await hd(
        scope, filename, data,
        lambda **kw: message.reply(mention_author=False, **kw),
    )


async def cmd_mv_cfg(message: discord.Message, arg: str | None):
    """`.mv_cfg` — interactive config panel or inline key toggle."""
    (_, _, _, _, _, gc, sc, DEFAULTS, OPTIONS, ALIASES, rk, sem, _, _, _, _, _, _) = _load()
    scope = _scope(message)
    if not arg:
        # open panel
        panel = MvCfgPanel(scope)
        panel_msg = await message.reply(embed=panel._make_embed(), view=panel, mention_author=False)
        panel.message = panel_msg
        return
    parts = arg.strip().split()
    key_raw = parts[0]
    value_raw = parts[1] if len(parts) > 1 else None
    if key_raw.lower() == "reset":
        for k, v in DEFAULTS.items():
            sc(scope, k, v)
        panel = MvCfgPanel(scope)
        panel_msg = await message.reply(embed=panel._make_embed(), view=panel, mention_author=False)
        panel.message = panel_msg
        return
    resolved = rk(key_raw)
    if resolved is None:
        em = discord.Embed(color=0xED4245)
        em.description = (
            "🌙 **MoonVeil cfg** — unknown key `%s`.\n"
            "Valid keys: %s (or `reset`)." % (key_raw, ", ".join(OPTIONS))
        )
        em.set_footer(text="6Vms · MoonVeil")
        await message.reply(embed=em, mention_author=False)
        return
    if value_raw is None:
        newval = not gc(scope).get(resolved)
    else:
        newval = value_raw.lower() in ("on", "true", "1", "yes", "y", "enable", "enabled")
    sc(scope, resolved, newval)
    panel = MvCfgPanel(scope)
    panel_msg = await message.reply(embed=panel._make_embed(), view=panel, mention_author=False)
    panel.message = panel_msg


async def cmd_mv_status(message: discord.Message):
    """`.mv_status` — show pending trace session."""
    (_, _, _, ls, _, _, _, _, _, _, _, sem, _, _, _, Q, _, _) = _load()
    sess = ls(_scope(message))
    if not sess:
        em = discord.Embed(
            title="🌙 MoonVeil — no pending session",
            color=discord.Color.light_grey(),
            description="Run `.mv_moonveil` with a `.lua` to start.",
        )
    else:
        age = max(0, int(time.time() - sess.get("ts", 0)))
        em = discord.Embed(
            title="🌙 MoonVeil — pending trace session",
            color=discord.Color.green(),
            description="Send the harness output back with `.mv_trace`.",
        )
        em.add_field(name="file", value="`%s`" % sess["filename"][:80], inline=True)
        em.add_field(name="age", value="%dm %ds" % (age // 60, age % 60), inline=True)
    depth = Q.depth
    em.add_field(
        name="queue",
        value=("idle" if depth == 0 else "%d job(s) ahead" % depth),
        inline=True,
    )
    em.set_footer(text="6Vms · MoonVeil")
    await message.reply(embed=em, mention_author=False)


async def cmd_mv_abort(message: discord.Message):
    """`.mv_abort` — cancel pending trace session."""
    (_, _, _, _, cs, _, _, _, _, _, _, _, _, _, _, _, _, _) = _load()
    existed = cs(_scope(message))
    em = discord.Embed(
        color=discord.Color.light_grey() if existed else discord.Color.greyple(),
        description=(
            "🌙 **MoonVeil** — pending trace cancelled."
            if existed
            else "🌙 **MoonVeil** — no pending session to cancel."
        ),
    )
    em.set_footer(text="6Vms · MoonVeil")
    await message.reply(embed=em, mention_author=False)


def mv_help_embed() -> discord.Embed:
    em = discord.Embed(
        title="🌙 MoonVeil — command reference",
        color=0x5865F2,
        description=(
            "Moonveil is **environment-locked** — its VM only decrypts inside real Roblox, "
            "so the main flow is two steps.\n"
            "You can attach a file, reply to a message that has one, or pass a raw URL "
            "to any command."
        ),
    )
    em.add_field(
        name="🔑  Main flow",
        value=(
            "`.mv_moonveil` *(attach `.lua`/`.luau`)* — **step 1**: generate a Roblox trace harness.\n"
            "`.mv_trace` *(attach `moonveil_trace.txt`)* — **step 2**: devirtualize from the trace.\n\n"
            "*Run the harness in your Roblox executor; it writes `moonveil_trace.txt` to your "
            "workspace folder (~10s auto-flush). Send multiple traces to merge & raise coverage.*"
        ),
        inline=False,
    )
    em.add_field(
        name="⚙️  Extra",
        value=(
            "`.mv_decompile` *(attach `.lua`)* — static pipeline: strings / disasm / structured CFG "
            "(partial without a trace).\n"
            "`.moonveildeobf` *(premium)* — force **all** recoverable outputs in one run: "
            "strings · opcodes · disasm · decompiled · devirtualized (structured) source.\n"
            "`.mv_cfg` — toggle which `.mv_decompile` artifacts are returned "
            "(interactive panel, or `.mv_cfg <key>` / `.mv_cfg <key> on|off` / `.mv_cfg reset`).\n"
            "`.mv_status` — show your pending trace session.\n"
            "`.mv_abort` — cancel your pending trace session."
        ),
        inline=False,
    )
    em.add_field(
        name="💡  Tip",
        value=(
            "Open the script's UI and trigger its branches in-game *before* the harness "
            "flushes for higher trace coverage. Attach several `moonveil_trace.txt` files "
            "to a single `.mv_trace` to merge them."
        ),
        inline=False,
    )
    em.set_footer(text="6Vms · MoonVeil deobfuscator")
    return em
