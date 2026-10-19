# AIHarnessContest specializes ContestRunnerContest

function HLog(msg)
	myLogText = myLogText .. tostring(System.RealTime()) .. " " .. msg .. "\n"
	String.WriteFile(myJob.log, myLogText)
end

function MessageHarnessStart(job, name, def, contestants)
	myJob = job
	myLogText = ""
	HLog("HarnessStart " .. tostring(name))
	myReports = {}
	myReportCount = 0
	myDone = false
	myRoundsCompleted = 0
	Agent.SetTimer("Watchdog", Agent.Me(), "watchdog", 1)
	MessageBeginContest(name, def, contestants)
	HLog("BeginContest returned")
end

function HarnessSimTime()
	if myWorld == nil then
		return -1
	end
	World.OpenAccess(myWorld)
	local t = World.SimTime()
	World.CloseAccess()
	return t
end

function MessageBehaviourReport(message)
	myReportCount = myReportCount + 1
	local r = { name = tostring(message.name), round = myRound, sim_time = HarnessSimTime() }
	if message.scoreboard ~= nil then
		r.score = message.scoreboard.score
		r.contestant = message.scoreboard.contestant
		r.player = message.scoreboard.player
	end
	myReports[myReportCount] = r
	Trace("aiharness: report % round % t=%", r.name, myRound, r.sim_time)
	HLog("report " .. r.name .. " t=" .. tostring(r.sim_time))
	if message.name == "finish" and myJob.stop_on_finish == 1 then
		myRoundsCompleted = myRound
		Agent.PostMessage("EndRound", Agent.Me(), 0.01)
	end
end

function MessageEndRound()
	myRound = myRound + 1
	if myContestDef.rounds[myRound] ~= nil then
		BeginRound()
	else
		HarnessDone("finished")
	end
end

function TimerWatchdog()
	HLog("watchdog t=" .. tostring(HarnessSimTime()))
	if myDone == false and HarnessSimTime() > myJob.max_sim_time then
		HarnessDone("timeout")
	end
end

function HarnessDone(status)
	if myDone ~= false then
		return
	end
	myDone = 1
	Agent.StopTimer(Agent.Me(), "watchdog")
	local result = {
		status = status,
		contest = myContestName,
		green = myJob.green,
		red = myJob.red,
		rounds_completed = myRoundsCompleted,
		sim_time = HarnessSimTime(),
		real_time = System.RealTime(),
		reports = myReports,
	}
	System.WriteTable(myJob.out, result)
	Trace("aiharness: done (%)", status)
	System.ExitMainLoop()
end
