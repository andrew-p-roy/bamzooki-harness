# HarnessCountdown specializes Agent embeds GUI

-- bamzooki harness countdown: "3", "2", "1", "Go!" over the game, one per sim second, ending on Go.
-- The game's own Countdown/VideoCountdown, made big: the same picture (Count.png, a 4-cell strip,
-- retextured to the next quarter each second), each count fading out over its second, but sized to
-- the window and smoothed by its own timer. notify gets CountdownStep(label) as each count shows,
-- for the driver's log (the runner plays the beeps from it), then CountdownStep("end").
-- Lua 4.0 dialect: no `local function`, conditions must not be nil.

LABELS = { "3", "2", "1", "go" }
TEXTURE = "./Agents/Governor/Countdown/Count"

function Initialize(world, goTime, size, notify)
	myWorld = world
	myGo = goTime
	mySize = size
	myNotify = notify
	myShown = -1
	myDone = false
	GUI.CreatePicture("count", TEXTURE, 0, 0, 1, 1, 0, 0, 0, 0)
	Place()
	Agent.SetTimer("Tick", Agent.Me(), "tick", 0.02)
end

-- Centre the picture, mySize of the window's shorter side (it follows the window if resized).
function Place()
	local w, h = GUI.Area()
	local side = h
	if w < h then
		side = w
	end
	local half = side * mySize / 2
	GUI.Resize(Segment.count, w / 2 - half, h / 2 - half, w / 2 + half, h / 2 + half)
end

function TimerTick()
	if myDone ~= false then
		return
	end
	World.OpenAccess(myWorld)
	local t = World.SimTime()
	World.CloseAccess()
	local s = t - (myGo - 3)
	if s < 0 then
		return
	end
	local cell = Number.floor(s)
	if cell > 3 then
		-- Done: hide, tell the driver (it stops tracking us), and go (not from inside our own timer).
		myDone = 1
		Agent.StopTimer(Agent.Me(), "tick")
		GUI.SetColour(Segment.count, 1, 1, 1, 0)
		if myNotify ~= nil then
			Agent.SendMessage("CountdownStep", myNotify, "end")
		end
		Agent.PostMessage("Destroy", Agent.Me(), 0)
		return
	end
	local f = s - cell
	if cell ~= myShown then
		myShown = cell
		GUI.Retexture(Segment.count, TEXTURE, cell * 0.25, 0, (cell + 1) * 0.25, 1)
		GUI.ToFront(Segment.count)
		if myNotify ~= nil then
			Agent.SendMessage("CountdownStep", myNotify, LABELS[cell + 1])
		end
	end
	Place()
	GUI.SetColour(Segment.count, 1, 1, 1, 1 - f)
end

function MessageDestroy()
	if myDone == false then
		myDone = 1
		Agent.StopTimer(Agent.Me(), "tick")
	end
	Agent.Destroy()
end
