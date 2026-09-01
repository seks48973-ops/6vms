import sys, argparse
sys.stdout.reconfigure(encoding="utf-8")

LUAU_TEMPLATE = r"""
-- 6Vms Luarmor Logger @ dsc.gg/6vms

local dir = "luarmor_cache/"
local writefile = writefile or write_file
local isfolder = isfolder or is_folder
local makefolder = makefolder or create_folder
local listfiles = listfiles or list_files
local hookfunction = hookfunction or hook_func

if not writefile or not hookfunction then return end
if not isfolder(dir) then pcall(makefolder, dir) end

local old
old = hookfunction(game.HttpGet, function(Self, Url, ...)
    if typeof(Url) == "string" and Url:find("https://api.luarmor") then
        local files = isfolder(dir) and listfiles(dir) or {}
        local content = old(Self, Url, ...)
        local fname = dir .. "file_" .. (#files + 1) .. ".lua"
        writefile(fname, content)
        return content
    end
    return old(Self, Url, ...)
end)

print("[6Vms] Luarmor logger active → " .. dir)
"""

def build_bytes(out_dir="luarmor_cache/"):
    return LUAU_TEMPLATE.replace('"luarmor_cache/"', '"' + out_dir + '"')

def build(out_path, **kwargs):
    full = build_bytes(**kwargs)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full)
    print("[6Vms] wrote {} ({} KB)".format(out_path, len(full) // 1024))

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="6Vms Luarmor Logger")
    p.add_argument("output", nargs="?", default="luarmor_logger.luau")
    p.add_argument("--out-dir", default="luarmor_cache/")
    a = p.parse_args()
    build(a.output, out_dir=a.out_dir)