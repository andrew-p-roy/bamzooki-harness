# AIHarnessDriver specializes Agent embeds Visual

-- Minimal contest driver: no GUI, optional camera. Lua 4.0 dialect (see Bamzooki_Harness.md §3).
-- Job fields used: contest, out, log, max_sim_time, stop_on_finish, go_time, update_interval,
-- camera (0 = none, 1 = SplitScreen views + lights, 2 = one 2x2-pixel SplitCamera).

function HLog(msg)
	myLogText = myLogText .. String.format("%8.3f ", System.RealTime() - myStartReal) .. msg .. "\n"
	String.WriteFile(myJob.log, myLogText)
end

function MessageHarnessStart(job, contestDef, contestants)
	myJob = job
	myStartReal = System.RealTime()
	myLogText = ""
	myReports = {}
	myReportCount = 0
	mySamples = {}
	mySampleCount = 0
	myUpdates = 0
	myDone = false
	myDef = contestDef
	myContestants = contestants
	myRound = 1
	myRoundsCompleted = 0
	HLog("start " .. tostring(job.contest))
	BeginRound()
	Agent.SetTimer("Update", Agent.Me(), "update", job.update_interval)
end

function CleanUp()
	if myWorld ~= nil then
		if myCamera ~= nil then
			Agent.SendMessage("Destroy", myCamera)
			myCamera = nil
		end
		Agent.SendMessage("Destroy", myContest)
		if myLights ~= nil then
			Segment.Destroy(Segment.ambient)
			Segment.Destroy(Segment.directional)
			myLights = nil
		end
		World.Destroy(myWorld)
		myWorld = nil
	end
end

function BeginRound()
	CleanUp()
	myWorld = World.Create()
	if myJob.camera == 1 then
		local camera = myDef.camera
		if camera == nil then
			camera = {}
		end
		camera.no_margin = 1
		myCamera = Agent.Create("SplitScreen", myWorld, camera)
	end
	World.OpenAccess(myWorld)
	if myJob.camera == 2 then
		-- Minimal render context: one 2x2-pixel camera, no SplitScreen GUI.
		myTinyCamera = Agent.Create("SplitCamera", 0, 0, 2, 2)
		Agent.SendMessage("SetView", myTinyCamera, { zook = -1, target = Vector.New(0, 0, 0),
			offset = Vector.New(-200, 120, 200), verticalViewAngle = 18, targetHeight = 0 })
	end
	local round = myDef.rounds[myRound]
	local contestData = System.ReadTable("Contests/Contest Pack/" .. round.contest .. ".contest")
	local selected = {}
	for i, v in round.contestants do
		selected[i] = myContestants[v]
	end
	myContest = Agent.Create("Contest", nil, contestData, selected, Agent.Me())
	if myJob.camera == 1 then
		CreateLights()
	end
	World.CloseAccess()
	myGoing = false
	HLog("round " .. myRound .. " created (" .. round.contest .. ")")
end

-- Same lighting as ContestRunnerContest.CreateLights (call inside World.OpenAccess).
function CreateLights()
	Visual.CreateShadowSource(Vector.New(2, 10, 2), .2)
	Visual.CreateLight(0, "ambient")
	Visual.SetLightColour(Segment.ambient, 0.3, 0.3, 0.3)
	Visual.CreateLight(1, "directional")
	Visual.SetLightColour(Segment.directional, 0.7, 0.7, 0.7)
	local trans = Matrix.New()
	Matrix.SetTranslation(trans, Vector.New(1, 1, 0.8))
	Matrix.LookAt(trans, Vector.New(0, 0, 0), Vector.New(0, 1, 0))
	Visual.SetLightTransform(Segment.directional, trans)
	myLights = 1
end

function SimTime()
	if myWorld == nil then
		return -1
	end
	World.OpenAccess(myWorld)
	local t = World.SimTime()
	World.CloseAccess()
	return t
end

function TimerUpdate()
	if myDone ~= false then
		return
	end
	myUpdates = myUpdates + 1
	local t = SimTime()
	if myGoing == false and t >= myJob.go_time then
		World.OpenAccess(myWorld)
		Agent.SendMessage("Go", myContest)
		World.CloseAccess()
		myGoing = 1
		HLog("go at sim " .. t .. " positions " .. PosString(Positions()))
	end
	if Number.mod(myUpdates, 10) == 0 then
		mySampleCount = mySampleCount + 1
		mySamples[mySampleCount] = { real = System.RealTime() - myStartReal, sim = t }
	end
	if t > myJob.max_sim_time then
		HarnessDone("timeout")
	end
end

function MessageInstantiateContest()
	Agent.SendMessage("InstantiateAll", myContest)
end

function MessageContestInstantiated()
	HLog("contest instantiated")
end

function MessageBehaviourReport(message)
	myReportCount = myReportCount + 1
	local r = { name = tostring(message.name), round = myRound, sim_time = SimTime() }
	if message.scoreboard ~= nil then
		r.score = message.scoreboard.score
		r.contestant = message.scoreboard.contestant
	end
	myReports[myReportCount] = r
	HLog("report " .. r.name .. " at sim " .. r.sim_time)
	if message.name == "finish" and myJob.stop_on_finish == 1 then
		myRoundsCompleted = myRound
		Agent.PostMessage("EndRound", Agent.Me(), 0.01)
	end
end

function MessageEndRound()
	myRound = myRound + 1
	if myDef.rounds[myRound] ~= nil then
		BeginRound()
	else
		HarnessDone("finished")
	end
end

function Positions()
	local out = {}
	if myWorld == nil then
		return out
	end
	-- World agents are only visible to a governor while it holds world access.
	World.OpenAccess(myWorld)
	local evos = Agent.GetAgentsOfClass("Evo")
	for i, a in evos do
		local x, y, z = Vector.GetXYZ(Agent.SendMessage("GetPosition", a))
		out[i] = { x = x, y = y, z = z }
	end
	World.CloseAccess()
	return out
end

function PosString(p)
	local s = ""
	for i, v in p do
		s = s .. String.format("(%.3f, %.3f, %.3f) ", v.x, v.y, v.z)
	end
	return s
end

function HarnessDone(status)
	if myDone ~= false then
		return
	end
	myDone = 1
	Agent.StopTimer(Agent.Me(), "update")
	HLog("done: " .. status)
	local result = {
		status = status,
		contest = myJob.contest,
		rounds_completed = myRoundsCompleted,
		sim_time = SimTime(),
		real_time = System.RealTime() - myStartReal,
		updates = myUpdates,
		positions = Positions(),
		reports = myReports,
		samples = mySamples,
	}
	System.WriteTable(myJob.out, result)
	HLog("result written")
	CleanUp()
	HLog("cleaned up")
	System.ExitMainLoop()
end
