# HarnessCurtain specializes Agent embeds GUI

-- bamzooki harness curtain: black over the whole window from boot until the driver has created its
-- cameras (it then sends Destroy), so a letterboxed window shows black instead of the engine's blue
-- background while the world is created (~2 s, when nothing is drawn and the last frame stays up).
-- Cameras can't do this: they need the world. The picture is a 2x2 black image string in
-- Camera.GetImageAsString's format ("08080800", width, height, RGB as hex), oversized so it covers
-- the window at any size. Lua 4.0 dialect: no `local function`, conditions must not be nil.

BLACK = "0808080000020002000000000000000000000000"

function Initialize()
	GUI.CreatePicture("curtain", "", 0, 0, 8192, 8192)
	GUI.SetImageFromString(Segment.curtain, BLACK)
end

function MessageDestroy()
	Agent.Destroy()
end
