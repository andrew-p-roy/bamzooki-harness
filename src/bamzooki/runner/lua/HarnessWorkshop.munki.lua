# HarnessWorkshop specializes Agent embeds Visual

-- bamzooki harness workshop: finalise draft Zooks and photograph them (previews for models).
-- Job: tasks = { i = { draft, out, name, owner, shots = { j = { path, view } } } }, shot_angle,
-- texture_dir, shot_w, shot_h, out (result table), log. Lua 4.0 dialect (see AIHarnessDriver).

function HLog(msg)
	myLogText = myLogText .. String.format("%8.3f ", System.RealTime() - myStartReal) .. msg .. "\n"
	String.WriteFile(myJob.log, myLogText)
end

function MessageHarnessStart(job)
	myJob = job
	myStartReal = System.RealTime()
	myLogText = ""
	myResults = {}
	myWorld = World.Create()
	World.OpenAccess(myWorld)
	Visual.CreateLight(0, "ambient")
	Visual.SetLightColour(Segment.ambient, 0.45, 0.45, 0.45)
	Visual.CreateLight(1, "directional")
	Visual.SetLightColour(Segment.directional, 0.75, 0.75, 0.75)
	local trans = Matrix.New()
	Matrix.SetTranslation(trans, Vector.New(1, 1.5, 1.2))
	Matrix.LookAt(trans, Vector.New(0, 0, 0), Vector.New(0, 1, 0))
	Visual.SetLightTransform(Segment.directional, trans)
	myCamera = Agent.Create("HarnessCamera", 0, 0, job.shot_w, job.shot_h)
	World.CloseAccess()
	if job.curtain_agent ~= nil then
		-- Black only while loading: from here the window shows each Zook as it is photographed.
		Agent.SendMessage("Destroy", job.curtain_agent)
	end
	myTask = 0
	-- Give the camera a frame to exist before the first snapshot.
	Agent.PostMessage("NextTask", Agent.Me(), 0.2)
end

function MessageNextTask()
	myTask = myTask + 1
	local task = myJob.tasks[myTask]
	if task == nil then
		Done()
		return
	end
	World.OpenAccess(myWorld)
	local builder = Agent.Create("HarnessBuilder", Vector.New(0, 0, 0), task.owner)
	local info = Agent.SendMessage("HarnessFinalize", builder, task.draft, task.out, myJob.texture_dir, task.name)
	HLog("finalised " .. task.name .. ": " .. tostring(info))
	local shots = 0
	if task.shots ~= nil and info ~= nil then
		for j, s in task.shots do
			local pos, target = ShotFor(info, s.view, myJob.shot_angle)
			Agent.SendMessage("Snapshot", myCamera, s.path, pos, target, myJob.shot_angle, myJob.shot_w, myJob.shot_h)
			shots = shots + 1
		end
	end
	Agent.SendMessage("Destroy", builder)
	World.CloseAccess()
	if info == nil then
		info = { error = "finalize failed" }
	end
	info.shots = shots
	myResults[myTask] = info
	HLog("task " .. myTask .. " done, " .. shots .. " shots")
	Agent.PostMessage("NextTask", Agent.Me(), 0.1)
end

-- Camera position for a named view around the Zook's bounding box. The Zook faces +z
-- (attach.around = 0 is the front of a part).
function ShotFor(info, view, angle)
	local cx = (info.minx + info.maxx) / 2
	local cy = (info.miny + info.maxy) / 2
	local cz = (info.minz + info.maxz) / 2
	local r = info.maxx - info.minx
	if info.maxy - info.miny > r then
		r = info.maxy - info.miny
	end
	if info.maxz - info.minz > r then
		r = info.maxz - info.minz
	end
	local d = 0.7 * r / Number.tan(angle / 2) + 0.8 * r
	local target = Vector.New(cx, cy, cz)
	local off = Vector.New(0.7 * d, 0.45 * d, 0.7 * d)
	if view == "front" then
		off = Vector.New(0, 0.15 * d, d)
	elseif view == "back" then
		off = Vector.New(0, 0.15 * d, -d)
	elseif view == "left" then
		off = Vector.New(d, 0.15 * d, 0)
	elseif view == "right" then
		off = Vector.New(-d, 0.15 * d, 0)
	elseif view == "top" then
		off = Vector.New(0, d, 0.02 * d)
	end
	return target + off, target
end

function Done()
	System.WriteTable(myJob.out, { status = "finished", tasks = myResults })
	HLog("done")
	Segment.Destroy(Segment.ambient)
	Segment.Destroy(Segment.directional)
	World.Destroy(myWorld)
	System.ExitMainLoop()
end
