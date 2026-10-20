# AIHarnessDriver specializes Agent embeds Visual

-- bamzooki harness contest driver (our own code; uses the game's Contest/BehaviourManager agents).
-- Lua 4.0 dialect: no `local function`, globals instead of upvalues, conditions must not be nil,
-- do not define Finalize (it breaks runtime classes), world agents only visible inside OpenAccess.
--
-- Job fields: contest_file, green, red, out, log, go_time, max_sim_time, max_real_after_go
-- (the time limit also in real seconds after Go: the sim runs slower than real time while capturing
-- video, and the game must still end on time), stop_on_finish,
-- finish_hold, update_interval, track_interval, overrides = { BehaviourName = { param = value } },
-- record (0/1), record_bmv, record_bvz,
-- camera: 1 = the game's SplitScreen (5 views) + lights, 2 = 2x2-pixel camera (no picture),
--         3 = one full-window HarnessCamera + lights, with view = { zook, offset, angle } and optional
--             frame capture (capture = 1, capture_dir, capture_fps, capture_start; size = window_w/h).

function HLog(msg)
	myLogText = myLogText .. String.format("%8.3f ", System.RealTime() - myStartReal) .. msg .. "\n"
	String.WriteFile(myJob.log, myLogText)
end

function MessageHarnessStart(job)
	myJob = job
	myStartReal = System.RealTime()
	myLogText = ""
	myReports = {}
	myReportCount = 0
	myTrack = {}
	myTrackCount = 0
	myNextTrack = 0
	myUpdates = 0
	myDone = false
	myGoing = false
	myGoTime = -1
	myGoReal = -1
	myFinishTime = -1
	myRecording = false
	HLog("start " .. job.contest_file)
	Setup()
	Agent.SetTimer("Update", Agent.Me(), "update", job.update_interval)
end

function ApplyOverrides(data)
	if data.behaviours == nil then
		return
	end
	-- Per-behaviour patches by id (fixes for data bugs in specific contests).
	if myJob.patches ~= nil then
		for id, p in myJob.patches do
			local b = data.behaviours[id]
			if b ~= nil then
				if b.params == nil then
					b.params = {}
				end
				for k, v in p do
					b.params[k] = v
					HLog("patch #" .. id .. " " .. b.name .. "." .. k .. " = " .. tostring(v))
				end
			end
		end
	end
	if myJob.overrides == nil then
		return
	end
	for id, b in data.behaviours do
		local o = myJob.overrides[b.name]
		if o ~= nil then
			if b.params == nil then
				b.params = {}
			end
			for k, v in o do
				b.params[k] = v
				HLog("override #" .. id .. " " .. b.name .. "." .. k .. " = " .. tostring(v))
			end
		end
	end
end

-- Put our Zooks into the arena's placeholders ourselves: green -> flags "1", red -> flags "5"
-- (the engine's own camera colours). Contestant ids follow ascending agent index, as in Contest.
function AssignContestants(data)
	myContestants = {}
	local n = 0
	for index = 1, getn(data.agents) do
		local a = data.agents[index]
		if a ~= nil and a.agent == "ZookPlaceHolder" then
			n = n + 1
			local flags = tostring(a.params.flags)
			local colour = "unknown"
			local file = nil
			if flags == "1" then
				colour = "green"
				file = myJob.green
			elseif flags == "5" then
				colour = "red"
				file = myJob.red
			end
			if file == nil then
				file = myJob.green
			end
			if file == nil then
				file = myJob.red
			end
			a.params.contestant = { fullname = file }
			local x, y, z = Vector.GetXYZ(a.params.position)
			myContestants[n] = { colour = colour, agent_index = index, file = file,
				group = tostring(a.params.group), description = tostring(a.params.description),
				x = x, y = y, z = z }
			HLog("contestant " .. n .. " = " .. colour .. " (agent " .. index .. "): " .. file)
		end
	end
end

function Setup()
	local data = System.ReadTable("Contests/Contest Pack/" .. myJob.contest_file .. ".contest")
	ApplyOverrides(data)
	AssignContestants(data)
	myWorld = World.Create()
	if myJob.camera == 1 then
		local camera = myJob.camera_def
		if camera == nil then
			camera = {}
		end
		camera.no_margin = 1
		myCamera = Agent.Create("SplitScreen", myWorld, camera)
	end
	World.OpenAccess(myWorld)
	if myJob.camera == 3 then
		myHarnessCamera = Agent.Create("HarnessCamera", 0, 0, myJob.window_w, myJob.window_h)
		HLog("camera fitted to width " .. tostring(Agent.SendMessage("FitWindow", myHarnessCamera)))
		local v = myJob.view
		Agent.SendMessage("SetMinAngle", myHarnessCamera, v.min_angle)
		Agent.SendMessage("SetView", myHarnessCamera, { zook = v.zook, target = v.target,
			offset = v.offset, verticalViewAngle = v.angle, targetHeight = 0 })
		if myJob.capture == 1 then
			local ok = Agent.SendMessage("HarnessCapture", myHarnessCamera, myJob.capture_dir, myJob.capture_fps,
				myJob.capture_start, myJob.capture_ext)
			HLog("camera " .. tostring(myHarnessCamera) .. " capture -> " .. tostring(ok))
		end
	end
	if myJob.camera == 2 then
		myTinyCamera = Agent.Create("SplitCamera", 0, 0, 2, 2)
		Agent.SendMessage("SetView", myTinyCamera, { zook = -1, target = Vector.New(0, 0, 0),
			offset = Vector.New(-200, 120, 200), verticalViewAngle = 18, targetHeight = 0 })
	end
	-- An empty selection list: every placeholder already has its contestant.
	myContest = Agent.Create("Contest", nil, data, {}, Agent.Me())
	if myJob.camera == 1 or myJob.camera == 3 then
		CreateLights()
	end
	World.CloseAccess()
	HLog("contest created")
end

-- Same lighting as the game's ContestRunnerContest (call inside World.OpenAccess).
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

function SimTime()
	if myWorld == nil then
		return -1
	end
	World.OpenAccess(myWorld)
	local t = World.SimTime()
	World.CloseAccess()
	return t
end

function Positions()
	local out = {}
	if myWorld == nil then
		return out
	end
	World.OpenAccess(myWorld)
	local evos = Agent.GetAgentsOfClass("Evo")
	for i, a in evos do
		local x, y, z = Vector.GetXYZ(Agent.SendMessage("GetPosition", a))
		out[i] = { x = x, y = y, z = z }
	end
	World.CloseAccess()
	return out
end

function TimerUpdate()
	if myDone ~= false then
		return
	end
	myUpdates = myUpdates + 1
	local t = SimTime()
	if myJob.record == 1 and myRecording == false and t > 1 then
		StartRecording()
	end
	if myGoing == false and t >= myJob.go_time then
		World.OpenAccess(myWorld)
		Agent.SendMessage("Go", myContest)
		World.CloseAccess()
		myGoing = 1
		myGoTime = t
		myGoReal = System.RealTime()
		HLog("go at sim " .. t)
	end
	if t >= myNextTrack then
		myTrackCount = myTrackCount + 1
		myTrack[myTrackCount] = { t = t, real = System.RealTime() - myStartReal, p = Positions() }
		myNextTrack = t + myJob.track_interval
	end
	if t > myJob.max_sim_time then
		HarnessDone("timeout")
	elseif myGoing ~= false and myJob.max_real_after_go > 0 then
		if System.RealTime() - myGoReal > myJob.max_real_after_go then
			HLog("time limit reached in real time at sim " .. t)
			HarnessDone("timeout")
		end
	end
end

function StartRecording()
	World.OpenAccess(myWorld)
	local rate = World.OpenVisualOutput(myJob.record_bmv)
	World.CloseAccess()
	myRecording = 1
	Agent.SetTimer("Record", Agent.Me(), "record", rate)
	HLog("recording to " .. myJob.record_bmv .. " every " .. tostring(rate) .. " s")
end

function TimerRecord()
	if myWorld ~= nil then
		World.OpenAccess(myWorld)
		World.WriteVisual()
		World.CloseAccess()
	end
end

function StopRecording()
	if myRecording ~= false then
		Agent.StopTimer(Agent.Me(), "record")
		World.CloseVisualOutput()
		myRecording = false
		System.Zip(myJob.record_bmv, myJob.record_bvz, 6)
		HLog("recording zipped to " .. myJob.record_bvz)
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
	local r = { name = tostring(message.name), sim_time = SimTime() }
	if message.scoreboard ~= nil then
		r.score = message.scoreboard.score
		r.contestant = message.scoreboard.contestant
		r.player = message.scoreboard.player
	end
	myReports[myReportCount] = r
	HLog("report " .. r.name .. " at sim " .. r.sim_time)
	if message.name == "finish" and myFinishTime < 0 then
		myFinishTime = r.sim_time
		if myJob.stop_on_finish == 1 then
			-- Keep simulating briefly so the finish is visible on screen and in the recording.
			Agent.PostMessage("HarnessFinished", Agent.Me(), myJob.finish_hold)
		end
	end
end

function MessageHarnessFinished()
	HarnessDone("finished")
end

function HarnessDone(status)
	if myDone ~= false then
		return
	end
	myDone = 1
	Agent.StopTimer(Agent.Me(), "update")
	StopRecording()
	local frames = 0
	if myHarnessCamera ~= nil and myJob.capture == 1 then
		World.OpenAccess(myWorld)
		frames = Agent.SendMessage("HarnessStopCapture", myHarnessCamera)
		World.CloseAccess()
	end
	HLog("done: " .. status)
	local result = {
		status = status,
		contest_file = myJob.contest_file,
		go_time = myGoTime,
		finish_time = myFinishTime,
		sim_time = SimTime(),
		real_time = System.RealTime() - myStartReal,
		updates = myUpdates,
		frames = frames,
		contestants = myContestants,
		final = Positions(),
		reports = myReports,
		track = myTrack,
	}
	System.WriteTable(myJob.out, result)
	HLog("result written")
	CleanUp()
	System.ExitMainLoop()
end
