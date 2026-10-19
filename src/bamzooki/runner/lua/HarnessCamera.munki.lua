# HarnessCamera specializes SplitCamera

-- bamzooki harness camera: the game's SplitCamera (follow-cam, view angles) plus frame capture.
-- Capture saves one image whenever the clock crosses the next 1/fps boundary, named by frame
-- index, so videos have exact timing even if the engine renders slower than real time; missing
-- indices are filled in by Python. The clock is World.SimTime() for live contests, or the replay
-- time pushed with SetClock (the player governor knows it, the camera does not).

function MessageHarnessCapture(dir, fps, start)
	myCapDir = dir
	myCapFps = fps
	myCapStart = start
	myCapLast = -1
	myCapCount = 0
	myCapturing = 1
end

function MessageHarnessStopCapture()
	myCapturing = false
	return myCapCount
end

function MessageSetClock(t)
	myClock = t
end

function SystemCamera(frametime)
	UpdateView()
	if myCapturing == 1 then
		local t = myClock
		if t == nil then
			t = World.SimTime()
		end
		local k = Number.floor((t - myCapStart) * myCapFps)
		if k > myCapLast and k >= 0 then
			Camera.SaveAsJpeg(myCapDir .. String.format("/frame_%06d.jpg", k))
			myCapLast = k
			myCapCount = myCapCount + 1
		end
	end
end
