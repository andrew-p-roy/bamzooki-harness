# HarnessSelection specializes UIAgent

-- bamzooki harness: the Simulator's Zook selection screen (ContestRunner's), showing the next
-- knockout game: the round, its contest (picked in the list, with its picture) and the two Zooks
-- turning in the green and red panels. Runs until the runner stops the engine, or max_time.
-- Job: title, contest, zooks = { {path, label}, {path, label} }, caption, max_time,
-- curtain_agent, log. Lua 4.0 dialect (see AIHarnessDriver).

function HLog(msg)
	myLogText = myLogText .. String.format("%8.3f ", System.RealTime() - myStartReal) .. msg .. "\n"
	String.WriteFile(myJob.log, myLogText)
end

function ReadContestDefinitions()
	myContests = {}
	local packs = System.GetDirectoryContents("ContestPacks/", "*.dat", 1)
	for index, value in packs do
		local pack = System.ReadTable("ContestPacks/" .. value)
		for name, contest in pack do
			myContests[name] = contest
		end
	end
end

function MessageHarnessStart(job)
	myJob = job
	myStartReal = System.RealTime()
	myLogText = ""
	ReadContestDefinitions()
	local width, height = GUI.Area()
	local margin = 16
	-- As ContestRunner.CreateUI: the contests on the left, the Zooks on the right.
	GUI.CreatePicture("background", "white.png", 0, 0, width, height)
	GUI.CreateText("contest_label", job.title, margin, margin, margin, margin, "Fonts/Arial-32.met")
	GUI.SetColour(Segment.contest_label, 0, 0, 0, 1)
	GUI.CreatePicture("contests_border", "white.png", margin, margin + 36, width / 2 - margin, height / 2 - margin)
	GUI.SetColour(Segment.contests_border, 0, 0, 0, 1)
	GUI.CreateList("contests", margin, margin + 36, width / 2 - margin - 1, height / 2 - margin - 1, "fonts/edit.met")
	local names = {}
	local i = 0
	for name, contest in myContests do
		i = i + 1
		names[i] = name
	end
	sort(names)
	GUI.SetListText(Segment.contests, names)
	GUI.SetText(Segment.contests, job.contest)  -- selects it, as ZookSelector does with its list
	GUI.CreatePicture("contest_picture_border", "white.png", margin, height / 2 + margin, width / 2 - margin,
		height - 36 - margin * 2)
	GUI.SetColour(Segment.contest_picture_border, 0, 0, 0, 1)
	local picture = "white.png"
	if myContests[job.contest] ~= nil and myContests[job.contest].picture ~= nil then
		picture = "./Contests/Contest Pack/" .. myContests[job.contest].picture
	end
	GUI.CreatePicture("contest_picture", picture, margin + 1, height / 2 + margin + 1, width / 2 - margin - 1,
		height - 36 - margin * 2 - 1)
	if job.caption ~= nil then
		GUI.CreateText("caption", job.caption, margin, height - margin - 32, margin, height - margin - 32,
			"Fonts/Arial-32.met")
		GUI.SetColour(Segment.caption, 0, 0, 0, 1)
	end
	myPreviews = {}
	local colours = { {0, 0.7, 0.4}, {1, 0.1, 0.1} }
	for i = 1, 2 do
		local x1 = width / 2 + margin
		local x2 = width - margin
		local y1 = (i - 1) * (height - 36 - margin) / 2 + margin + 36
		local y2 = i * (height - 36 - margin) / 2 - margin
		local pic = GUI.CreatePicture("back" .. i, "white.png", x1, y1, x2, y2)
		GUI.SetColour(pic, colours[i][1], colours[i][2], colours[i][3], 1)
		local label = GUI.CreateText("label" .. i, job.zooks[i].label, x1, y1 - 36 - 8, x1, y1 - 36 - 8,
			"Fonts/Arial-32.met")
		GUI.SetColour(label, 0, 0, 0, 1)
		myPreviews[i] = Agent.Create("ZookPreview", x1 + 8, y1 + 8, x2 - 8, y2 - 8)
		Agent.SendMessage("Load", myPreviews[i], nil, job.zooks[i].path, nil)
	end
	if job.curtain_agent ~= nil then
		Agent.SendMessage("Destroy", job.curtain_agent)
	end
	HLog("selection " .. job.contest .. ": " .. job.zooks[1].label .. " v " .. job.zooks[2].label)
	Agent.SetTimer("Check", Agent.Me(), "check", 0.5)
end

function TimerCheck()
	if System.RealTime() - myStartReal > myJob.max_time then
		HLog("max time")
		Agent.StopTimer(Agent.Me(), "check")
		System.ExitMainLoop()
	end
end
