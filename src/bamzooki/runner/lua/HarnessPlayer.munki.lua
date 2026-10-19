# HarnessPlayer specializes Agent embeds Visual

-- bamzooki harness replay renderer: plays a .bvz/.bmv recording through a HarnessCamera and
-- (optionally) captures frames by replay time. Modelled on the game's VisualFilePlayer, no UI.
-- Job fields: replay (.bvz or .bmv), tmp_bmv, speed, view, window_w/h, capture, capture_dir,
-- capture_fps, out, log. Lua 4.0 dialect (see AIHarnessDriver).

function HLog(msg)
	myLogText = myLogText .. String.format("%8.3f ", System.RealTime() - myStartReal) .. msg .. "\n"
	String.WriteFile(myJob.log, myLogText)
end

function MessageHarnessStart(job)
	myJob = job
	myStartReal = System.RealTime()
	myLogText = ""
	myDone = false
	myLastTime = 0
	local file = job.replay
	if String.strsub(file, -4) == ".bvz" then
		System.Unzip(file, job.tmp_bmv)
		file = job.tmp_bmv
	end
	myWorld = World.Create(2)
	World.OpenAccess(myWorld)
	CreateLights()
	myCamera = Agent.Create("HarnessCamera", 0, 0, job.window_w, job.window_h)
	Agent.SendMessage("FitWindow", myCamera)
	local v = job.view
	Agent.SendMessage("SetMinAngle", myCamera, v.min_angle)
	Agent.SendMessage("SetView", myCamera, { zook = v.zook, target = v.target,
		offset = v.offset, verticalViewAngle = v.angle, targetHeight = 0 })
	World.EraseRemoteVisuals()
	local rate, startTime = World.OpenVisualInput(file)
	myStartTime = startTime
	World.SeekVisual(0)
	if job.capture == 1 then
		Agent.SendMessage("SetClock", myCamera, 0)
		-- Messages must get every declared argument, or Munki silently rejects them.
		local ok = Agent.SendMessage("HarnessCapture", myCamera, job.capture_dir, job.capture_fps, 0, "hex")
		HLog("camera " .. tostring(myCamera) .. " capture -> " .. tostring(ok))
	end
	World.CloseAccess()
	if rate == false then
		HLog("incompatible replay file (version " .. tostring(startTime) .. ")")
		Done("bad_file")
		return
	end
	HLog("opened " .. job.replay .. ": rate " .. tostring(rate) .. ", starts at " .. tostring(startTime))
	-- Timers must be longer than max_tick_size (0.0625 in the player profile).
	Agent.SetTimer("Update", Agent.Me(), "readvis", job.read_interval)
end

function CreateLights()
	Config.Set("visual_ignore_lights", 1)
	Visual.CreateShadowSource(Vector.New(2, 10, 2), .2)
	Visual.CreateLight(0, "ambient")
	Visual.SetLightColour(Segment.ambient, 0.3, 0.3, 0.3)
	Visual.CreateLight(1, "directional")
	Visual.SetLightColour(Segment.directional, 0.7, 0.7, 0.7)
	local trans = Matrix.New()
	Matrix.SetTranslation(trans, Vector.New(-1, 1, -1))
	Matrix.LookAt(trans, Vector.New(0, 0, 0), Vector.New(0, 1, 0))
	Visual.SetLightTransform(Segment.directional, trans)
end

function TimerUpdate()
	if myDone ~= false then
		return
	end
	World.OpenAccess(myWorld)
	local t = World.ReadVisual(myJob.speed)
	if t >= 0 then
		myLastTime = t
		Agent.SendMessage("SetClock", myCamera, t)
	end
	World.CloseAccess()
	if t < 0 then
		Done("finished")
	end
end

function Done(status)
	if myDone ~= false then
		return
	end
	myDone = 1
	Agent.StopTimer(Agent.Me(), "readvis")
	local frames = 0
	if myWorld ~= nil then
		World.OpenAccess(myWorld)
		if myJob.capture == 1 then
			frames = Agent.SendMessage("HarnessStopCapture", myCamera)
		end
		World.CloseVisualInput()
		World.CloseAccess()
	end
	HLog("done: " .. status .. ", frames " .. tostring(frames))
	System.WriteTable(myJob.out, { status = status, start_time = myStartTime, last_time = myLastTime,
		frames = frames, real_time = System.RealTime() - myStartReal })
	if myWorld ~= nil then
		Segment.Destroy(Segment.ambient)
		Segment.Destroy(Segment.directional)
		World.Destroy(myWorld)
		myWorld = nil
	end
	System.DeleteFile(myJob.tmp_bmv)
	System.ExitMainLoop()
end
