import sys, argparse
sys.stdout.reconfigure(encoding="utf-8")

LUARMOR2_TEMPLATE = r"""-- 6Vms Luarmor 2 Logger @ dsc.gg/6vms

writefile('logged.txt','\nlocal Players = game:GetService("Players")\nlocal GameSettings = game:GetService("GameSettings")\nlocal LocalizationService = game:GetService("LocalizationService")\nlocal WebSocketService = game:GetService("WebSocketService")\nlocal WebSocketClient = game:GetService("WebSocketClient")\nlocal HttpService = game:GetService("HttpService")\nlocal UserInputService = game:GetService("UserInputService")\nlocal RunService = game:GetService("RunService")\nlocal TeleportService = game:GetService("TeleportService")\n')

local function isuilib()
 local a = debug.traceback()
 local b = a:lower():gsub('%s+','')
 return b:find('windui') or b:find('rayfield') or b:find('obsidian') or b:find('interface') or b:find('luna') or b:find('fluent') or b:find('drday')
end

local function formatlog(text)
 if type(text) ~= 'string' then
  error('Bad agrument #1 to formatlog "string" expected, got: '..type(text))
  return
 end
 return text:gsub('table: ',''):gsub('function: ',''):gsub('Ugc','game'):gsub('\n',''):gsub('%s%s+',';'):gsub('""',''):gsub('Data Ping', 'DataPing'):gsub('Workspace','workspace'):gsub('game.Players','Players'):gsub('Teleport Service','TeleportService'):gsub('Run Service','RunService'):gsub('HttpGetAsync','HttpGet'):gsub('"',"'")
end

local function tblformat(tbl, depth)
 local depth = depth or 0
 local res = ''
 local first = true
 if depth > 5 then return 'too big to display' end
 if type(tbl) ~= 'table' then
  res = '"'..tostring(tbl)..'"'
  if res == '"nil"' then
   res = ''
  end
  return res
 end
 for i, v in pairs(tbl) do
  if not first then res = res .. ', ' end
  first = false
  if type(i) == 'string' then
   res = res .. i .. ' = '
  end
  if type(i) == 'table' then
   res = res .. tblformat(v, depth + 1)
  else
   res = res .. tostring(v)
  end
 end
 return res .. ''
end

local Track = {}
local kirked = {}
local function kirk(fem, boy)
 return fem .. ';' .. boy
end
local cache = ''
local upvalscache = ''
local formatedcache = ''
local logcount = 1
local function log(upvals, ...)
 upvals = upvals or 'nil'
 upvals = formatlog(tostring(upvals))
 if #upvals > 100 then
  local holder = #upvals
  upvals = upvals:sub(1,50) .. '... (' .. holder .. ' character remaining)'
 end
 local args = ...
 local formated = formatlog(tostring(args))
 local logged = formated
 if logged == cache then
  return
 end
 if formated == formatedcache and upvals == upvalscache then
  return
 end
 local charliekirk = kirk(logged, upvals)
 if kirked[charliekirk] then
  return
 end
 if upvals:find('Signal') then
  logged = formated .. ':Connect(function(...)end)'
 end
 if logged:find('game:HttpGet') then
  logged = 'loadstring('..formated..')()'
 end
 if logcount > 36000 then
  game:shutdown()
  return
 end
 if logged:find('IsA') then
  return
 end
 logcount += 1
 cache = logged
 upvalscache = upvals
 formatedcache = formated
 kirked[charliekirk] = true
 appendfile('logged.txt',logged..'\n')
end

isfunctionhooked = nil
restorefunction = nil

function GlobalScan()
 for i, v in pairs(_G) do
  log('_G Scan', '_G.'..i..' = '..tblformat(v))
 end
end

function GenvScan()
 for i, v in pairs(getgenv()) do
  log('getgenv Scan', 'getgenv().'..i..' = '..tblformat(v))
 end
end

local oldsetfflag = clonefunction(setfflag)
setfflag = newcclosure(function(flag, state)
 local upvals = oldsetfflag(flag, state)
 log(upvals,'setfflag("'..flag..'", '..'"'..state..'")')
 return upvals
end)

if http and http.request then
 setreadonly(http, false)
 http.request = nil
 setreadonly(http, false)
end
local oldrequest = request
request = newcclosure(function(data)
 local upvals = oldrequest(data)
 local meow = data.Body
 if type(data.Body) == 'string' then
  if data.Body:sub(1,1) == '{' and data.Body:sub(-1) == '}' then
   meow = data.Body
  else
   meow = '"'..data.Body..'"'
  end
 elseif type(data.Body) == 'table' then
  meow = 'game:GetService("HttpService"):JSONEncode('..tblformat(data.Body)..')'
 else
  meow = tostring(data.Body)
 end
 local meowmeow = '{'
 local first = true
 if data.Headers then
  for i, v in pairs(data.Headers) do
   if not first then meowmeow = meowmeow .. ', ' end
   first = false
   meowmeow = meowmeow .. '["'..i..'"] = "'..v..'"'
  end
 end
 meowmeow = meowmeow .. '}'
 log(upvals, 'request({\n Url = "'..data.Url..'",\n Method = "'..data.Method..'",\n Body = '..meow..',\n Headers = '..meowmeow..'\n})')
 return upvals
end)

local oldl = clonefunction(loadstring)
hookfunction(loadstring, function(str)
 if true then
  writefile(math.random(1,999)..'.txt', str)
  warn'xd'
 end
 return oldl(str)
end)

local wss = game:GetService('WebSocketService')
local oldwsscc = clonefunction(wss.CreateClient)
hookfunction(game.WebSocketService.CreateClient, function(_, url)
 warn('WSS')
 if not url:lower():find'luarmor' then
  log('idk i found luarmor use this xd', 'WebsocketService:CreateClient("WebSocketService","'..url..'")')
 end
 return oldwsscc(_, url)
end)

Instance = Instance or {}
local oldinstancenew = clonefunction(Instance.new)
setreadonly(Instance, false)
Instance.new = newcclosure(function(name, parent)
 if checkcaller() and not isuilib() then
  local upvals = oldinstancenew(name, parent)
  local a = debug.getinfo(2,'Sl')
  if a and a.source:find('@') then
   log(upvals, 'local a = Instance.new("'..name..'")')
  else
   local b = tostring(name)
   Track[upvals] = b
   log(upvals, 'local '..b..' = Instance.new("'..name..'")')
  end
  return upvals
 end
 return oldinstancenew(name, parent)
end)

local mt = getrawmetatable(game)
local oldindex = clonefunction(mt.__index)
local oldnamecall = clonefunction(mt.__namecall)
local oldnewindex = clonefunction(mt.__newindex)
hookmetamethod(game,'__index',newcclosure(function(self, v, ...)
 if checkcaller() and not isuilib() then
  local upvals = oldindex(self, v, ...)
  local formated = tblformat(...)
  if v == 'Character' then
   log('LocalPlayer.Character', self:GetFullName()..'.'..v)
   return upvals
  end
  if v == 'GetService' then return upvals end
  if v == 'HttpGet' then return upvals end
  if v == 'JSONDecode' then return upvals end
  if v == 'CoreGui' then return upvals end
  if v == 'JSONEncode' then return upvals end
  if v == 'JobId' then log('game.JobId', self:GetFullName()..'.'..v) return upvals end
  if v == 'PlaceId' then log('game.PlaceId', self:GetFullName()..'.'..v) return upvals end
  if v == 'WaitForChild' then return upvals end
  if v == 'FindFirstChild' then return upvals end
  if v == 'DescendantRemoving' then return upvals end
  if tostring(upvals):find('function:') then
   log(upvals, self:GetFullName()..':'..v..'('..formated..')')
   return upvals
  end
  log(upvals, self:GetFullName()..'.'..v)
  return upvals
 end
 return oldindex(self, v, ...)
end))
hookmetamethod(game, '__namecall', newcclosure(function(self, ...)
 if checkcaller() and not isuilib() and getnamecallmethod() ~= 'GetFullName' then
  local instance = tostring(self)
  if type(instance) == 'Instance' then
   instance = oldnamecall(instance, 'GetFullName')
  end
  local upvals = oldnamecall(self, ...)
  local args = {...}
  local formated = tblformat(args)
  if getnamecallmethod() == 'GetService' then
   log(upvals, 'game:GetService("'..args[1]..'")')
   return upvals
  end
  if getnamecallmethod() == 'WaitForChild' then
   log(upvals, instance..':WaitForChild("'..args[1]..'")')
   return upvals
  end
  if getnamecallmethod() == 'FindFirstChild' then
   log(upvals, instance..':FindFirstChild("'..args[1]..'")')
   return upvals
  end
  if getnamecallmethod() == 'HttpGet' then
   log(upvals, 'game:HttpGet("'..args[1]..'", true)')
   return upvals
  end
  log(upvals, instance..':'..getnamecallmethod()..'("'..formated..'")')
  return upvals
 end
 return oldnamecall(self, ...)
end))
hookmetamethod(game, '__newindex', newcclosure(function(self, i, v)
 if checkcaller() and not isuilib() then
  local upvals = oldnewindex(self, i, v)
  local a = Track[self]
  local b = tostring(i)
  local c = tostring(typeof(v)) or 'Unknown'
  local d = tostring(v)
  if a then
   if b then
    if c == 'Instance' then
     log(upvals, a..'.'..b..' = '..v:GetFullName())
    elseif c == 'number' then
     log(upvals, a..'.'..b..' = '..d)
    elseif c == 'string' then
     log(upvals, a..'.'..b..' = "'..d..'"')
    elseif c == 'boolean' then
     log(upvals, a..'.'..b..' = '..d)
    elseif c == 'Color3' then
     log(upvals, a..'.'..b..' = Color3.new('..d..')')
    elseif c == 'CFrame' then
     log(upvals, a..'.'..b..' = CFrame.new('..d..')')
    elseif c == 'Vector3' then
     log(upvals, a..'.'..b..' = Vector3.new('..d..')')
    elseif c == 'UDim2' then
     log(upvals, a..'.'..b..' = UDim2.new('..d:gsub('{',''):gsub('}','')..')')
    elseif c == 'Vector2' then
     log(upvals, a..'.'..b..' = Vector2.new('..d..')')
    elseif c == 'UDim' then
     log(upvals, a..'.'..b..' = UDim.new('..d..')')
    elseif c == 'EnumItem' then
     log(upvals, a..'.'..b..' = '..d)
    elseif c == 'ColorSequence' then
     log(upvals, a..'.'..b..' = ColorSequence.new('..d:gsub('%s+',',')..')')
    else
     log(upvals, a..'.'..b..' = '..'['..c..'] '..d)
    end
   end
  else
   if b then
    if c == 'Instance' then
     log(upvals, 'a.'..b..' = '..v:GetFullName())
    elseif c == 'number' then
     log(upvals, 'a.'..b..' = '..d)
    elseif c == 'string' then
     log(upvals, 'a.'..b..' = "'..d..'"')
    elseif c == 'boolean' then
     log(upvals, 'a.'..b..' = '..d)
    elseif c == 'Color3' then
     log(upvals, 'a.'..b..' = Color3.new('..d..')')
    elseif c == 'CFrame' then
     log(upvals, 'a.'..b..' = CFrame.new('..d..')')
    elseif c == 'Vector3' then
     log(upvals, 'a.'..b..' = Vector3.new('..d..')')
    elseif c == 'UDim2' then
     log(upvals, 'a.'..b..' = UDim2.new('..d:gsub('{',''):gsub('}','')..')')
    elseif c == 'Vector2' then
     log(upvals, 'a.'..b..' = Vector2.new('..d..')')
    elseif c == 'UDim' then
     log(upvals, 'a.'..b..' = UDim.new('..d..')')
    elseif c == 'EnumItem' then
     log(upvals, 'a.'..b..' = '..d)
    elseif c == 'ColorSequence' then
     log(upvals, 'a.'..b..' = ColorSequence.new('..d:gsub('%s+',',')..')')
    else
     log(upvals, a..'.'..b..' = '..'['..c..'] '..d)
    end
   end
  end
  return upvals
 end
 return oldnewindex(self, i, v)
end))

game.DescendantRemoving:Connect(function(a)
 Track[a] = nil
end)

local oldprint = print
print = newcclosure(function(...)
 if checkcaller() and not isuilib() then
  local args = {...}
  local formated = {}
  for i = 1, select('#', ...) do
   local v = args[i]
   if type(v) == 'table' then
    formated[i] = tblformat(v)
   else
    formated[i] = tostring(v)
   end
  end
  local upvals = oldprint(...)
  log(upvals, 'print("'.. table.concat(formated,'\t') ..'")')
  return upvals
 end
 return oldprint(...)
end)
print("API calls will be logged to logged.txt")

--put a luarmor script here
"""

PAYLOAD_MARKER = "--put a luarmor script here"

def build_bytes(payload: str | None = None, out_file: str = "logged.txt"):
    script = LUARMOR2_TEMPLATE.replace('"logged.txt"', '"' + out_file + '"')
    if payload and payload.strip():
        script = script.replace(PAYLOAD_MARKER, payload.rstrip() + "\n")
    return script

def build(out_path, **kwargs):
    full = build_bytes(**kwargs)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full)
    print("[6Vms] wrote {} ({} KB)".format(out_path, len(full) // 1024))

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="6Vms Luarmor 2 Logger")
    p.add_argument("output", nargs="?", default="luarmor2_logger.luau")
    p.add_argument("--out-file", default="logged.txt")
    a = p.parse_args()
    build(a.output, out_file=a.out_file)