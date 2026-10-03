' ============================================================================
'  AI Workbench - silent launcher
'
'  This is what the desktop shortcut runs. It starts scripts\start.bat with the
'  console window hidden, waits for it to finish, and only surfaces anything to
'  the user when the start actually failed.
'
'  Success path:  no window at all. start.bat opens the browser once the
'                 frontend really serves HTTP 200.
'  Failure path:  a message box with the exit code and the path to
'                 .runtime\logs\startup-error.log. Errors are never swallowed.
'
'  Kept ASCII-only on purpose: Windows Script Host reads .vbs as ANSI, so
'  non-ASCII text would be mangled.
' ============================================================================
Option Explicit

Dim shell, fso, scriptDir, repoRoot, batPath, errorLog, exitCode, message

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
repoRoot  = fso.GetParentFolderName(scriptDir)
batPath   = fso.BuildPath(scriptDir, "start.bat")
errorLog  = fso.BuildPath(repoRoot, ".runtime\logs\startup-error.log")

If Not fso.FileExists(batPath) Then
    MsgBox "Launcher not found:" & vbCrLf & batPath, vbCritical, "AI Workbench"
    WScript.Quit 2
End If

' 0 = hidden window, True = wait for the command to complete.
exitCode = shell.Run("""" & batPath & """ --hidden", 0, True)

If exitCode <> 0 Then
    message = "AI Workbench failed to start." & vbCrLf & vbCrLf & _
              "Exit code: " & exitCode & vbCrLf & vbCrLf & _
              "Details are in:" & vbCrLf & errorLog & vbCrLf & vbCrLf & _
              "Run scripts\start.bat from a command prompt to see the full output."
    MsgBox message, vbCritical, "AI Workbench"
    WScript.Quit exitCode
End If

WScript.Quit 0
