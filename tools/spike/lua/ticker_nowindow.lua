-- Does the main loop keep running while the engine window is in the background?
ROOT = System.GetLabeledPath("ROOT")
Config.Set("app_name", "Simulator")
Execute("BuildConfiguration")
Execute("MemoryConfiguration")
Execute("ProductConfiguration")
Config.Set("exclusive", 0)
Execute("MakeWindowCaption", "aiharness ticker")
Agent.CreateClassFromString(String.ReadFile(SCRIPT_DIR .. "/Ticker.sax.lua"))
-- no window
ticker = Agent.Create("Ticker")
Agent.PostMessage("Start", ticker, 0.01, ROOT .. "/ticker_nowin_log.txt", 8)
System.MainLoop()
Agent.Destroy(ticker)

