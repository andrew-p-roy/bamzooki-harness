# HarnessCamera specializes SplitCamera

-- bamzooki harness camera: the game's SplitCamera (follow-cam, view angles) plus frame capture.
-- Capture saves one image whenever the clock crosses the next 1/fps boundary, named by frame
-- index, so videos have exact timing even if the engine renders slower than real time; missing
-- indices are filled in by Python. The clock is World.SimTime() for live contests, or the replay
-- time pushed with SetClock (the player governor knows it, the camera does not).

-- Frames are written as frame_<k>.hex (Camera.GetImageAsString). `format` is reserved and must be
-- passed (Munki silently rejects messages with fewer arguments than the handler declares).
function MessageHarnessCapture(dir, fps, start, format)
	myCapDir = dir
	myCapFps = fps
	myCapStart = start
	myCapLast = -1
	myCapCount = 0
	myCapturing = 1
	return 1
end

function MessageHarnessStopCapture()
	myCapturing = false
	return myCapCount
end

-- Fill the window's actual drawable area (the requested size can differ from the client area,
-- leaving the engine's blue background visible around the view).
function MessageFitWindow()
	local w, h = Camera.Area()
	MessageResize(0, 0, w, h)
	return w
end

-- The follow-both mode narrows its view angle as the Zooks get closer; keep a minimum so the
-- arena stays in view. (Overrides SplitCamera.SetViewAngle.)
function MessageSetMinAngle(a)
	myMinAngle = a
end

function SetViewAngle(angle)
	local minAngle = myMinAngle
	if minAngle == nil then
		minAngle = 10
	end
	if angle < minAngle then
		angle = minAngle
	end
	myVerticalViewAngle = angle
	SetViewAngles()
end

function MessageSetClock(t)
	myClock = t
end

function SystemCamera(frametime)
	-- Follow the window's drawable area (it can change if the window is resized).
	local w, h = Camera.Area()
	if w ~= myAreaW or h ~= myAreaH then
		myAreaW = w
		myAreaH = h
		MessageResize(0, 0, w, h)
	end
	UpdateView()
	if myCapturing == 1 then
		local t = myClock
		if t == nil then
			t = World.SimTime()
		end
		local k = Number.floor((t - myCapStart) * myCapFps)
		if k > myCapLast and k >= 0 then
			-- Camera.SaveAsJpeg (used by the game's dead code) does not exist in this build and
			-- Camera.SaveToFile writes nothing; GetImageAsString renders and returns the image.
			String.WriteFile(myCapDir .. String.format("/frame_%06d.hex", k), Camera.GetImageAsString())
			myCapLast = k
			myCapCount = myCapCount + 1
		end
	end
end
