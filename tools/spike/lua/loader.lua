-- Encrypted once, then runs a plain-text Lua file named on the command line:
--   Bonsai.exe -init loader -script "C:/path/to/file.lua"
-- Note: this Lua 4.0 build errors on conditions that evaluate to nil; compare explicitly.
local cmd = System.GetCommandLine()
local s, e, path = String.strfind(cmd, "%-script%s+\"([^\"]+)\"")
if path == nil then
	s, e, path = String.strfind(cmd, "%-script%s+(%S+)")
end
if path == nil then
	Trace("loader: no -script argument")
	System.ExitMainLoop()
else
	Trace("loader: running " .. path)
	SCRIPT_PATH = path
	s, e, SCRIPT_DIR = String.strfind(path, "^(.*)[/\\]")
	dostring(String.ReadFile(path))
end
