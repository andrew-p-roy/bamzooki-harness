# HarnessTrial specializes Agent embeds Visual

-- bamzooki harness: the Zook Kit's Test-mode trials, driven without the Builder UI.
-- Same plumbing as the Builder: a ClickableTarget registered as Config "BuilderTarget", a trial
-- environment world agent (SprintEnv, BlockPush, Hurdles, HighJump, Lap, or a free environment
-- such as BoxEnv / SlopeEnv), the Zook expressed by BuilderParts at the environment's start
-- position, then Go + ReportEvent. The environment calls back StopWatch and AddDetail.
-- Job: zook, name, env, trial (0/1), go_time, max_time, texture_dir, view, window_w/h,
-- capture, capture_dir, capture_fps, update_interval, track_interval, out, log.
-- Lua 4.0 dialect (see AIHarnessDriver).

function HLog(msg)
	myLogText = myLogText .. String.format("%8.3f ", System.RealTime() - myStartReal) .. msg .. "\n"
	String.WriteFile(myJob.log, myLogText)
end

function MessageHarnessStart(job)
	myJob = job
	myStartReal = System.RealTime()
	myLogText = ""
	myDone = false
	myStarted = false
	myResult = nil
	myTrack = {}
	myTrackCount = 0
	myNextTrack = 0
	myWorld = World.Create()
	World.OpenAccess(myWorld)
	if job.view_fit ~= nil then
		myBars = Agent.Create("HarnessCamera", 0, 0, job.window_w, job.window_h)
		Agent.SendMessage("Bars", myBars, 0, 0, 0)
	end
	myCamera = Agent.Create("HarnessCamera", 0, 0, job.window_w, job.window_h)
	if job.view_fit ~= nil then
		Agent.SendMessage("SetFit", myCamera, job.view_fit.aspect, job.view_fit.max_w)
		Agent.SendMessage("ToFront", myCamera)
	end
	Agent.SendMessage("FitWindow", myCamera)
	-- The target must exist before the environment: environments move "BuilderTarget".
	myTarget = Agent.Create("ClickableTarget", Vector.New(0, 0, -52), 0.5)
	Config.Set("BuilderTarget", myTarget)
	myEnv = Agent.Create(job.env, nil, 130, 130, Quatn.New())
	myBuilder = Agent.Create("HarnessBuilder", Vector.New(0, 0, 0), "harness")
	Agent.SendMessage("Load", myBuilder, job.zook, job.texture_dir, job.name, 0)
	local start = Vector.New(0, 0, 0)
	if job.trial == 1 then
		local s = Agent.SendMessage("StartPosition", myEnv)
		if s ~= nil then
			start = s
		end
	end
	Agent.SendMessage("SetGenomeVisibility", myBuilder, false)
	Agent.SendMessage("Express", myBuilder, false, start, 1)
	Agent.SendMessage("SetCreatureTarget", myBuilder, myTarget)
	myCreature = Agent.SendMessage("GetExpressedCreature", myBuilder)
	-- Visual group 1 lets the follow camera find the Zook (as in contests, green = 1).
	Agent.SendMessage("SetVisualFlags", myCreature, 1)
	-- Like the Builder, report the creature straight away (HighJump measures from creation) and
	-- again at Go, which also restarts the stopwatch.
	myStopWatchStart = World.SimTime()
	if job.trial == 1 then
		Agent.SendMessage("ReportEvent", myEnv, myCreature, "StopWatch")
	end
	local v = job.view
	Agent.SendMessage("SetMinAngle", myCamera, v.min_angle)
	Agent.SendMessage("SetView", myCamera, { zook = v.zook, target = v.target,
		offset = v.offset, verticalViewAngle = v.angle, targetHeight = 0 })
	if job.capture == 1 then
		Agent.SendMessage("HarnessCapture", myCamera, job.capture_dir, job.capture_fps, 0, "hex")
	end
	World.CloseAccess()
	if job.curtain_agent ~= nil then
		Agent.SendMessage("Destroy", job.curtain_agent)
	end
	HLog("expressed " .. job.name .. " in " .. job.env)
	Agent.SetTimer("Update", Agent.Me(), "update", job.update_interval)
end

function SimTime()
	World.OpenAccess(myWorld)
	local t = World.SimTime()
	World.CloseAccess()
	return t
end

function Position()
	World.OpenAccess(myWorld)
	local x, y, z = Vector.GetXYZ(Agent.SendMessage("GetPosition", myCreature))
	World.CloseAccess()
	return { x = x, y = y, z = z }
end

function TimerUpdate()
	if myDone ~= false then
		return
	end
	local t = SimTime()
	if myStarted == false and t >= myJob.go_time then
		World.OpenAccess(myWorld)
		Agent.SendMessage("StartCreature", myBuilder)
		if myJob.trial == 1 then
			Agent.SendMessage("Go", myEnv)
		end
		myStopWatchStart = World.SimTime()
		if myJob.trial == 1 then
			Agent.SendMessage("ReportEvent", myEnv, myCreature, "StopWatch")
		end
		World.CloseAccess()
		myStarted = 1
		HLog("go at sim " .. t)
	end
	if t >= myNextTrack then
		myTrackCount = myTrackCount + 1
		myTrack[myTrackCount] = { t = t, p = Position() }
		myNextTrack = t + myJob.track_interval
	end
	if myStarted ~= false and t - myStopWatchStart > myJob.max_time then
		Done("timeout")
	end
end

-- Called by the trial environment (as on the Builder).
function MessageStopWatch()
	return SimTime() - myStopWatchStart
end

function MessageAddDetail(category, name, data, comment, mods)
	myResult = { name = name, value = data, unit = comment, at = SimTime() - myStopWatchStart }
	HLog("result " .. tostring(name) .. " = " .. tostring(data) .. " " .. tostring(comment))
	Agent.PostMessage("TrialDone", Agent.Me(), 1.5)
end

function MessageSetSprintSpeed(result)
end

function MessageTrialDone()
	Done("finished")
end

function Done(status)
	if myDone ~= false then
		return
	end
	myDone = 1
	Agent.StopTimer(Agent.Me(), "update")
	local frames = 0
	World.OpenAccess(myWorld)
	if myJob.capture == 1 then
		frames = Agent.SendMessage("HarnessStopCapture", myCamera)
	end
	World.CloseAccess()
	local final = Position()
	System.WriteTable(myJob.out, { status = status, result = myResult, track = myTrack, final = final,
		frames = frames, sim_time = SimTime() - myStopWatchStart, real_time = System.RealTime() - myStartReal })
	HLog("done: " .. status)
	World.OpenAccess(myWorld)
	Agent.SendMessage("Destroy", myEnv)
	Agent.SendMessage("Destroy", myBuilder)
	World.CloseAccess()
	World.Destroy(myWorld)
	System.ExitMainLoop()
end
