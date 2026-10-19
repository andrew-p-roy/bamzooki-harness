-- M0 headless experiments: job-driven contest runner (plain text, run via the loader).
--   Bonsai.exe -init loader -videocfg aiharness.vcfg -script <this file> -job <job.dat>
-- The job file is a Bonsai text table (see bamzooki.codec.table). Fields:
--   contest, green, red, out, log, boot_log, max_sim_time, stop_on_finish (0/1), go_time,
--   window (0/1: create SimpleFrame), camera (0/1: render via SplitScreen), update_interval,
--   config = { key = value ... } applied after the standard configuration.

ROOT = System.GetLabeledPath("ROOT")
local s, e, jobPath = String.strfind(System.GetCommandLine(), "%-job%s+(%S+)")
JOB = System.ReadTable(jobPath)

BOOT_LOG = ""
function BootLog(msg)
	BOOT_LOG = BOOT_LOG .. tostring(System.RealTime()) .. " " .. msg .. "\n"
	String.WriteFile(JOB.boot_log, BOOT_LOG)
end
BootLog("boot start, job " .. jobPath)

-- Same configuration sequence as Scripts/Boot/ContestRunner, but windowed.
Config.Set("app_name", "Simulator")
Execute("BuildConfiguration")
Execute("MemoryConfiguration")
Execute("ProductConfiguration")
Config.Set("exclusive", 0)
Execute("MakeWindowCaption", "aiharness")
Config.Set("physically_connected_integrity", false)
Config.Set("auto_set_inertial_tensor", 1)
Execute("PhysicsConstants")
if JOB.seed ~= nil then
	Number.randomseed(JOB.seed) -- PhysicsConstants seeds from the clock
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
if JOB.config ~= nil then
	for k, v in JOB.config do
		Config.Set(k, v)
		BootLog("config " .. k .. " = " .. tostring(v))
	end
end

local driverSource = String.ReadFile(SCRIPT_DIR .. "/AIHarnessDriver.sax.lua")
if JOB.camera == 0 then
	-- The Visual component (only needed for lights) appears to need a render context.
	driverSource = String.gsub(driverSource, " embeds Visual", "")
end
local created = Agent.CreateClassFromString(driverSource)
BootLog("driver class: " .. tostring(created) .. ", source " .. type(driverSource) .. " len " .. String.strlen(driverSource) .. ": " .. String.strsub(driverSource, 1, 45))

local packs = System.GetDirectoryContents("ContestPacks/", "*.dat", 1)
local contests = {}
for i, file in packs do
	local pack = System.ReadTable("ContestPacks/" .. file)
	for name, def in pack do
		contests[name] = def
	end
end

if JOB.window == 1 then
	mainWindow = Agent.Create("SimpleFrame")
	BootLog("window created")
end
driver = Agent.Create("AIHarnessDriver")
BootLog("driver agent: " .. tostring(driver))
local contestants = { { fullname = JOB.green }, { fullname = JOB.red } }
Agent.PostMessage("HarnessStart", driver, 0.1, JOB, contests[JOB.contest], contestants)
BootLog("main loop")
System.MainLoop()
BootLog("main loop exited")
Agent.Destroy(driver)
if mainWindow ~= nil then
	Agent.Destroy(mainWindow)
end
