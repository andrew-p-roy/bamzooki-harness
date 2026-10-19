-- Plain-text script run through the encrypted loader.
local root = System.GetLabeledPath("ROOT")
Trace("hello_plain: running from plain text")
System.WriteTable(root .. "/hello_plain_out.dat", { status = "ok", via = "loader", version = _VERSION })
String.WriteFile(root .. "/hello_plain_out.txt", "{\"status\": \"ok\", \"via\": \"String.WriteFile\"}\n")
System.ExitMainLoop()
