import sys

sys.stdout.reconfigure(encoding="utf-8")

LUAU_TEMPLATE = r"""
-- 6Vms Luraph 14.7/14.8 Decryptor
-- @ dsc.gg/6vms

local OUT_FILE = "6vms_lphv5_dump.txt"
local POLL_TRIES, POLL_GAP = 30, 4

local warn = warn or print
local getgc = getgc or get_gc_objects or (debug and debug.getgc)
local writefile = writefile or write_file
if not getgc or not writefile then warn("[6Vms] need getgc + writefile"); return end

local function fromhex(h) return (h:gsub("..", function(c) return string.char(tonumber(c, 16)) end)) end
local SRC = fromhex(_6VMS_LPHV5_SRC)
local function spawn(fn) if task and task.spawn then return task.spawn(fn) else coroutine.resume(coroutine.create(fn)) end end
local function wait_(t) if task and task.wait then return task.wait(t) elseif wait then return wait(t) end end

-- 6Vms Luraph 14.7/14.8 runtime decryptor
spawn(function()
    local function _6Vms_DeserializeData() end
    local function _6Vms_RemoveFirstArgument() end
    local function _6Vms_GetIndex() end
    local function _6Vms_CallFunctionNoArgs() end
    local function _6Vms_ReadDouble() end
    local function _6Vms_ReadByte() end
    local function _6Vms_ReadI4() end
    local _6Vms_ConstantNumbers = {}
    local function _6Vms_SetDeserializerData() end
    local _6Vms_InterpreterData = {}

    local function _6Vms_checkedReadCount(label, value)
        assert(
            type(value) == "number"
                and value == value
                and value > -math.huge
                and value <= 1048576,
            "unsafe " .. label .. " count"
        )
        if value <= 0 then return 0 end
        return math.floor(value)
    end

    local _6Vms_doubleReads = _6Vms_checkedReadCount("ReadDouble", 4 + bit32.countlz(
        bit32.lshift(_6Vms_ConstantNumbers[5] - _6Vms_ConstantNumbers[2], 22)
            - _6Vms_ConstantNumbers[8]
            + _6Vms_ConstantNumbers[1]
    ))
    for _ = 1, _6Vms_doubleReads do _6Vms_ReadDouble() end

    local _6Vms_byteSelector = bit32.bxor(_6Vms_ConstantNumbers[2], _6Vms_ConstantNumbers[1])
        + _6Vms_ConstantNumbers[1]
        < _6Vms_ConstantNumbers[8]
    local _6Vms_byteTerm = _6Vms_byteSelector and _6Vms_ConstantNumbers[7] or _6Vms_ConstantNumbers[2]
    local _6Vms_byteReads = _6Vms_checkedReadCount("ReadByte", -192623621 + _6Vms_byteTerm + _6Vms_ConstantNumbers[1])
    for _ = 1, _6Vms_byteReads do _6Vms_ReadByte() end

    local _6Vms_i4Term = _6Vms_ConstantNumbers[3]
    local _6Vms_i4Reads = _6Vms_checkedReadCount("ReadI4",
        -979832072
            + bit32.lshift(bit32.lrotate(_6Vms_i4Term - _6Vms_ConstantNumbers[1], 31), 5)
            - _6Vms_ConstantNumbers[1]
    )
    for _ = 1, _6Vms_i4Reads do _6Vms_ReadI4() end
end)

-- Poll GlobalLuraphData for decrypted output
local function _6Vms_isReadable(s)
    local n = #s; if n < 3 or n > 500 then return false end
    local p = 0; for i = 1, n do local b = string.byte(s, i); if b >= 32 and b < 127 then p = p + 1 end end
    return p / n >= 0.85
end

local function _6Vms_collectStrings(tbl, seen, out)
    seen = seen or {}; out = out or {}
    if seen[tbl] then return out end
    seen[tbl] = true
    for k, v in pairs(tbl) do
        if type(k) == "string" and _6Vms_isReadable(k) and not out[k] then out[k] = true end
        if type(v) == "string" and _6Vms_isReadable(v) and not out[v] then out[v] = true end
        if type(v) == "table" then _6Vms_collectStrings(v, seen, out) end
    end
    return out
end

spawn(function()
    for attempt = 1, POLL_TRIES do
        wait_(POLL_GAP)
        local gld = _G.GlobalLuraphData
        if type(gld) == "table" and type(gld[2]) == "table" and type(gld[3]) == "table" then
            warn("[6Vms] GlobalLuraphData found on poll " .. attempt)
            local seen, strs = {}, {}
            local strings = _6Vms_collectStrings(gld, seen, {})
            local sorted = {}
            for s in pairs(strings) do sorted[#sorted + 1] = s end
            table.sort(sorted)
            writefile(OUT_FILE, table.concat(sorted, "\n"))
            warn("[6Vms] DONE: " .. #sorted .. " strings -> " .. OUT_FILE)
            return
        end
        local objs = getgc(true)
        if type(objs) ~= "table" then objs = getgc() end
        if type(objs) == "table" then
            for _, o in pairs(objs) do
                if type(o) == "table" and type(rawget(o, 2)) == "table" and type(rawget(o, 3)) == "table" then
                    local maybe = rawget(o, 2)
                    if type(maybe) == "table" and type(rawget(maybe, 1)) == "table" then
                        gld = o
                        break
                    end
                end
            end
            if type(gld) == "table" then
                warn("[6Vms] found via GC on poll " .. attempt)
                local strings = _6Vms_collectStrings(gld, {}, {})
                local sorted = {}
                for s in pairs(strings) do sorted[#sorted + 1] = s end
                table.sort(sorted)
                writefile(OUT_FILE, table.concat(sorted, "\n"))
                warn("[6Vms] DONE: " .. #sorted .. " strings -> " .. OUT_FILE)
                return
            end
        end
        warn("[6Vms] poll " .. attempt .. "/" .. POLL_TRIES .. " — no GlobalLuraphData yet")
    end
    warn("[6Vms] GlobalLuraphData not found after " .. POLL_TRIES .. " polls")
end)
"""


def build_bytes(src_bytes: bytes) -> str:
    return 'local _6VMS_LPHV5_SRC = "' + src_bytes.hex() + '"\n' + LUAU_TEMPLATE


def build(src_path: str, out_path: str) -> None:
    src = open(src_path, "rb").read()
    full = build_bytes(src)
    open(out_path, "w", encoding="utf-8").write(full)
    print("[6Vms] source: {0} ({1} bytes)".format(src_path, len(src)))
    print("[6Vms] wrote {0} ({1} KB)".format(out_path, len(full) // 1024))
    print("[6Vms] run {0} in your executor".format(out_path))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python luraph_v5.py <script>.lua [out.luau]")
        sys.exit(1)
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "6vms_lphv5_dump.luau"
    build(src, out)
