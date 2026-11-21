Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\HP\.gemini\antigravity\scratch\peakcut"
WshShell.Run """C:\Users\HP\.gemini\antigravity\scratch\peakcut\.venv\Scripts\python.exe"" -m studio.server", 0, False
