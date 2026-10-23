# HarnessShowcase specializes Agent embeds Visual

-- bamzooki harness showcase: a submitted Zook standing still (expressed but never started) on a
-- free-play floor, the camera circling it like a turntable, while the arena shows its maker's
-- presentation. Runs until the runner stops the engine, or max_time.
-- Job: zook, name, env, texture_dir, window_w/h, view_fit, orbit (degrees per second), angle,
-- max_time, curtain_agent, log. Lua 4.0 dialect (see AIHarnessDriver).

function HLog(msg)
	myLogText = myLogText .. String.format("%8.3f ", System.RealTime() - myStartReal) .. msg .. "\n"
	String.WriteFile(myJob.log, myLogText)
end

function MessageHarnessStart(job)
	myJob = job
	myStartReal = System.RealTime()
	myLogText = ""
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
	-- As in trials: the target before the environment (environments move "BuilderTarget").
	myTarget = Agent.Create("ClickableTarget", Vector.New(0, 0, -52), 0.5)
	Config.Set("BuilderTarget", myTarget)
	myEnv = Agent.Create(job.env, nil, 130, 130, Quatn.New())
	myBuilder = Agent.Create("HarnessBuilder", Vector.New(0, 0, 0), "harness")
	Agent.SendMessage("Load", myBuilder, job.zook, job.texture_dir, job.name, 0)
	Agent.SendMessage("SetGenomeVisibility", myBuilder, false)
	Agent.SendMessage("Express", myBuilder, false, Vector.New(0, 0, 0), 1)  -- dontStart: it stands still
	myCreature = Agent.SendMessage("GetExpressedCreature", myBuilder)
	Agent.SendMessage("SetVisualFlags", myCreature, 1)
	-- Frame it from its size, as the workshop's previews do, from a little above.
	local box = Agent.SendMessage("HarnessBox", myBuilder)
	local r = 4
	if box ~= nil then
		r = Number.max(box.maxx - box.minx, Number.max(box.maxy - box.miny, box.maxz - box.minz))
	end
	local d = 0.8 * (0.7 * r / Number.tan(job.angle / 2) + 0.8 * r)  -- a bit closer than the previews
	Agent.SendMessage("SetMinAngle", myCamera, job.angle)
	Agent.SendMessage("SetView", myCamera, { zook = 1, target = Vector.New(0, 0, 0),
		offset = Vector.New(0.7 * d, 0.35 * d, 0.7 * d), verticalViewAngle = job.angle, targetHeight = 0 })
	Agent.SendMessage("Orbit", myCamera, job.orbit)
	World.CloseAccess()
	if job.curtain_agent ~= nil then
		Agent.SendMessage("Destroy", job.curtain_agent)
	end
	HLog("showcase " .. job.name .. " (size " .. String.format("%.1f", r) .. ")")
	Agent.SetTimer("Check", Agent.Me(), "check", 0.5)
end

function TimerCheck()
	if System.RealTime() - myStartReal > myJob.max_time then
		HLog("max time")
		Agent.StopTimer(Agent.Me(), "check")
		World.Destroy(myWorld)
		System.ExitMainLoop()
	end
end
