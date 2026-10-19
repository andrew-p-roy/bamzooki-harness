-- M0 spike: minimal custom boot script (Lua 4.0). Launch with: Bonsai.exe -init hello
Trace("hello: boot script started")

local info = {}
info.status = "ok"
info.lua_version = tostring(_VERSION)
info.command_line = tostring(System.GetCommandLine())
info.root = tostring(System.GetLabeledPath("ROOT"))
info.home = tostring(System.GetLabeledPath("HOME"))
info.mydocuments = tostring(System.GetLabeledPath("MYDOCUMENTS"))
info.build = tostring(System.BuildNumber())
info.has_call = tostring(call ~= nil)
info.has_dofile = tostring(dofile ~= nil)
info.has_pairs = tostring(pairs ~= nil)
info.has_executestring = tostring(ExecuteString ~= nil)

-- The engine has a write-path whitelist; try the install root first, then HOME.
System.WriteTable(info.root .. "/hello_out.dat", info)
Trace("hello: wrote " .. info.root .. "/hello_out.dat")

System.ExitMainLoop()
Trace("hello: done")
