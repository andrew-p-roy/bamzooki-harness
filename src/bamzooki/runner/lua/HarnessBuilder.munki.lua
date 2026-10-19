# HarnessBuilder specializes BuilderParts

-- bamzooki harness: finalise a draft Zook with the Zook Kit's own BuilderParts code.
-- Load (clamps out-of-range sliders, assigns mirror groups, lays parts out on their parents'
-- surfaces), create default IK paths for moving parts, compute passport stats, save.
-- Installed into the sandbox as Agents/World/HarnessBuilder (world agents can't be runtime classes).

function EnsureIK(node)
	if node.element_type == "bodysolid" then
		if node.leg_type == "1" or node.leg_type == "2" then
			MessageIKPositions(node.partId)
		end
	end
	for i, child in node.children do
		EnsureIK(child)
	end
end

-- Every argument must be passed (Munki drops messages with missing arguments).
function MessageHarnessFinalize(draft, out, textureDir, zookName)
	local invalid = MessageLoad(draft, textureDir, zookName, 0)
	EnsureIK(myRootNode)
	UpdatePositions(myRootNode, false)
	MessageSetNoseBottomPosition(Vector.New(0, 0, 0))
	MessageSetPhysicalProperties()
	MessageSave(out)
	local count, weight = MessageCountAndWeigh()
	local minx, miny, minz, maxx, maxy, maxz = MessageBoundingBox()
	return { clamped = invalid, parts = count, weight = weight,
		minx = minx, miny = miny, minz = minz, maxx = maxx, maxy = maxy, maxz = maxz }
end
