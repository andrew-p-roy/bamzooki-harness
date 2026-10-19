# Ticker specializes Agent

function MessageStart(log, seconds)
	myLog = log
	myText = ""
	myStart = System.RealTime()
	mySeconds = seconds
	myTicks = 0
	Agent.SetTimer("Tick", Agent.Me(), "tick", 0.25)
end

function TimerTick()
	myTicks = myTicks + 1
	local t = System.RealTime() - myStart
	myText = myText .. tostring(myTicks) .. " " .. tostring(t) .. "\n"
	String.WriteFile(myLog, myText)
	if t > mySeconds then
		Agent.StopTimer(Agent.Me(), "tick")
		System.ExitMainLoop()
	end
end
