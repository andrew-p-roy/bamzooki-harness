-- bamzooki harness boot script (plain text, run by the encrypted loader).
--   Bonsai.exe -init aiharness -videocfg aiharness.vcfg -script <this file> -job <job.dat>
-- The job file is a Bonsai text table written by bamzooki.runner (see Job in runner/__init__.py).
-- Lua 4.0 dialect: no `local function`, globals instead of upvalues, conditions must not be nil.

ROOT = System.GetLabeledPath("ROOT")
local s, e, jobPath = String.strfind(System.GetCommandLine(), "%-job%s+(%S+)")
JOB = System.ReadTable(jobPath)

BOOT_LOG = ""
function BootLog(msg)
	BOOT_LOG = BOOT_LOG .. String.format("%8.3f ", System.RealTime()) .. msg .. "\n"
	String.WriteFile(JOB.boot_log, BOOT_LOG)
end
BootLog("boot: job " .. jobPath)

if JOB.profile == "player" then
	-- Same configuration sequence as the game's Motion Player boot script, but windowed.
	Config.Set("app_name", "Motion Player")
	Execute("BuildConfiguration")
	Execute("PlaybackMemoryConfiguration")
	Execute("ServerConfiguration")
	Execute("ProductConfiguration")
	Config.Set("exclusive", 0)
	Execute("MakeWindowCaption", JOB.window_title)
	Config.Set("worldscale", 1)
	Execute("StudioSettings")
	Execute("PhysicsConstants")
	Config.Set("target_red", 1)
	Config.Set("target_green", 0)
	Config.Set("target_blue", 0)
	Config.Set("v_sync", false)
	Config.Set("supress_textures", false)
	Config.Set("shadow_catcher_height", -6.25)
	Config.Set("max_tick_size", 0.0625)
	Config.Set("texture_path", "NewTeam/Textures;Agents/World/Target")
	Config.Set("auto_GUI_resize", false)
else
	-- Same configuration sequence as the game's Simulator boot script, but windowed.
	Config.Set("app_name", "Simulator")
	Execute("BuildConfiguration")
	Execute("MemoryConfiguration")
	Execute("ProductConfiguration")
	Config.Set("exclusive", 0)
	Execute("MakeWindowCaption", JOB.window_title)
	Config.Set("physically_connected_integrity", false)
	Config.Set("auto_set_inertial_tensor", 1)
	Execute("PhysicsConstants")
	-- PhysicsConstants seeds the RNG from the clock; a fixed seed makes contests repeatable.
	if JOB.seed ~= nil then
		Number.randomseed(JOB.seed)
	end
	Config.Set("show_invisible", 0)
	Config.Set("fogging", 1)
	Config.Set("fog_start", 40)
	Config.Set("fog_end", 80)
	Config.Set("target_red", 1)
	Config.Set("target_green", 0)
	Config.Set("target_blue", 0)
	Execute("GenomeConfiguration")
	Config.Set("auto_GUI_resize", false)
	Config.Set("texture_path", "./NewTeam/Textures")
end
if JOB.config ~= nil then
	for k, v in JOB.config do
		Config.Set(k, v)
		BootLog("config " .. k .. " = " .. tostring(v))
	end
end

-- Agent classes shipped next to this file.
-- Governor-agent classes are defined at runtime. (World-agent classes such as HarnessCamera
-- cannot be: runtime classes are invisible inside worlds, so the runner installs those into
-- the sandbox's Agents/World as encrypted .sax files instead.)
for i, name in JOB.classes do
	local src = String.ReadFile(SCRIPT_DIR .. "/" .. name .. ".munki.lua")
	if JOB.camera ~= 1 and JOB.camera ~= 3 then
		-- The Visual component (lights) needs a camera with a real render context.
		src = String.gsub(src, " embeds Visual", "")
	end
	local ok = Agent.CreateClassFromString(src)
	BootLog("class " .. name .. ": " .. tostring(ok))
end

mainWindow = Agent.Create("SimpleFrame")
local driverClass = JOB.driver
if driverClass == nil then
	driverClass = "AIHarnessDriver"
end
driver = Agent.Create(driverClass)
Agent.PostMessage("HarnessStart", driver, 0.1, JOB)
BootLog("main loop")
System.MainLoop()
BootLog("main loop exited")
Agent.Destroy(driver)
Agent.Destroy(mainWindow)
