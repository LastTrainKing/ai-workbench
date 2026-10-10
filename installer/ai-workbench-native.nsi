; ============================================================================
;  AI Workbench - installer (native build)
;
;  Installs the PySide6/Qt desktop application. There is no WebView2, no Qt
;  WebEngine, no browser and no React bundle: every control is a Qt control, and
;  the application calls the service layer in its own process.
;
;  WHY A SEPARATE SCRIPT FROM `ai-workbench.nsi`
;
;  The two install *different things*. The previous one shipped a Tauri shell
;  plus `backend/`, `frontend/dist/`, `worker/`, the whole `scripts/` tree and a
;  323 MB bundled Python interpreter, because its shell started a FastAPI server
;  through `scripts/start-desktop.bat`. The native build ships one PyInstaller
;  tree that contains its own interpreter, and starts only the three database
;  engines. The file lists have nothing in common; the guarantees do, and those
;  are carried over deliberately rather than reinvented:
;
;    * per-user install, no UAC;
;    * refuse to install over a running copy;
;    * stop only *this product's* processes, by pid, never `taskkill /IM`;
;    * the user's data lives outside the program directory, so uninstalling is
;      two independent decisions and nothing has to be moved;
;    * verify the things that make an install *real* (registry, shortcuts,
;      uninstaller) before claiming it finished.
;
;  THE SAME INSTALL DIRECTORY, ON PURPOSE
;
;  `$LOCALAPPDATA\AI Workbench` and the same uninstall registry key as the
;  previous build. Installing this over an existing 2.2.0 install is an
;  **upgrade**, not a second product: one entry in 设置 → 应用, one directory,
;  one data directory. The install section therefore removes the previous
;  shell's files explicitly - `AI Workbench.exe`, `backend\`, `frontend\`,
;  `worker\`, `vendor\runtime\python\` - instead of leaving 800 MB of a program
;  that is no longer there.
;
;  WHAT IS NOT DELETED: `.env`
;
;  It holds this installation's `SECRET_KEY`. Removing it on an upgrade would
;  rotate the key, sign the user out, and can strand data encrypted under the
;  old value. It is kept, exactly as the previous installer kept it.
;
;  WHERE THE USER'S DATA GOES
;
;  `AI_WORKBENCH_DATA_DIR` if the machine has one set, otherwise
;  `%LOCALAPPDATA%\AI Workbench Data\.runtime`. The override is the environment
;  variable and not a command-line switch because the application and
;  `scripts/lib.ps1` already read that variable - one answer everywhere, and a
;  path with spaces needs no re-parsing. The chosen path is written to
;  `data-dir.txt` beside the executable, which is where `app/core/config.py`
;  looks for it.
; ============================================================================

Unicode true
!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "TextFunc.nsh"

; ------------------------------------------------------------------- config --
!define APP_NAME        "AI Workbench"
!define APP_EXE         "AI-Workbench-Native.exe"
!define APP_PUBLISHER   "AI Workbench"
!define APP_VERSION     "2.2.0"
!define UNINST_KEY      "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
!define DATA_DIR_NAME   "AI Workbench Data"
!define PAYLOAD         "..\build\native-installer-payload"

;: The previous shell's launcher. Refusing to install over it matters as much as
;: refusing over ourselves: the install section deletes it, and deleting a running
;: executable fails halfway.
!define OLD_APP_EXE     "AI Workbench.exe"

Name "${APP_NAME}"
OutFile "..\build\installer\AI-Workbench-Native-Setup.exe"
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
; The installer's and uninstaller's own icons, and the icon shown in the app list.
; Paths are relative to this file, like `PAYLOAD` above.
;
; The shortcuts are *not* set here: they point at the installed executable with index 0,
; so they show whatever that executable embeds. Setting the icon in the PyInstaller spec
; is what fixes the desktop shortcut; this line fixes the setup program and the uninstaller.
!define MUI_ICON   "..\assets\ai-workbench.ico"
!define MUI_UNICON "..\assets\ai-workbench.ico"

!define MUI_WELCOMEPAGE_TITLE "安装 ${APP_NAME}"
!define MUI_WELCOMEPAGE_TEXT "这是一个运行在你电脑上的私人 AI 工作台。$\r$\n$\r$\n原生界面，自带数据库引擎，所有数据都留在本机。$\r$\n$\r$\n安装不需要管理员权限。"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES

; Defined before MUI_LANGUAGE: MUI2 expands MUI_FUNCTION_ABORTWARNING while processing
; the language macros, and a `!define` that comes later is not there yet - the build
; then succeeds with "warning 6010: install function OnInstallAborted not referenced",
; which is the compiler saying the safety net was thrown away.
!define MUI_CUSTOMFUNCTION_ABORT OnInstallAborted

!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "立即启动 ${APP_NAME}"
!define MUI_FINISHPAGE_TEXT "安装完成。$\r$\n$\r$\n第一次启动会初始化数据库，需要一两分钟，界面上会显示进度。$\r$\n$\r$\n数据目录会在下面这一步写进 data-dir.txt。"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

; ------------------------------------------------------------------ helpers --
!macro CheckNotRunning
  ; Refuse to install or uninstall over a running copy.
  ;
  ; The check reads an *exit code* rather than searching text. `tasklist` exits 0 whether
  ; or not it found anything, and its "no tasks" line is localized, so matching on output
  ; would be matching on the machine's language.
  ;
  ; Both names are checked. Ours, because the install would overwrite a running
  ; executable; the previous shell's, because the install *deletes* that file and a
  ; half-deleted program directory is worse than a refusal.
  nsExec::ExecToStack 'powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-Process -Name ''AI-Workbench-Native'',''AI Workbench'' -ErrorAction SilentlyContinue) { exit 7 } else { exit 0 }"'
  Pop $0
  Pop $1
  ${If} $0 == "7"
    ; IfSilent jump_if_silent jump_if_not_silent. Written out rather than as an offset:
    ; an earlier version used `IfSilent +2 0` and +2 counts from the next instruction, so
    ; it jumped over the Abort as well and a silent run skipped the guard entirely.
    IfSilent 0 +2
    MessageBox MB_OK|MB_ICONSTOP "${APP_NAME} 正在运行。$\r$\n$\r$\n请先关闭它，再重新运行安装程序。"
    Abort
  ${EndIf}
!macroend

!macro StopEverything
  ; Stop the stack the way the product does, then make sure nothing survived.
  ;
  ; `stop-by-pid.ps1` is the scoped sweep: every process this product starts is recorded
  ; in `<data>\.runtime\pids\<name>.pid`, PostgreSQL writes its own `postmaster.pid`, and
  ; the script refuses any pid whose executable does not live under the product's own
  ; directory. `taskkill /F /IM postgres.exe` would take out the user's own database,
  ; which is a real loss for the audience this is aimed at.
  ;
  ; The runtime root comes from the marker the installer wrote, so a custom data directory
  ; is swept too; a zero-byte marker falls back to the default rather than to the drive
  ; root.
  ReadEnvStr $1 "LOCALAPPDATA"
  StrCpy $2 "$1\${DATA_DIR_NAME}\.runtime"
  IfFileExists "$INSTDIR\data-dir.txt" 0 stop_pid_sweep
    FileOpen $3 "$INSTDIR\data-dir.txt" r
    FileRead $3 $2
    FileClose $3
    ${TrimNewLines} $2 $2
    ${If} $2 == ""
      StrCpy $2 "$1\${DATA_DIR_NAME}\.runtime"
    ${EndIf}
  stop_pid_sweep:

  ; Our own binary by name is fine - the name belongs to this product and to nothing else.
  nsExec::ExecToLog 'taskkill /F /IM "${APP_EXE}" /T'
  Pop $0

  IfFileExists "$INSTDIR\scripts\stop-by-pid.ps1" 0 stop_pid_done
    ; `-ProductRoot $INSTDIR` is passed explicitly. The script also derives it from its own
    ; location, and both agree here; passing it makes the intent visible and does not
    ; depend on the derivation being right.
    nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\scripts\stop-by-pid.ps1" -RuntimeRoot "$2" -ProductRoot "$INSTDIR"'
    Pop $0
  stop_pid_done:
  Sleep 2000
!macroend

; ------------------------------------------------------- install verification --
;
; 装得上、看不见、删不掉 is the failure these macros exist for: a real install once ended
; with `Uninstall.exe` written, none of the eight registry values set, and no Start Menu
; entry. The software ran; it appeared in no list; there was no uninstall entry anywhere
; the user would look. The installer reported success, because that is what NSIS does -
; `WriteRegStr` sets an error flag that nothing was reading, and the progress bar reaches
; the end either way.
;
; **铺完文件不等于装好。**

; Sets $R0 to the description of the first missing thing, or leaves it empty.
;
; `${AndIfNot} ${FileExists}` rather than a string comparison: LogicLib's expression parser
; splits on quotes, so a value that *contains* a quote - which `UninstallString` does -
; arrives as five parameters to a four-parameter macro and makensis stops.
!macro FailIfMissing PATH DESC
  ${If} $R0 == ""
  ${AndIfNot} ${FileExists} "${PATH}"
    StrCpy $R0 "${DESC}"
  ${EndIf}
!macroend

; Leaves the answer in $R0: empty means the install is complete.
;
; Every comparison is register-to-register. `${If} $R1 != "some path"` works right up until
; the path contains a quote, and the one value that matters most - `UninstallString` - is
; `"C:\...\Uninstall.exe"`, which contains two.
!macro VerifyInstall
  StrCpy $R0 ""

  !insertmacro FailIfMissing "$INSTDIR\${APP_EXE}" "程序文件 $INSTDIR\${APP_EXE}"
  !insertmacro FailIfMissing "$INSTDIR\Uninstall.exe" "卸载程序 $INSTDIR\Uninstall.exe"
  !insertmacro FailIfMissing "$INSTDIR\data-dir.txt" "数据目录标记 data-dir.txt"
  !insertmacro FailIfMissing "$INSTDIR\vendor\runtime\pgsql\pgsql\bin\postgres.exe" "内置 PostgreSQL"
  !insertmacro FailIfMissing "$INSTDIR\scripts\infra-start.ps1" "服务启动脚本"
  !insertmacro FailIfMissing "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "开始菜单快捷方式"
  !insertmacro FailIfMissing "$DESKTOP\${APP_NAME}.lnk" "桌面快捷方式"

  ; The three values that decide whether the program shows up in 设置 → 应用 and whether
  ; the 「卸载」 button there works.
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
  ; `scripts/verify_native_installer.py` uses to find the application. A wrong one is how a
  ; reinstall lands somewhere unexpected.
  ReadRegStr $R1 HKCU "${UNINST_KEY}" "InstallLocation"
  StrCpy $R2 "$INSTDIR"
  ${If} $R0 == ""
  ${AndIf} $R1 != $R2
    StrCpy $R0 "注册表 InstallLocation"
  ${EndIf}
!macroend

; ------------------------------------------------------------------ install --
Section "Install"
  SectionIn RO
  !insertmacro CheckNotRunning

  ; A reinstall must not silently discard the existing database. The payload contains no
  ; `.runtime`, so extracting over an existing install cannot overwrite user data - but
  ; saying so explicitly is worth more than relying on a property of the file list.
  IfFileExists "$INSTDIR\.runtime\*.*" 0 no_existing_data
    DetailPrint "检测到程序目录下有 .runtime（不应存在），将保留不动"
  no_existing_data:

  ; -------------------------------------------------- remove the old shell --
  ;
  ; This is an upgrade of the same product, so the previous build's files go. They are
  ; named, not globbed: `RMDir /r "$INSTDIR"` would take the user's `.env` with them.
  ;
  ; `vendor\runtime\python` is 323 MB of interpreter the previous shell needed to start
  ; FastAPI. Nothing in the native build starts it, and leaving it would mean the upgrade
  ; keeps 323 MB of a program that is no longer installed.
  IfFileExists "$INSTDIR\${OLD_APP_EXE}" 0 no_old_shell
    DetailPrint "检测到旧版外壳，正在移除（保留 .env 与数据）"
  no_old_shell:
  Delete "$INSTDIR\${OLD_APP_EXE}"
  RMDir /r "$INSTDIR\backend"
  RMDir /r "$INSTDIR\frontend"
  RMDir /r "$INSTDIR\worker"
  RMDir /r "$INSTDIR\vendor\runtime\python"
  Delete "$INSTDIR\vendor\runtime\python-nuget.zip"
  ; The old script tree is replaced wholesale rather than merged: the two builds ship
  ; different sets, and a stale `start-desktop.bat` in a native install is a second,
  ; contradictory description of how the product starts.
  RMDir /r "$INSTDIR\scripts"
  Delete "$INSTDIR\repair-install.bat"
  Delete "$INSTDIR\.env.example"
  ; A stray file an earlier launcher wrote here. `RMDir` without `/r` on purpose: the design
  ; guarantees this directory holds no user data, so if it turns out to be non-empty
  ; something is wrong and deleting it recursively would be the wrong guess.
  Delete "$INSTDIR\.runtime\qdrant-api-key.txt"
  RMDir "$INSTDIR\.runtime"

  SetOutPath "$INSTDIR"
  DetailPrint "正在释放程序文件（约 500 MB）..."
  File /r "${PAYLOAD}\*.*"

  ; --------------------------------------------------------- data directory --
  ;
  ; Point the copy at a data directory **outside** the program directory. This is the whole
  ; reason uninstalling is safe: with the data inside, removing the program means either
  ; deleting the database or leaving a directory behind, and the first attempt at this
  ; moved the data aside with a rename that failed and took the database with it.
  ;
  ; Rewritten on every install, including an upgrade, so the marker always describes this
  ; installation. An existing file is only rewritten with the same value unless the
  ; environment changed - the path is not derived from anything that moves.
  ReadEnvStr $9 "AI_WORKBENCH_DATA_DIR"
  ${If} $9 == ""
    ReadEnvStr $0 "LOCALAPPDATA"
    StrCpy $9 "$0\${DATA_DIR_NAME}\.runtime"
  ${EndIf}
  ClearErrors
  FileOpen $8 "$INSTDIR\data-dir.txt" w
  IfErrors data_dir_failed
    FileWrite $8 "$9"
    FileClose $8
    DetailPrint "数据目录：$9"
    Goto data_dir_done
  data_dir_failed:
    DetailPrint "无法写入 data-dir.txt"
    SetErrorLevel 4
    Abort
  data_dir_done:

  ; ------------------------------------------------------------ shortcuts --
  ;
  ; `SetShellVarContext current` is written out rather than left to the default. The default
  ; happens to be `current`, and "happens to be" is how an installer ends up writing its
  ; Start Menu entry into `C:\ProgramData` as a non-elevated user - where `CreateShortCut`
  ; fails without saying anything.
  SetShellVarContext current

  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0 SW_SHOWNORMAL "" "${APP_NAME}"
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0 SW_SHOWNORMAL "" "${APP_NAME}"

  ; ------------------------------------------------------------- registry --
  ; These values are what Windows reads to list the program. They are also the part that
  ; failed silently once, so they are verified below before this install is allowed to call
  ; itself finished.
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
  ; Everything above is a write that can fail without saying so. This is where the install
  ; finds out whether it actually worked - **before** the finish page claims it did.
  !insertmacro VerifyInstall

  ${If} $R0 != ""
    ${IfNot} ${Silent}
      MessageBox MB_OK|MB_ICONSTOP \
        "安装没有完成，缺的是：$\r$\n$\r$\n$R0$\r$\n$\r$\n程序文件已经释放到：$\r$\n$INSTDIR$\r$\n$\r$\n请把上面这一行发给开发者，或重新运行安装程序。"
    ${EndIf}
    DetailPrint "安装校验失败：$R0"
    SetErrorLevel 3
    Abort
  ${EndIf}

  DetailPrint "校验通过：注册表、开始菜单、桌面、卸载程序、数据目录标记都在"
SectionEnd

; ---------------------------------------------------------------- uninstall --
Section "Uninstall"
  !insertmacro CheckNotRunning
  !insertmacro StopEverything

  ; ------------------------------------------------------------- user data --
  ; The data lives outside the program directory, so removing the program cannot touch it.
  ; The only question is whether to remove it as well.
  ;
  ; Default to keeping it. The database holds the user's documents, their memories and
  ; everything the agent produced for them; losing that to an uninstall would be the worst
  ; thing this installer could do, so the destructive answer is the one that has to be
  ; chosen deliberately.
  IfFileExists "$LOCALAPPDATA\${DATA_DIR_NAME}\*.*" 0 no_data
    ; In silent mode there is nobody to ask, so the safe answer is taken: keep. A MessageBox
    ; is not suppressed by `/S`, so an unguarded one makes an unattended uninstall hang on a
    ; question no one can answer.
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
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  RMDir "$SMPROGRAMS\${APP_NAME}"

  ; The application tree. `_internal` is where PyInstaller puts the modules, the Qt
  ; libraries and the QML sources; `vendor` is the three engines; `scripts` is what starts
  ; them. Named rather than `RMDir /r "$INSTDIR"`, so a file the installer did not create -
  ; a user's `.env`, or anything else they put here - is not silently removed.
  RMDir /r "$INSTDIR\_internal"
  RMDir /r "$INSTDIR\vendor"
  RMDir /r "$INSTDIR\scripts"
  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\data-dir.txt"
  Delete "$INSTDIR\VERSION"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\Uninstall.exe"
  ; `.env` is deliberately **not** deleted. It holds this installation's SECRET_KEY, and a
  ; user who reinstalls is entitled to their data still being readable.
  RMDir "$INSTDIR"

  DeleteRegKey HKCU "${UNINST_KEY}"

  ; Tell the user where their data is, rather than leaving them to guess.
  IfFileExists "$LOCALAPPDATA\${DATA_DIR_NAME}\*.*" 0 done
    IfSilent done 0
    MessageBox MB_OK|MB_ICONINFORMATION \
      "卸载完成。$\r$\n$\r$\n你的数据已保留在：$\r$\n$LOCALAPPDATA\${DATA_DIR_NAME}$\r$\n$\r$\n重新安装后会继续使用它。"
  done:
SectionEnd

; ----------------------------------------------------------------- callbacks --
Function .onInit
  ; Nothing to prepare. There is no data to move aside on a reinstall: the data lives
  ; outside the program directory and the marker is rewritten on every install.
FunctionEnd

; A cancelled install is a half-install, and it looks exactly like a finished one.
;
; The payload is written first; the shortcuts and the registry entries - the things that
; make Windows aware the program exists - come last. Clicking Cancel in the last seconds
; therefore leaves a directory full of working files, no app-list entry and no Start Menu
; entry: 装得上、看不见、删不掉. NSIS aborts at the next instruction, so `WriteUninstaller`
; can have run while the registry writes have not.
;
; The installer cannot stop the abort, but it can refuse to leave the user with no way out:
; say what happened, and say that re-running fixes it.
;
; MUI2 owns `.onUserAbort` itself - defining that function here fails to compile with
; "Function named .onUserAbort already exists". `MUI_CUSTOMFUNCTION_ABORT` is the hook.
Function OnInstallAborted
  ${If} ${FileExists} "$INSTDIR\${APP_EXE}"
    ${IfNot} ${Silent}
      MessageBox MB_OK|MB_ICONEXCLAMATION \
        "安装被取消了，但程序文件已经释放了一部分。$\r$\n$\r$\n这时候的软件是「装了一半」：文件在，但 Windows 可能认不出它 —— 设置里找不到、开始菜单里没有。$\r$\n$\r$\n重新运行安装程序即可补齐。"
    ${EndIf}
  ${EndIf}
FunctionEnd
