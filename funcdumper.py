import os, sys, argparse
sys.stdout.reconfigure(encoding="utf-8")

# Bundled FunctionDecompiler.lua (Forked xAPI function decompiler)
# https://github.com/bbbbbbbbbbbbbb121/Roblox/blob/main/FunctionDecompiler.lua
_BASE = os.path.dirname(os.path.abspath(__file__))
_DECOMPSRC = os.path.join(_BASE, "FunctionDecompiler.lua")

WRAPPER_HEADER = """-- 6Vms Function Dumper @ dsc.gg/6vms
-- Bundled FunctionDecompiler.lua (Forked xAPI function decompiler)
-- Run in a Roblox executor, then: local code = Funcdump(SomeFunction)

local Decompile = (function()
"""

WRAPPER_FOOTER = """
end)()

--// 6Vms wrapper
local gv = getgenv and getgenv() or _G
gv.Funcdump = Decompile
gv.FuncDump  = Decompile
gv.Decompile = Decompile

if not getgenv then
	-- demo when run in a plain Luau env with a debug library
	local ok, s = pcall(function()
		return Decompile(function(a, b)
			return a + b
		end)
	end)
	print(ok and s or "[6Vms] Function Dumper loaded — use Funcdump(SomeFunction).")
else
	print("[6Vms] Function Dumper loaded — use Funcdump(SomeFunction) to decompile.")
end
"""

def build_bytes():
    with open(_DECOMPSRC, "r", encoding="utf-8") as f:
        src = f.read()
    return WRAPPER_HEADER + src + WRAPPER_FOOTER

def build(out_path):
    full = build_bytes()
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full)
    print("[6Vms] wrote {} ({} KB)".format(out_path, len(full) // 1024))

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="6Vms Function Dumper (executor script)")
    p.add_argument("output", nargs="?", default="funcdumper.luau")
    a = p.parse_args()
    build(a.output)
