"""KeyForge (ForgeVM) trace deobfuscator wrapper.

Loads the verbatim upstream module ``keyforge_deobf`` (by @wp1d/@virelesss,
discord.gg/tbjBPTRnph) and drives it against the bot's ``lune.exe``.  This is a
VM-trace *breakdown guide*, not a full deobfuscator: it emulates the ForgeVM
interpreter under Lune and prints the register-level pseudo-source so the
virtualized code can be read back.

The upstream file originally hardcodes ``s='/home/elliot/lune'``; we patch that
module global before each run so the bot's Lune binary is used.
"""

import os
import tempfile
import time

import keyforge_deobf as _kf

MARKER = "-- KeyForge Obfuscator (ForgeVM) [https://keyforge.win]"


def is_keyforge(src_text):
    """True if the payload announces itself as KeyForge-protected."""
    return MARKER in src_text


def run_deobf(src_bytes, lune_bin="lune", timeout=60):
    """Recover the ForgeVM trace for a KeyForge payload.

    Returns ``(ok, output_text, took_seconds)``; ``output_text`` is the
    pseudo-source trace when ``ok``, otherwise the failure reason.
    """
    started = time.perf_counter()
    _kf.s = lune_bin
    in_path = None
    out_path = None
    try:
        src = src_bytes.decode("utf-8", errors="replace")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".lua",
                                         delete=False, encoding="utf-8") as fh:
            fh.write(src)
            in_path = fh.name
        out_path = in_path + ".out"
        try:
            ok = _kf.B(in_path, out_path)
        except Exception as exc:  # upstream raises on malformed input
            return False, str(exc), time.perf_counter() - started
        if not ok:
            return False, "KeyForge markers / entry point not found", time.perf_counter() - started
        with open(out_path, "r", encoding="utf-8", errors="replace") as fh:
            out = fh.read()
        return True, out, time.perf_counter() - started
    except Exception as exc:
        return False, str(exc), time.perf_counter() - started
    finally:
        for p in (in_path, out_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass