import sys
import json
import urllib.request
from urllib.parse import urlparse

import requests

sys.stdout.reconfigure(
    encoding="utf-8"
)

PASTEFY_API = (
    "https://pastefy.app/api/v2/paste"
)

PASTEFY_TOKEN = "g1l8HidbaTyx9VcMcataqPl2IjmPmln1pTz3vQ5EGswq0eBWRHAbKkVFTKxG"

HARDCODED_SCRIPT_KEY = (
    "PUT_YOUR_KEY_HERE"
)

LUAU_TEMPLATE = r"""
local OUT_STRINGS = "luraph_strings_dump.txt"
local OUT_PROTOS = "luraph_protos_dump.tsv"
local OUT_OBJECTS = "luraph_objects_dump.tsv"
local OUT_RELATIONS = "luraph_relations_dump.tsv"
local OUT_CONSTANTS = "luraph_constants_dump.tsv"
local OUT_VALUES = "luraph_values_dump.tsv"
local OUT_META = "luraph_metadata.txt"

local GAME_SETTLE_TRIES = 60
local GAME_SETTLE_GAP = 2
local GAME_STABLE_REQUIRED = 5

local SCRIPT_POLL_TRIES = 40
local SCRIPT_POLL_GAP = 2
local SCRIPT_STABLE_REQUIRED = 4

local MAX_FIELD = 64
local MAX_ARRAY_SCAN = 16384
local MAX_TABLE_KEYS = 8192
local MAX_RECURSION = 10
local MAX_CANDIDATES = 50000
local MAX_FUNCTION_CONSTANTS = 16384
local MAX_STRING_LENGTH = 8192
local MAX_FUNCTION_SCAN = 50000
local MAX_VALUE_OUTPUT = 200000

local MIN_PROTO_SCORE = 10
local MIN_CODE_LENGTH = 2

local warn = warn or print

local getgc =
getgc
or get_gc_objects
or (
debug
and debug.getgc
)

local writefile =
writefile
or write_file

local getconstants =
getconstants
or get_constants

local getprotos =
getprotos
or get_protos

if not getgc then
warn("[dump] getgc is unavailable")
return
end

if not writefile then
warn("[dump] writefile is unavailable")
return
end

pcall(function()

local genv =
    (
        getgenv
        and getgenv()
    )
    or _G

genv.SCRIPT_KEY =
    "%SCRIPT_KEY%"

genv.script_key =
    "%SCRIPT_KEY%"

end)

local function fromhex(h)

return h:gsub(
    "..",
    function(c)

        return string.char(
            tonumber(c, 16)
        )

    end
)

end

local SRC =
fromhex(
_LPH_SRC
)

local function spawn(fn)

if task
    and task.spawn then

    return task.spawn(fn)

end


local co =
    coroutine.create(fn)

return coroutine.resume(co)

end

local function wait_(t)

if task
    and task.wait then

    return task.wait(t)

end


if wait then
    return wait(t)
end

end

local function get_objects()

local ok
local objects


ok, objects =
    pcall(
        function()
            return getgc(true)
        end
    )


if ok
    and type(objects)
    == "table" then

    return objects

end


ok, objects =
    pcall(
        function()
            return getgc()
        end
    )


if ok
    and type(objects)
    == "table" then

    return objects

end


return {}

end

local function get_object_count()

local objects =
    get_objects()

return #objects

end

local function snapshot_gc()

local snapshot = {}


for _, object in pairs(
    get_objects()
) do

    snapshot[object] =
        true

end


return snapshot

end

local function get_new_objects(
before
)

local result = {}


for _, object in pairs(
    get_objects()
) do

    if not before[object] then

        result[
            #result + 1
        ] =
            object

    end

end


return result

end

local function wait_for_game_settle()

warn(
    "[dump] waiting for game GC to settle"
)


local last_count =
    get_object_count()


local stable =
    0


for attempt = 1,
    GAME_SETTLE_TRIES do


    wait_(
        GAME_SETTLE_GAP
    )


    local current =
        get_object_count()


    local difference =
        math.abs(
            current
            - last_count
        )


    warn(
        string.format(
            "[dump] game settle %d/%d | gc=%d | delta=%d | stable=%d/%d",
            attempt,
            GAME_SETTLE_TRIES,
            current,
            difference,
            stable,
            GAME_STABLE_REQUIRED
        )
    )


    if difference == 0 then

        stable =
            stable + 1

    else

        stable =
            0

    end


    last_count =
        current


    if stable
        >= GAME_STABLE_REQUIRED then

        warn(
            "[dump] game GC stabilized"
        )

        return

    end

end


warn(
    "[dump] game settle timeout reached"
)

end

local function safe_rawget(
t,
k
)

local ok
local value


ok, value =
    pcall(
        rawget,
        t,
        k
    )


if ok then
    return value
end


return nil

end

local function safe_len(t)

local ok
local n


ok, n =
    pcall(
        function()
            return #t
        end
    )


if ok
    and type(n)
    == "number" then

    return n

end


return 0

end

local function safe_pairs(t)

local ok
local result


ok, result =
    pcall(
        function()

            local out = {}


            for k, v in pairs(t) do

                out[
                    #out + 1
                ] =
                    {
                        k = k,
                        v = v
                    }


                if #out
                    >= MAX_TABLE_KEYS then

                    break

                end

            end


            return out

        end
    )


if ok
    and type(result)
    == "table" then

    return result

end


return {}

end

local function hex_encode(s)

return s:gsub(
    ".",
    function(ch)

        return string.format(
            "%02x",
            string.byte(ch)
        )

    end
)

end

local function readable(s)

if type(s)
    ~= "string" then

    return false

end


if #s < 2
    or #s > MAX_STRING_LENGTH then

    return false

end


local printable =
    0


for i = 1,
    #s do


    local b =
        string.byte(
            s,
            i
        )


    if b >= 32
        and b < 127 then

        printable =
            printable + 1

    end

end


return printable / #s
    >= 0.70

end

local function safe_tostring(v)

local ok
local result


ok, result =
    pcall(
        tostring,
        v
    )


if ok then
    return result
end


return "<unprintable>"

end

local function value_signature(v)

local t =
    type(v)


if t == "nil" then
    return "nil"
end


if t == "number" then

    return "number:"
        .. safe_tostring(v)

end


if t == "string" then

    return "string_hex:"
        .. hex_encode(v)

end


if t == "boolean" then

    return "boolean:"
        .. safe_tostring(v)

end


if t == "function" then
    return "function"
end


if t == "table" then
    return "table"
end


if t == "thread" then
    return "thread"
end


if t == "userdata" then
    return "userdata"
end


return t

end

local function write_value(v)

return value_signature(v)

end

local strings = {}
local string_seen = {}

local function add_string(
s,
source
)

if not readable(s) then
    return
end


if string_seen[s] then
    return
end


string_seen[s] =
    true


strings[
    #strings + 1
] =
    {
        value = s,
        source =
            source
            or "unknown"
    }

end

local values = {}
local value_seen = {}

local function add_value(
v,
source
)

local sig =
    value_signature(v)


if not value_seen[sig] then

    value_seen[sig] =
        true


    values[
        #values + 1
    ] =
        {
            value = sig,
            source =
                source
                or "unknown"
        }

end


if type(v)
    == "string" then

    add_string(
        v,
        source
    )

end

end

local table_seen = {}
local function_seen = {}
local function_count = 0

local scan_table
local scan_function

scan_table =
function(
t,
depth,
source
)

    if type(t)
        ~= "table" then

        return

    end


    if depth
        > MAX_RECURSION then

        return

    end


    if table_seen[t] then
        return
    end


    table_seen[t] =
        true


    local entries =
        safe_pairs(t)


    for _, entry in ipairs(
        entries
    ) do


        add_value(
            entry.k,
            source
            .. ":key"
        )


        add_value(
            entry.v,
            source
            .. ":value"
        )


        if type(entry.k)
            == "table" then

            scan_table(
                entry.k,
                depth + 1,
                source
                .. ":key"
            )

        end


        if type(entry.v)
            == "table" then

            scan_table(
                entry.v,
                depth + 1,
                source
                .. ":value"
            )

        elseif type(entry.v)
            == "function" then

            scan_function(
                entry.v,
                source
                .. ":function"
            )

        end

    end

end

scan_function =
function(
fn,
source
)

    if type(fn)
        ~= "function" then

        return

    end


    if function_seen[fn] then
        return
    end


    if function_count
        >= MAX_FUNCTION_SCAN then

        return

    end


    function_seen[fn] =
        true


    function_count =
        function_count + 1


    if getconstants then

        local ok
        local constants


        ok, constants =
            pcall(
                getconstants,
                fn
            )


        if ok
            and type(constants)
            == "table" then


            local limit =
                math.min(
                    #constants,
                    MAX_FUNCTION_CONSTANTS
                )


            for i = 1,
                limit do


                local value =
                    constants[i]


                add_value(
                    value,
                    source
                    .. ":constant"
                )


                if type(value)
                    == "table" then

                    scan_table(
                        value,
                        1,
                        source
                        .. ":constant"
                    )

                end

            end

        end

    end


    if getprotos then

        local ok
        local protos


        ok, protos =
            pcall(
                getprotos,
                fn
            )


        if ok
            and type(protos)
            == "table" then


            for _, proto in ipairs(
                protos
            ) do


                if type(proto)
                    == "function" then

                    scan_function(
                        proto,
                        source
                        .. ":proto"
                    )

                end

            end

        end

    end

end

local function array_info(t)

if type(t)
    ~= "table" then

    return 0, 0, 0

end


local length =
    safe_len(t)


if length <= 0 then
    return 0, 0, 0
end


local numbers =
    0


local strings_found =
    0


local limit =
    math.min(
        length,
        MAX_ARRAY_SCAN
    )


for i = 1,
    limit do


    local value =
        safe_rawget(
            t,
            i
        )


    if type(value)
        == "number" then

        numbers =
            numbers + 1


    elseif type(value)
        == "string" then

        strings_found =
            strings_found + 1

    end

end


return length,
    numbers,
    strings_found

end

local function is_numeric_array(t)

local length
local numbers


length, numbers =
    array_info(t)


if length < 2 then
    return false
end


return numbers
    >= math.max(
        2,
        math.floor(
            length
            * 0.65
        )
    )

end

local function parallel_score(
t,
code_field
)

local code =
    safe_rawget(
        t,
        code_field
    )


if type(code)
    ~= "table" then

    return 0

end


local code_len =
    safe_len(code)


if code_len
    < MIN_CODE_LENGTH then

    return 0

end


local matching =
    0


local arrays =
    0


for f = 1,
    MAX_FIELD do


    if f
        ~= code_field then


        local value =
            safe_rawget(
                t,
                f
            )


        if type(value)
            == "table" then


            arrays =
                arrays + 1


            local len =
                safe_len(value)


            if len == code_len
                or math.abs(
                    len
                    - code_len
                ) <= 1 then

                matching =
                    matching + 1

            end

        end

    end

end


return matching * 4
    + math.min(
        arrays,
        12
    )

end

local function proto_score(
t,
code_field
)

if type(t)
    ~= "table" then

    return 0

end


local code =
    safe_rawget(
        t,
        code_field
    )


if type(code)
    ~= "table" then

    return 0

end


local length
local numbers


length, numbers =
    array_info(code)


if length
    < MIN_CODE_LENGTH then

    return 0

end


local score =
    5


if numbers
    >= math.max(
        2,
        math.floor(
            length
            * 0.65
        )
    ) then

    score =
        score + 6

end


if length >= 4 then
    score =
        score + 2
end


score =
    score
    + parallel_score(
        t,
        code_field
    )


local table_fields =
    0


local numeric_arrays =
    0


local primitive_arrays =
    0


for f = 1,
    MAX_FIELD do


    if f
        ~= code_field then


        local value =
            safe_rawget(
                t,
                f
            )


        if type(value)
            == "table" then


            table_fields =
                table_fields + 1


            if is_numeric_array(
                value
            ) then

                numeric_arrays =
                    numeric_arrays + 1

            end


            local len =
                math.min(
                    safe_len(
                        value
                    ),
                    256
                )


            if len >= 2 then


                local primitive_count =
                    0


                for i = 1,
                    len do


                    local item =
                        safe_rawget(
                            value,
                            i
                        )


                    local item_type =
                        type(item)


                    if item_type
                        == "string"
                        or item_type
                        == "number"
                        or item_type
                        == "boolean"
                        or item == nil then


                        primitive_count =
                            primitive_count
                            + 1

                    end

                end


                if primitive_count
                    >= math.max(
                        2,
                        math.floor(
                            len
                            * 0.5
                        )
                    ) then

                    primitive_arrays =
                        primitive_arrays
                        + 1

                end

            end

        end

    end

end


if table_fields >= 3 then

    score =
        score + 3

end


if numeric_arrays >= 2 then

    score =
        score + 4

end


if primitive_arrays >= 2 then

    score =
        score + 3

end


return score

end

local function find_code_field(t)

local best_field
local best_score =
    0


for f = 1,
    MAX_FIELD do


    local score =
        proto_score(
            t,
            f
        )


    if score
        > best_score then


        best_score =
            score


        best_field =
            f

    end

end


return best_field,
    best_score

end

local function dump_protos(
objects
)

local candidates =
    {}


local field_votes =
    {}


for _, object in pairs(
    objects
) do


    if type(object)
        == "table" then


        local field
        local score


        field, score =
            find_code_field(
                object
            )


        if field
            and score
            >= MIN_PROTO_SCORE then


            candidates[
                #candidates + 1
            ] =
                {
                    object = object,
                    field = field,
                    score = score
                }


            field_votes[field] =
                (
                    field_votes[field]
                    or 0
                ) + 1

        end

    end

end


if #candidates == 0 then

    return 0, 0, 0

end


table.sort(
    candidates,
    function(a, b)

        return a.score
            > b.score

    end
)


while #candidates
    > MAX_CANDIDATES do

    candidates[
        #candidates
    ] =
        nil

end


local dominant_field
local dominant_votes =
    0


for field, votes in pairs(
    field_votes
) do


    if votes
        > dominant_votes then


        dominant_field =
            field


        dominant_votes =
            votes

    end

end


if not dominant_field then

    return 0, 0, 0

end


local protos =
    {}


local seen =
    {}


for _, candidate in ipairs(
    candidates
) do


    if candidate.field
        == dominant_field
        and not seen[
            candidate.object
        ] then


        seen[
            candidate.object
        ] =
            true


        protos[
            #protos + 1
        ] =
            candidate

    end

end


local index =
    {}


for id, proto in ipairs(
    protos
) do


    index[
        proto.object
    ] =
        id

end


local constants =
    {}


local constant_seen =
    {}


local relations =
    {}


local relation_seen =
    {}


for id, proto in ipairs(
    protos
) do


    local object =
        proto.object


    local field =
        proto.field


    local code =
        safe_rawget(
            object,
            field
        )


    if type(code)
        == "table" then


        local code_len =
            safe_len(code)


        for i = 1,
            code_len do


            local opcode =
                safe_rawget(
                    code,
                    i
                )


            local sig =
                value_signature(
                    opcode
                )


            if not constant_seen[
                sig
            ] then


                constant_seen[
                    sig
                ] =
                    true


                constants[
                    #constants + 1
                ] =
                    sig

            end


            for f = 1,
                MAX_FIELD do


                if f
                    ~= field then


                    local array =
                        safe_rawget(
                            object,
                            f
                        )


                    if type(array)
                        == "table" then


                        local value =
                            safe_rawget(
                                array,
                                i
                            )


                        add_value(
                            value,
                            "proto:"
                            .. tostring(id)
                            .. ":field:"
                            .. tostring(f)
                        )


                        local value_sig =
                            value_signature(
                                value
                            )


                        if not constant_seen[
                            value_sig
                        ] then


                            constant_seen[
                                value_sig
                            ] =
                                true


                            constants[
                                #constants + 1
                            ] =
                                value_sig

                        end


                        if type(value)
                            == "table" then


                            local child =
                                index[
                                    value
                                ]


                            if child then


                                local key =
                                    id
                                    .. ":"
                                    .. child
                                    .. ":"
                                    .. i
                                    .. ":"
                                    .. f


                                if not relation_seen[
                                    key
                                ] then


                                    relation_seen[
                                        key
                                    ] =
                                        true


                                    relations[
                                        #relations + 1
                                    ] =
                                        table.concat(
                                            {
                                                id,
                                                child,
                                                i,
                                                f
                                            },
                                            "\t"
                                        )

                                end

                            end

                        end

                    end

                end

            end

        end

    end

end


local proto_out =
    {
        "codefield\t"
        .. tostring(
            dominant_field
        ),

        "roots\t"
        .. tostring(
            #protos
        )
    }


for id, proto in ipairs(
    protos
) do


    local code =
        safe_rawget(
            proto.object,
            proto.field
        )


    local code_len =
        safe_len(code)


    proto_out[
        #proto_out + 1
    ] =
        table.concat(
            {
                "proto",
                id,
                code_len,
                proto.score,
                proto.field
            },
            "\t"
        )


    for i = 1,
        code_len do


        local row =
            {
                "i",
                i,
                write_value(
                    safe_rawget(
                        code,
                        i
                    )
                )
            }


        for f = 1,
            MAX_FIELD do


            if f
                ~= proto.field then


                local array =
                    safe_rawget(
                        proto.object,
                        f
                    )


                local value


                if type(array)
                    == "table" then


                    value =
                        safe_rawget(
                            array,
                            i
                        )

                end


                row[
                    #row + 1
                ] =
                    "f"
                    .. tostring(f)
                    .. "="
                    .. write_value(
                        value
                    )

            end

        end


        proto_out[
            #proto_out + 1
        ] =
            table.concat(
                row,
                "\t"
            )

    end


    proto_out[
        #proto_out + 1
    ] =
        "endproto"

end


writefile(
    OUT_PROTOS,
    table.concat(
        proto_out,
        "\n"
    )
)


local object_out =
    {
        "id\tscore\tcodefield\tcodelen"
    }


for id, proto in ipairs(
    protos
) do


    local code =
        safe_rawget(
            proto.object,
            proto.field
        )


    object_out[
        #object_out + 1
    ] =
        table.concat(
            {
                id,
                proto.score,
                proto.field,
                safe_len(code)
            },
            "\t"
        )

end


writefile(
    OUT_OBJECTS,
    table.concat(
        object_out,
        "\n"
    )
)


local constant_out =
    {
        "type\tvalue"
    }


for _, value in ipairs(
    constants
) do


    constant_out[
        #constant_out + 1
    ] =
        value

end


writefile(
    OUT_CONSTANTS,
    table.concat(
        constant_out,
        "\n"
    )
)


local relation_out =
    {
        "parent\tchild\tinstruction\tfield"
    }


for _, relation in ipairs(
    relations
) do


    relation_out[
        #relation_out + 1
    ] =
        relation

end


writefile(
    OUT_RELATIONS,
    table.concat(
        relation_out,
        "\n"
    )
)


return #protos,
    #constants,
    #relations

end

local function write_strings()

table.sort(
    strings,
    function(a, b)

        return a.value
            < b.value

    end
)


local output =
    {}


for i, entry in ipairs(
    strings
) do


    output[
        #output + 1
    ] =
        "[" .. tostring(i) .. "] [string] "
        .. entry.value

end


writefile(
    OUT_STRINGS,
    table.concat(
        output,
        "\n"
    )
)

end

local function write_values()

table.sort(
    values,
    function(a, b)

        return a.value
            < b.value

    end
)


local output =
    {
        "[1] [type] [value]"
    }


for i, entry in ipairs(
    values
) do


    if #output
        >= MAX_VALUE_OUTPUT then

        break

    end


    output[
        #output + 1
    ] =
        "[" .. tostring(i + 1) .. "] ["
        .. entry.value
        .. "] "
        .. entry.source

end


writefile(
    OUT_VALUES,
    table.concat(
        output,
        "\n"
    )
)

end

local function write_metadata(
proto_count,
constant_count,
relation_count,
script_objects
)

local metadata =
    {
        "string_count=" .. tostring(#strings),
        "value_count=" .. tostring(#values),
        "proto_count=" .. tostring(proto_count),
        "constant_count=" .. tostring(constant_count),
        "relation_count=" .. tostring(relation_count),
        "function_count=" .. tostring(function_count),
        "script_gc_objects=" .. tostring(#script_objects)
    }


writefile(
    OUT_META,
    table.concat(
        metadata,
        "\n"
    )
)

end

spawn(function()

warn(
    "[dump] phase 1: waiting for game GC to settle"
)


wait_for_game_settle()


warn(
    "[dump] phase 2: taking game baseline"
)


local game_baseline =
    snapshot_gc()


warn(
    "[dump] game baseline captured"
)


warn(
    "[dump] phase 3: loading target script"
)


local fn
local err


fn, err =
    loadstring(
        SRC,
        "=luraph"
    )


if not fn then


    warn(
        "[dump] loadstring failed: "
        .. tostring(err)
    )


    write_strings()
    write_values()


    write_metadata(
        0,
        0,
        0,
        {}
    )


    return

end


warn(
    "[dump] phase 4: executing target script"
)


local ok
local result


ok, result =
    pcall(fn)


warn(
    "[dump] script executed: "
    .. tostring(ok)
)


if not ok then


    warn(
        "[dump] error: "
        .. tostring(result)
    )

end


warn(
    "[dump] phase 5: collecting script-only GC delta"
)


local best_objects =
    {}


local best_count =
    0


local stable =
    0


local previous_count =
    -1


for attempt = 1,
    SCRIPT_POLL_TRIES do


    wait_(
        SCRIPT_POLL_GAP
    )


    local script_objects =
        get_new_objects(
            game_baseline
        )


    local count =
        #script_objects


    warn(
        string.format(
            "[dump] script delta %d/%d | new=%d | stable=%d/%d",
            attempt,
            SCRIPT_POLL_TRIES,
            count,
            stable,
            SCRIPT_STABLE_REQUIRED
        )
    )


    if count
        > best_count then


        best_count =
            count


        best_objects =
            script_objects

    end


    if count
        == previous_count then


        stable =
            stable + 1


    else


        stable =
            0


    end


    previous_count =
        count


    if stable
        >= SCRIPT_STABLE_REQUIRED then


        break

    end

end


warn(
    "[dump] script-only objects: "
    .. tostring(
        #best_objects
    )
)


add_string(
    SRC,
    "embedded_source"
)


scan_function(
    fn,
    "executed_function"
)


for _, object in ipairs(
    best_objects
) do


    local object_type =
        type(object)


    if object_type
        == "string" then


        add_value(
            object,
            "script_gc:string"
        )


    elseif object_type
        == "table" then


        scan_table(
            object,
            0,
            "script_gc:table"
        )


    elseif object_type
        == "function" then


        scan_function(
            object,
            "script_gc:function"
        )


    end

end


warn(
    "[dump] phase 6: dumping script-only prototypes"
)


local proto_count
local constant_count
local relation_count


proto_count,
constant_count,
relation_count =
    dump_protos(
        best_objects
    )


write_strings()
write_values()


write_metadata(
    proto_count,
    constant_count,
    relation_count,
    best_objects
)


warn(
    string.format(
        "[dump] done | strings=%d values=%d protos=%d constants=%d relations=%d",
        #strings,
        #values,
        proto_count,
        constant_count,
        relation_count
    )
)

end)
"""


def fetch_source(source):
    parsed = urlparse(source)

    if parsed.scheme in ("http", "https"):
        request = urllib.request.Request(
            source,
            headers={
                "User-Agent": "Mozilla/5.0"
            }
        )

        with urllib.request.urlopen(
            request,
            timeout=60
        ) as response:
            return response.read()

    with open(source, "rb") as f:
        return f.read()


def upload_pastefy(content, title):
    token = PASTEFY_TOKEN.strip()

    if not token:
        raise RuntimeError(
            "PASTEFY_TOKEN is empty. "
            "Add your new token before uploading."
        )

    payload = {
        "title": title,
        "content": content,
        "visibility": "PUBLIC",
        "encrypted": False
    }

    response = requests.post(
        PASTEFY_API,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json"
        },
        json=payload,
        timeout=60
    )

    print(
        "[*] Pastefy HTTP:",
        response.status_code
    )

    if not response.ok:
        raise RuntimeError(
            "Pastefy upload failed: HTTP "
            + str(response.status_code)
            + "\\n"
            + response.text
        )

    data = response.json()
    paste = data.get("paste", data)

    raw_url = (
        paste.get("raw_url")
        or paste.get("rawUrl")
        or paste.get("url")
    )

    if not raw_url:
        paste_id = paste.get("id")

        if paste_id:
            raw_url = (
                "https://pastefy.app/"
                + str(paste_id)
                + "/raw"
            )

    if not raw_url:
        raise RuntimeError(
            "Pastefy returned no raw URL:\\n"
            + json.dumps(
                data,
                indent=2
            )
        )

    return raw_url


def build(source_path, out_path, key):
    src = fetch_source(source_path)

    script_key = (
        key
        or "__PUT_YOUR_KEY_HERE__"
    )

    luau = LUAU_TEMPLATE.replace(
        "%SCRIPT_KEY%",
        script_key
    )

    full = (
        'local _LPH_SRC = "'
        + src.hex()
        + '"\\n'
        + luau
    )

    with open(
        out_path,
        "w",
        encoding="utf-8"
    ) as f:
        f.write(full)

    print(
        "[*] source:",
        source_path
    )

    print(
        "[*] source bytes:",
        len(src)
    )

    print(
        "[*] wrote:",
        out_path
    )

    print(
        "[*] output KB:",
        len(full) // 1024
    )

    return full


def main():
    if len(sys.argv) < 2:
        print(
            "usage: python pdump.py "
            "<local_file_or_url> "
            "[output.luau] "
            "[script_key]"
        )
        sys.exit(1)

    source = sys.argv[1]

    output = (
        sys.argv[2]
        if len(sys.argv) > 2
        else "dump.luau"
    )

    key = (
        sys.argv[3]
        if len(sys.argv) > 3
        else None
    )

    full = build(
        source,
        output,
        key
    )

    if not PASTEFY_TOKEN.strip():
        print()
        print("[!] Local output created.")
        print(
            "[!] Pastefy upload skipped "
            "because PASTEFY_TOKEN is empty."
        )
        return

    title = (
        output
        .rsplit("/", 1)[-1]
        .rsplit(".", 1)[0]
    )

    raw_url = upload_pastefy(
        full,
        title
    )

    launcher = (
        "loadstring("
        "game:HttpGet("
        + repr(raw_url)
        + ")"
        ")()"
    )

    print()
    print("[*] RAW URL:")
    print(raw_url)

    print()
    print("[*] LOADSTRING:")
    print(launcher)


if __name__ == "__main__":
    main()