-- bamzooki harness loader (encrypted into <sandbox>/Scripts/Boot/aiharness.ssx at setup).
-- Runs a plain-text Lua file named on the command line:
--   Bonsai.exe -init aiharness -script "C:/path/to/file.lua" [other args]
-- Sets SCRIPT_PATH / SCRIPT_DIR for the script. Lua 4.0: compare with nil explicitly.
local cmd = System.GetCommandLine()
local s, e, path = String.strfind(cmd, "%-script%s+\"([^\"]+)\"")
if path == nil then
	s, e, path = String.strfind(cmd, "%-script%s+(%S+)")
end
if path == nil then
	Trace("aiharness loader: no -script argument")
	System.ExitMainLoop()
else
	Trace("aiharness loader: running " .. path)
	SCRIPT_PATH = path
	s, e, SCRIPT_DIR = String.strfind(path, "^(.*)[/\\]")
	dostring(String.ReadFile(path))
end
