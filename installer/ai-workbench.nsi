; ============================================================================
;  AI Workbench - installer
;
;  Built with NSIS directly rather than through Tauri's bundler.
;
;  Reason: the payload is thousands of files and well over 400 MB (the exact count
;  changes on every build, so it is measured rather than stated here - see
;  build/installer-report.json), and what this installer has to do is not what a
;  bundler does. It must refuse to install over a running copy,
;  create shortcuts that point at the exe, register an uninstaller that stops
;  four child processes before deleting anything, and - the part that decides the
;  design - ask the user whether to keep their data. A generated NSIS script can
;  be extended with `installerHooks`, but past a certain amount of logic that is
;  a worse place to write it than a script of our own.
;
;  Decisions this file embodies, and why:
;
;  * Per-user install into %LOCALAPPDATA%.  `RequestExecutionLevel user` means no
;    UAC prompt, and more importantly it means the application can write
;    `.runtime` - the PostgreSQL cluster, the vector store, uploads and logs. An
;    install under Program Files would need elevation for every one of those
;    writes, which is not a product anyone can use.
;
;  * PostgreSQL runs as a child process, not a Windows service.  A service needs
;    administrator rights to install, survives the application being closed (a
;    database left running on a student's laptop), and needs its own uninstall
;    path. The child-process design already has a guarantee a service cannot
;    give: the whole tree is in a job object with KILL_ON_JOB_CLOSE, so closing
;    the window - or killing it - takes PostgreSQL, Redis and Qdrant with it.
;    That was verified in an earlier round against graceful close, force-kill and
;    re-entry, with no residue.
;
;  * User data is preserved by default on uninstall.  It is moved aside rather
;    than deleted, and the path is shown. A reinstall offers to bring it back.
; ============================================================================

Unicode true
!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "TextFunc.nsh"

; ------------------------------------------------------------------- config --
!define APP_NAME        "AI Workbench"
!define APP_EXE         "AI Workbench.exe"
!define APP_PUBLISHER   "AI Workbench"
!define APP_VERSION     "2.2.0"
!define UNINST_KEY      "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
!define DATA_DIR_NAME   "AI Workbench Data"
!define PAYLOAD         "..\build\installer-payload"

Name "${APP_NAME}"
OutFile "..\build\installer\AI-Workbench-Setup.exe"
InstallDir "$LOCALAPPDATA\${APP_NAME}"
InstallDirRegKey HKCU "${UNINST_KEY}" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show

VIProductVersion "2.2.0.0"
VIAddVersionKey "ProductName"     "${APP_NAME}"
VIAddVersionKey "CompanyName"     "${APP_PUBLISHER}"
VIAddVersionKey "FileDescription" "${APP_NAME} installer"
VIAddVersionKey "FileVersion"     "${APP_VERSION}"

; ------------------------------------------------------------------- pages ---
!define MUI_WELCOMEPAGE_TITLE "安装 ${APP_NAME}"
!define MUI_WELCOMEPAGE_TEXT "这是一个运行在你电脑上的私人 AI 工作台。$\r$\n$\r$\n它会带上你的档案、你的资料和你的做法去干活，干完交给你验收。$\r$\n$\r$\n所有数据都留在本机。安装不需要管理员权限。"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES

; MUI2 generates `.onUserAbort` itself and calls this hook from inside it — see the
; `OnInstallAborted` function at the bottom for why the hook is needed at all.
;
; It has to be defined **here**, before `MUI_LANGUAGE` below. MUI2 expands
; `MUI_FUNCTION_ABORTWARNING` while processing the language macros, and a `!define` that
; comes later is simply not there yet: the build then succeeds with
; `warning 6010: install function "OnInstallAborted" not referenced - zeroing code out`,
; which is the compiler saying the safety net was thrown away.
!define MUI_CUSTOMFUNCTION_ABORT OnInstallAborted

!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "立即启动 ${APP_NAME}"
!define MUI_FINISHPAGE_TEXT "安装完成。$\r$\n$\r$\n第一次启动会初始化数据库和服务，需要一两分钟，界面上会显示进度。$\r$\n$\r$\n数据目录：$INSTDIR\.runtime"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

; ------------------------------------------------------------------ helpers --
!macro CheckNotRunning
  ; Refuse to install or uninstall over a running copy.
  ;
  ; The check is delegated to PowerShell and read from its *exit code*, not by
  ; searching text. `tasklist` exits 0 whether or not it found anything, and its
  ; "no tasks" line is localized - so matching on output would be matching on the
  ; machine's language. An exit code reads the same in every language.
  ;
  ; `tasklist` rather than a window-title search: the shell may be running with no
  ; visible window (mid-startup, or the user closed it and a child is still
  ; winding down), and overwriting the exe underneath a live process produces a
  ; half-upgraded install that is worse than a refusal.
  nsExec::ExecToStack 'powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-Process -Name ''AI Workbench'' -ErrorAction SilentlyContinue) { exit 7 } else { exit 0 }"'
  Pop $0
  Pop $1
  ${If} $0 == "7"
    ; IfSilent jump_if_silent jump_if_not_silent: the first label is taken when
    ; /S is in effect. This used to read IfSilent +2 0, and +2 counts from the
    ; next instruction - so it jumped over the Abort as well, and a silent uninstall
    ; skipped the guard entirely.  falls through to the MessageBox; +2 skips
    ; the MessageBox and lands on the Abort, so the refusal holds in both modes.
    IfSilent 0 +2
    MessageBox MB_OK|MB_ICONSTOP "${APP_NAME} 正在运行。$\r$\n$\r$\n请先关闭它，再重新运行安装程序。"
    Abort
  ${EndIf}
!macroend

!macro StopEverything
  ; Stop the stack the way the product does, then make sure nothing survived.
  ;
  ; Two steps on purpose: `stop.bat` is the graceful path (it stops PostgreSQL with a
  ; checkpoint rather than a kill), and a PID sweep is the guarantee.
  ;
  ; **The guarantee used to be `taskkill /F /IM`.** That matches on the image name, so
  ; installing or uninstalling this application force-killed the user's own
  ; `postgres.exe`, `redis-server.exe` and `qdrant.exe` — a real loss for anyone who runs
  ; their own database. `scripts/stop.bat` states in its header that it deliberately has
  ; no `/IM` rule for exactly that reason; the installer had four.
  ;
  ; `stop-by-pid.ps1` is the scoped replacement. Every process this product starts is
  ; recorded in `<data>\.runtime\pids\<name>.pid`, PostgreSQL writes `postmaster.pid`,
  ; and the script additionally refuses any pid whose executable does not live under the
  ; product's own directory — so a stale pid file cannot take out an unrelated process
  ; that inherited the number.
  IfFileExists "$INSTDIR\scripts\stop.bat" 0 stop_bat_done
    nsExec::ExecToLog '"$INSTDIR\scripts\stop.bat"'
    Pop $0
  stop_bat_done:
  Sleep 3000

  ; Our own binary by name is fine — the name belongs to this product and to nothing
  ; else. It is the three third-party names (`postgres.exe`, `redis-server.exe`,
  ; `qdrant.exe`) that were the problem, and they are gone.
  nsExec::ExecToLog 'taskkill /F /IM "${APP_EXE}" /T'
  Pop $0

  IfFileExists "$INSTDIR\scripts\stop-by-pid.ps1" 0 stop_pid_done
    ; The runtime root comes from the marker the installer wrote, so a custom data
    ; directory is swept too.
    ReadEnvStr $0 "LOCALAPPDATA"
    StrCpy $1 "$0\${DATA_DIR_NAME}\.runtime"
    IfFileExists "$INSTDIR\data-dir.txt" 0 stop_pid_sweep
      FileOpen $2 "$INSTDIR\data-dir.txt" r
      FileRead $2 $1
      FileClose $2
      ; Trailing newline from FileRead.
      ${TrimNewLines} $1 $1
    stop_pid_sweep:
    nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\scripts\stop-by-pid.ps1" -RuntimeRoot "$1"'
    Pop $0
  stop_pid_done:
  Sleep 2000
!macroend


; ------------------------------------------------------- install verification --
;
; The three macros below exist because of one sentence from the user:
;
;     装得上、看不见、删不掉
;
; A real install on this machine ended with `Uninstall.exe` written, **none** of the
; eight registry values written, and no Start Menu entry at all. The software ran; it
; appeared in no list; there was no uninstall entry anywhere the user would look. The
; installer reported success, because that is what NSIS does: `WriteRegStr` sets an
; error flag that nothing here was reading, `CreateShortCut` does not even do that, and
; the progress bar reaches the end either way.
;
; **铺完文件不等于装好。** These macros check the things that make the install *real*
; and report the first one that is missing, by name.

; Sets $R0 to the description of the first missing file, or leaves it empty.
;
; `${AndIfNot} ${FileExists}` rather than a string comparison: LogicLib's expression
; parser splits on quotes, so a value that *contains* a quote — which
; `UninstallString` does — arrives as five parameters to a four-parameter macro and
; makensis stops with "macro _And requires 4 parameter(s), passed 5". Found by compiling;
; the registry comparisons below therefore put the expected value in a register first.
!macro FailIfMissing PATH DESC
  ${If} $R0 == ""
  ${AndIfNot} ${FileExists} "${PATH}"
    StrCpy $R0 "${DESC}"
  ${EndIf}
!macroend

; The whole check. Leaves the answer in $R0: empty means the install is complete.
;
; Every comparison is register-to-register on purpose. `${If} $R1 != "some path"` works
; right up until the path contains a quote, and the one value that matters most here —
; `UninstallString`, which is `"C:\...\Uninstall.exe"` — contains two.
!macro VerifyInstall
  StrCpy $R0 ""

  !insertmacro FailIfMissing "$INSTDIR\${APP_EXE}" "程序文件 $INSTDIR\${APP_EXE}"
  !insertmacro FailIfMissing "$INSTDIR\Uninstall.exe" "卸载程序 $INSTDIR\Uninstall.exe"
  !insertmacro FailIfMissing "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "开始菜单快捷方式"
  !insertmacro FailIfMissing "$DESKTOP\${APP_NAME}.lnk" "桌面快捷方式"

  ; The three values that decide whether the program shows up in 设置 → 应用 and whether
  ; the 「卸载」 button there works. DisplayName is the name in the list, UninstallString
  ; is what the button runs, DisplayIcon is the picture next to it.
  ReadRegStr $R1 HKCU "${UNINST_KEY}" "DisplayName"
  StrCpy $R2 "${APP_NAME}"
  ${If} $R0 == ""
  ${AndIf} $R1 != $R2
    StrCpy $R0 "注册表 DisplayName（应用列表里显示的名字）"
  ${EndIf}

  ReadRegStr $R1 HKCU "${UNINST_KEY}" "UninstallString"
  StrCpy $R2 '"$INSTDIR\Uninstall.exe"'
  ${If} $R0 == ""
  ${AndIf} $R1 != $R2
    StrCpy $R0 "注册表 UninstallString（应用列表里的「卸载」按钮要用它）"
  ${EndIf}

  ReadRegStr $R1 HKCU "${UNINST_KEY}" "DisplayIcon"
  StrCpy $R2 "$INSTDIR\${APP_EXE}"
  ${If} $R0 == ""
  ${AndIf} $R1 != $R2
    StrCpy $R0 "注册表 DisplayIcon（应用列表里的图标）"
  ${EndIf}

  ; InstallLocation is what `InstallDirRegKey` reads on the next install, and what
  ; `scripts/install_selfcheck.py` uses to find the app. A wrong one is how a reinstall
  ; lands somewhere unexpected.
  ReadRegStr $R1 HKCU "${UNINST_KEY}" "InstallLocation"
  StrCpy $R2 "$INSTDIR"
  ${If} $R0 == ""
  ${AndIf} $R1 != $R2
    StrCpy $R0 "注册表 InstallLocation"
  ${EndIf}
!macroend

; The repair entry point, written into the install directory.
;
; Pure ASCII, because cmd.exe reads a .bat as ANSI and a Chinese character in one turns
; into mojibake at best and a syntax error at worst. The Chinese lives in the shortcut
; name instead, where it is a UTF-16 string and safe.
;
; It does not reinstall and does not re-download 440 MB: it only fills in the registry
; values and shortcuts that are missing, then checks all four again.
!macro WriteRepairBat
  FileOpen $9 "$INSTDIR\repair-install.bat" w
  FileWrite $9 "@echo off$\r$\n"
  FileWrite $9 "REM AI Workbench - repair this installation.$\r$\n"
  FileWrite $9 "REM$\r$\n"
  FileWrite $9 "REM Fills in the parts that make Windows aware of the program: the app-list$\r$\n"
  FileWrite $9 "REM registry entries, the Start Menu shortcut and the desktop shortcut.$\r$\n"
  FileWrite $9 "REM It does not reinstall, does not download anything, and does not touch data.$\r$\n"
  FileWrite $9 "setlocal$\r$\n"
  ; `%~dp0`, not `$$~dp0`.
  ;
  ; `$$` is NSIS's escape for a literal `$`, so the old line wrote `set "HERE=$~dp0"` into
  ; the batch file — and batch wants `%~dp0`. `%HERE%` then expanded to the literal text
  ; `$~dp0`, the python path under it never existed, and the script exited 2 on line 9
  ; every single time. The repair entry point shipped broken and stayed broken: round 14
  ; verified `install_selfcheck.py --repair` by calling Python directly, which is not the
  ; thing the user clicks, and round 15's report recorded this as FAILED without fixing it.
  ;
  ; `%` is not special to NSIS. It is written literally.
  FileWrite $9 "set $\"HERE=%~dp0$\"$\r$\n"
  FileWrite $9 "if not exist $\"%HERE%vendor\runtime\python\python.exe$\" ($\r$\n"
  FileWrite $9 "  echo The program files are incomplete - python.exe is missing.$\r$\n"
  FileWrite $9 "  echo Run the installer again.$\r$\n"
  FileWrite $9 "  pause$\r$\n"
  FileWrite $9 "  exit /b 2$\r$\n"
  FileWrite $9 ")$\r$\n"
  FileWrite $9 "$\"%HERE%vendor\runtime\python\python.exe$\" $\"%HERE%scripts\install_selfcheck.py$\" --repair$\r$\n"
  FileWrite $9 "set CODE=%ERRORLEVEL%$\r$\n"
  FileWrite $9 "echo.$\r$\n"
  FileWrite $9 "if $\"%CODE%$\"==$\"0$\" ($\r$\n"
  FileWrite $9 "  echo Repaired. You can close this window.$\r$\n"
  FileWrite $9 ") else ($\r$\n"
  FileWrite $9 "  echo Still incomplete - see the output above.$\r$\n"
  FileWrite $9 ")$\r$\n"
  FileWrite $9 "pause$\r$\n"
  FileWrite $9 "exit /b %CODE%$\r$\n"
  FileClose $9
!macroend

; ------------------------------------------------------------------ install --
Section "Install"
  SectionIn RO
  !insertmacro CheckNotRunning

  ; A reinstall must not silently discard the existing database. The payload
  ; contains no `.runtime`, so extracting over an existing install cannot
  ; overwrite user data - but saying so explicitly is worth more than relying on
  ; a property of the file list.
  IfFileExists "$INSTDIR\.runtime\*.*" 0 no_existing_data
    DetailPrint "检测到已有数据目录，将保留：$INSTDIR\.runtime"
  no_existing_data:

  SetOutPath "$INSTDIR"
  DetailPrint "正在释放程序文件（约 440 MB，8,600 个文件）..."
  File /r "${PAYLOAD}\*.*"

  ; Nothing to restore: the data lives outside this directory and was never touched by
  ; a previous uninstall. An earlier design moved it aside on uninstall and moved it
  ; back here; that move is what failed and lost a database, and it is gone.

  ; The environment file is **generated on first launch**, not copied from the example.
  ;
  ; This used to be `CopyFiles .env.example .env`. The example selects `APP_ENV=dev`,
  ; sets `DEBUG=true` and carries a placeholder `SECRET_KEY` — and under that
  ; configuration the backend seeds `demo / demo1234`. So a fresh install came up in
  ; development mode, with a known account, on a machine where this product reads the
  ; user's files, runs commands and reaches the network. The sign-in screen printed the
  ; credentials.
  ;
  ; `app.cli init-env` now writes a production file with a per-install random key, and
  ; leaves an existing file untouched so an upgrade does not rotate the key and sign the
  ; user out. It runs from `scripts\start-desktop.bat` before the API starts — the
  ; launcher, not the installer, because the key must survive reinstalls and repairs, and
  ; an installer step that runs again on every repair is the wrong place for it.
  ;
  ; `.env.example` is still shipped as a reference. It is never copied to `.env`.
  IfFileExists "$INSTDIR\.env" env_present env_absent
  env_absent:
    DetailPrint "首次启动时会生成生产环境 .env（随机 SECRET_KEY，APP_ENV=prod）"
    Goto env_done
  env_present:
    DetailPrint "检测到已有 .env，将保留（升级不会更换 SECRET_KEY）"
  env_done:

  ; Point the copy at a data directory **outside** the program directory.
  ;
  ; This is the whole reason uninstalling is safe. With the data inside the install
  ; directory, removing the program means either deleting the database or leaving a
  ; directory behind - and the first attempt at this moved the data aside with a rename
  ; that failed and took the database with it. With the data outside, uninstalling is
  ; two independent decisions and nothing has to be moved.
  ;
  ; Both the launcher (`scripts/lib.ps1`) and the backend (`app/core/config.py`) read
  ; this one line, so they cannot disagree about where the data is.
  IfFileExists "$INSTDIR\data-dir.txt" marker_exists 0
    FileOpen $9 "$INSTDIR\data-dir.txt" w
    FileWrite $9 "$LOCALAPPDATA\${DATA_DIR_NAME}\.runtime"
    FileClose $9
    DetailPrint "数据目录：$LOCALAPPDATA\${DATA_DIR_NAME}\.runtime"
  marker_exists:

  ; ------------------------------------------------------------ shortcuts --
  ;
  ; `SetShellVarContext current` is written out rather than left to the default. The
  ; default happens to be `current`, and "happens to be" is how an installer ends up
  ; writing its Start Menu entry into `C:\ProgramData` as a non-elevated user — where
  ; `CreateShortCut` fails without saying anything, and the user gets an empty Start
  ; Menu while the file is technically somewhere.
  SetShellVarContext current

  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0 SW_SHOWNORMAL "" "${APP_NAME}"
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0 SW_SHOWNORMAL "" "${APP_NAME}"

  ; The repair entry point. Shipped on a *successful* install too, deliberately: the
  ; user who needs it is the one whose install already went wrong, and at that moment
  ; the only copy of this script is the one they cannot reach. It sits in the app's own
  ; Start Menu folder, not on the desktop, so a healthy install stays tidy.
  !insertmacro WriteRepairBat
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\修复 AI Workbench.lnk" "$INSTDIR\repair-install.bat" "" "$INSTDIR\repair-install.bat" 0 SW_SHOWNORMAL "" "修复 AI Workbench"

  ; ------------------------------------------------------------- registry --
  ; These eight values are what Windows reads to list the program. They are also the
  ; part that failed silently once, so they are verified below before this install is
  ; allowed to call itself finished.
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName"     "${APP_NAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion"  "${APP_VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher"       "${APP_PUBLISHER}"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon"     "$INSTDIR\${APP_EXE}"
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1

  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" "$0"

  ; ------------------------------------------------------- verify, then done --
  ;
  ; Everything above is a write that can fail without saying so. This is where the
  ; install finds out whether it actually worked — **before** the finish page claims it
  ; did. See the note above `FailIfMissing` for the install that made this necessary.
  !insertmacro VerifyInstall

  ${If} $R0 != ""
    ; Leave a way out before giving up. The Start Menu may be one of the things that is
    ; missing, so the repair entry goes on the desktop as well.
    !insertmacro WriteRepairBat
    CreateShortCut "$DESKTOP\修复 AI Workbench.lnk" "$INSTDIR\repair-install.bat" "" "$INSTDIR\repair-install.bat" 0 SW_SHOWNORMAL "" "修复 AI Workbench"

    ${IfNot} ${Silent}
      MessageBox MB_OK|MB_ICONSTOP \
        "安装没有完成，缺的是：$\r$\n$\r$\n$R0$\r$\n$\r$\n程序文件已经释放到：$\r$\n$INSTDIR$\r$\n$\r$\n不必重新下载 440 MB。桌面上有一个「修复 AI Workbench」，双击它就能把缺的部分补齐。$\r$\n$\r$\n如果它补不上，请把上面的这一行发给开发者。"
    ${EndIf}

    DetailPrint "安装校验失败：$R0"
    SetErrorLevel 3
    Abort
  ${EndIf}

  DetailPrint "校验通过：注册表、开始菜单、桌面、卸载程序都在"
SectionEnd

; ---------------------------------------------------------------- uninstall --
Section "Uninstall"
  !insertmacro CheckNotRunning
  !insertmacro StopEverything

  ; ------------------------------------------------------------- user data --
  ; The data lives outside the program directory, so removing the program cannot
  ; touch it. The only question is whether to remove it as well.
  ;
  ; Default to keeping it. The database holds the user's documents, their memories and
  ; everything the agent produced for them; losing that to an uninstall would be the
  ; worst thing this installer could do, so the destructive answer is the one that has
  ; to be chosen deliberately.
  ;
  ; An earlier design put the data *inside* the install directory and moved it aside
  ; here. That move failed, and the database went with the install directory. Keeping
  ; the two apart removes the whole class of problem: there is nothing to move.
  IfFileExists "$LOCALAPPDATA\${DATA_DIR_NAME}\.runtime\*.*" 0 no_data
    ; In silent mode there is nobody to ask, so the safe answer is taken: keep.
    ; A MessageBox is not suppressed by `/S`, so an unguarded one makes an unattended
    ; uninstall hang on a question no one can answer.
    IfSilent keep_data 0
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 \
      "是否同时删除你的数据？$\r$\n$\r$\n这些数据包括：资料库、记忆、档案、AI 产出物。$\r$\n$\r$\n它们位于：$\r$\n$LOCALAPPDATA\${DATA_DIR_NAME}$\r$\n$\r$\n选择「否」会保留（默认）。$\r$\n选择「是」将永久删除，无法恢复。" \
      IDNO keep_data
      DetailPrint "用户选择删除数据"
      RMDir /r "$LOCALAPPDATA\${DATA_DIR_NAME}"
      Goto no_data
    keep_data:
      DetailPrint "数据保留在 $LOCALAPPDATA\${DATA_DIR_NAME}"
  no_data:

  Delete "$DESKTOP\${APP_NAME}.lnk"
  Delete "$DESKTOP\修复 AI Workbench.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\修复 AI Workbench.lnk"
  RMDir "$SMPROGRAMS\${APP_NAME}"

  RMDir /r "$INSTDIR\backend"
  RMDir /r "$INSTDIR\frontend"
  RMDir /r "$INSTDIR\scripts"
  RMDir /r "$INSTDIR\worker"
  RMDir /r "$INSTDIR\vendor"
  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\.env"
  Delete "$INSTDIR\.env.example"
  Delete "$INSTDIR\data-dir.txt"
  Delete "$INSTDIR\repair-install.bat"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"

  DeleteRegKey HKCU "${UNINST_KEY}"

  ; Tell the user where their data is, rather than leaving them to guess.
  IfFileExists "$LOCALAPPDATA\${DATA_DIR_NAME}\.runtime\*.*" 0 done
    IfSilent done 0
    MessageBox MB_OK|MB_ICONINFORMATION \
      "卸载完成。$\r$\n$\r$\n你的数据已保留在：$\r$\n$LOCALAPPDATA\${DATA_DIR_NAME}$\r$\n$\r$\n重新安装时会询问是否恢复。"
  done:
SectionEnd

; ----------------------------------------------------------------- callbacks --
Function .onInit
  ; Nothing to prepare.
  ;
  ; An earlier version asked here whether to restore a data directory that a previous
  ; uninstall had moved aside. That whole mechanism is gone: the data lives outside the
  ; install directory and a reinstall simply finds it, so there is nothing to ask and
  ; nothing to move. `data-dir.txt` is rewritten on every install to point at it.
FunctionEnd

; A cancelled install is a half-install, and it looks exactly like a finished one.
;
; The payload is 440 MB and is written first; the shortcuts and the registry entries —
; the things that make Windows aware the program exists — come last. Clicking Cancel in
; the last seconds therefore leaves a directory full of working files, no app-list entry
; and no Start Menu entry: **「装得上、看不见、删不掉」**, which is precisely what the user
; reported. NSIS aborts at the next instruction, so `WriteUninstaller` can have run while
; the eight `WriteRegStr` calls have not.
;
; The installer cannot stop the abort, but it can refuse to leave the user with no way
; out: a repair entry point on the desktop, and a sentence saying what happened.
;
; MUI2 owns `.onUserAbort` itself — defining that function here fails to compile with
; "Function named .onUserAbort already exists". `MUI_CUSTOMFUNCTION_ABORT` (defined up
; with the pages, because MUI2 reads it while expanding the language macros) is the hook
; it provides for exactly this.
Function OnInstallAborted
  ${If} ${FileExists} "$INSTDIR\${APP_EXE}"
    !insertmacro WriteRepairBat
    CreateShortCut "$DESKTOP\修复 AI Workbench.lnk" "$INSTDIR\repair-install.bat" "" "$INSTDIR\repair-install.bat" 0 SW_SHOWNORMAL "" "修复 AI Workbench"
    ${IfNot} ${Silent}
      MessageBox MB_OK|MB_ICONEXCLAMATION \
        "安装被取消了，但程序文件已经释放了一部分。$\r$\n$\r$\n这时候的软件是「装了一半」：文件在，但 Windows 可能认不出它 —— 设置里找不到、开始菜单里没有。$\r$\n$\r$\n桌面上放了一个「修复 AI Workbench」，双击它就能把缺的部分补齐；或者重新运行安装程序。"
    ${EndIf}
  ${EndIf}
FunctionEnd
