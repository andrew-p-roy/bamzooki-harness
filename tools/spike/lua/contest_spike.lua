-- M0 spike (c): run one contest between two Zooks with no selection UI and write the scoreboard.
--   Bonsai.exe -init loader -videocfg aiharness.vcfg -script <this file>
-- Lua 4.0 dialect: no `local function`, no upvalues without %, conditions must not be nil.

ROOT = System.GetLabeledPath("ROOT")
JOB = {
	contest = "Zook Sumo",
	green = ROOT .. "/Teams/examples/Creatures/Ant/2.1.zook",
	red = ROOT .. "/Teams/examples/Creatures/Spider/2.1.zook",
	out = ROOT .. "/contest_out.dat",
	max_sim_time = 150,
	stop_on_finish = 1, -- 0 = keep simulating until max_sim_time
	log = ROOT .. "/contest_agent_log.txt",
}
BOOT_LOG = ""
function BootLog(msg)
	BOOT_LOG = BOOT_LOG .. tostring(System.RealTime()) .. " " .. msg .. "\n"
	String.WriteFile(ROOT .. "/contest_boot_log.txt", BOOT_LOG)
end
BootLog("boot start")

-- Same configuration sequence as Scripts/Boot/ContestRunner, but windowed.
Config.Set("app_name", "Simulator")
Execute("BuildConfiguration")
Execute("MemoryConfiguration")
Execute("ProductConfiguration")
Config.Set("exclusive", 0)
Execute("MakeWindowCaption", "aiharness spike")
Config.Set("physically_connected_integrity", false)
Config.Set("auto_set_inertial_tensor", 1)
Execute("PhysicsConstants")
Config.Set("show_invisible", 0)
Config.Set("fogging", 1)
Config.Set("fog_start", 40)
Config.Set("fog_end", 80)
Config.Set("target_red", 1)
Config.Set("target_green", 0)
Config.Set("target_blue", 0)
Config.Set("trace_behaviours", 1)
local dir = Config.Get("product_directory", ".")
Execute("LoadConfigTable", dir .. "ContestRunner.cfg")
Execute("GenomeConfiguration")
Config.Set("auto_GUI_resize", false)
BootLog("config done")
Config.Set("texture_path", "./NewTeam/Textures")

-- Contest driver: reuse ContestRunnerContest, hook the behaviour reports and the end of rounds.
Agent.CreateClassFromString(String.ReadFile(SCRIPT_DIR .. "/AIHarnessContest.sax.lua"))
BootLog("class created")

local packs = System.GetDirectoryContents("ContestPacks/", "*.dat", 1)
local contests = {}
for i, file in packs do
	local pack = System.ReadTable("ContestPacks/" .. file)
	for name, def in pack do
		contests[name] = def
	end
end

mainWindow = Agent.Create("SimpleFrame")
BootLog("frame created")
harness = Agent.Create("AIHarnessContest", nil)
BootLog("harness created")
local contestants = { { fullname = JOB.green }, { fullname = JOB.red } }
BootLog("contest def: " .. tostring(contests[JOB.contest]))
Agent.PostMessage("HarnessStart", harness, 0.1, JOB, JOB.contest, contests[JOB.contest], contestants)

System.MainLoop()
BootLog("main loop exited")

Agent.Destroy(harness)
Agent.Destroy(mainWindow)
