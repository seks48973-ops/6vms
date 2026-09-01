import os, sys, argparse
sys.stdout.reconfigure(encoding="utf-8")

# 6Vms Game Structure Dumper (game_structure_dumper.txt)
# Executor-side game tree dump + vulnerability scanner (WRITABLE_VALUE,
# CALLABLE_RF, API_SURFACE, NAKED_REMOTE, REMOTE_VALUE_PAIR, READABLE_MODULE,
# ANTICHEAT detection) with inline verification.

_BASE = os.path.dirname(os.path.abspath(__file__))
_DUMPSRC = os.path.join(_BASE, "GameStructureDumper.lua")

WRAPPER_HEADER = """-- 6Vms Game Structure Dumper @ dsc.gg/6vms
-- Dumps the full game object tree + runs vulnerability analysis with
-- automated verification. Run in a Roblox executor.

"""

WRAPPER_FOOTER = """
"""

def build_bytes():
    with open(_DUMPSRC, "r", encoding="utf-8") as f:
        src = f.read()
    return WRAPPER_HEADER + src + WRAPPER_FOOTER

def build(out_path):
    full = build_bytes()
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full)
    print("[6Vms] wrote {} ({} KB)".format(out_path, len(full) // 1024))

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="6Vms Game Structure Dumper (executor script)")
    p.add_argument("output", nargs="?", default="funcdumper2.luau")
    a = p.parse_args()
    build(a.output)
