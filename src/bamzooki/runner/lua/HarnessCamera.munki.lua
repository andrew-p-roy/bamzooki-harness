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
	FitArea(w, h)
	return w
end

-- Letterbox: keep the view at `aspect` (width / height) and at most `maxw` wide, centred in the
-- window; the rest shows the engine's background. Frames are captured at the view's size.
function MessageSetFit(aspect, maxw)
	myFitAspect = aspect
	myFitMaxW = maxw
	myAreaW = -1  -- re-fit on the next frame
	return 1
end

-- A camera that only shows its background colour, behind a letterboxed one: the bars are
-- otherwise the engine's own background (blue, sometimes black).
function MessageBars(r, g, b)
	Camera.SetBackground(r, g, b, 1)
	Camera.SetClipPlanes(0.001, 0.002)  -- nothing in the world is that close
	return 1
end

function MessageToFront()
	Camera.ToFront()
	return 1
end

function FitArea(w, h)
	local vw = w
	local vh = h
	if myFitAspect ~= nil then
		if vw > myFitMaxW then
			vw = myFitMaxW
		end
		vh = Number.floor(vw / myFitAspect + 0.5)
		if vh > h then
			vh = h
			vw = Number.floor(h * myFitAspect + 0.5)
		end
	end
	local x = Number.floor((w - vw) / 2)
	local y = Number.floor((h - vh) / 2)
	MessageResize(x, y, x + vw, y + vh)
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

-- One still image from an explicit viewpoint (previews). GetImageAsString renders on demand;
-- the game's Photographer calls it twice so the image reflects the new camera transform.
-- Leaves follow-cam mode (zook = -1) so the next frame doesn't move the camera back.
function MessageSnapshot(path, pos, target, angle, w, h)
	mySnapshotMode = 1
	MessageResize(0, 0, w, h)
	myAreaW = w
	myAreaH = h
	myVerticalViewAngle = angle
	SetViewAngles()
	Camera.SetPosition(pos)
	Camera.SetTarget(target, Vector.New(0, 1, 0))
	local image = Camera.GetImageAsString()
	image = Camera.GetImageAsString()
	String.WriteFile(path, image)
	return 1
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
		FitArea(w, h)
	end
	-- SplitCamera.UpdateView needs a view (SetView); until then (or for stills) leave it alone.
	if myZook ~= nil and myZook ~= -1 then
		UpdateView()
	elseif myZook == -1 and myTarget ~= nil and mySnapshotMode == nil then
		UpdateView()
	end
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
