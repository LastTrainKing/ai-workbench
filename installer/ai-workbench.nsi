; ============================================================================
;  AI Workbench - installer
;
;  Built with NSIS directly rather than through Tauri's bundler.
;
;  Reason: the payload is 8,669 files / 440 MB, and what this installer has to do
;  is not what a bundler does. It must refuse to install over a running copy,
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

; ------------------------------------------------------------------- config --
!define APP_NAME        "AI Workbench"
!define APP_EXE         "AI Workbench.exe"
!define APP_PUBLISHER   "AI Workbench"
!define APP_VERSION     "2.1.0"
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

VIProductVersion "2.1.0.0"
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
    ; `IfSilent +2 0` skips the message box in silent mode but still aborts. A
    ; MessageBox is not suppressed by `/S`, so an unguarded one hangs an
    ; unattended uninstall forever - which is exactly what happened the first time
    ; this was tested.
    IfSilent +2 0
    MessageBox MB_OK|MB_ICONSTOP "${APP_NAME} 正在运行。$\r$\n$\r$\n请先关闭它，再重新运行安装程序。"
    Abort
  ${EndIf}
!macroend

!macro StopEverything
  ; Stop the stack the way the product does, then make sure nothing survived.
  ;
  ; Two steps on purpose: `stop.bat` is the graceful path (it stops PostgreSQL
  ; with a checkpoint rather than a kill), and `taskkill` is the guarantee. An
  ; uninstaller that only does the graceful half leaves locked files behind when
  ; the graceful half fails, and the user sees "cannot delete".
  IfFileExists "$INSTDIR\scripts\stop.bat" 0 +3
    nsExec::ExecToLog '"$INSTDIR\scripts\stop.bat"'
    Pop $0
  Sleep 3000
  nsExec::ExecToLog 'taskkill /F /IM "${APP_EXE}" /T'
  Pop $0
  nsExec::ExecToLog 'taskkill /F /IM "postgres.exe" /T'
  Pop $0
  nsExec::ExecToLog 'taskkill /F /IM "redis-server.exe" /T'
  Pop $0
  nsExec::ExecToLog 'taskkill /F /IM "qdrant.exe" /T'
  Pop $0
  Sleep 2000
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

  ; Bring back the data a previous uninstall set aside, if the user asked for it.
  ; Done after extraction so `.runtime` lands on top of a complete tree, never the
  ; other way round.
  ${If} $R0 == "1"
    IfFileExists "$LOCALAPPDATA\${DATA_DIR_NAME}\.runtime\*.*" 0 restore_skipped
      DetailPrint "正在恢复上次保留的数据..."
      nsExec::ExecToLog 'cmd /c move /Y "$LOCALAPPDATA\${DATA_DIR_NAME}\.runtime" "$INSTDIR\.runtime"'
      Pop $0
      RMDir "$LOCALAPPDATA\${DATA_DIR_NAME}"
    restore_skipped:
  ${EndIf}

  ; The environment file is written from the example, never from the developer's
  ; own `.env` - that file holds an API key, and shipping it would put a real
  ; credential inside the installer.
  IfFileExists "$INSTDIR\.env" env_exists 0
    IfFileExists "$INSTDIR\.env.example" 0 env_exists
      CopyFiles /SILENT "$INSTDIR\.env.example" "$INSTDIR\.env"
      DetailPrint "已生成 .env（不含任何密钥）"
  env_exists:

  ; ------------------------------------------------------------ shortcuts --
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0 SW_SHOWNORMAL "" "${APP_NAME}"
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0 SW_SHOWNORMAL "" "${APP_NAME}"
  DetailPrint "已创建桌面与开始菜单快捷方式（指向 exe 本身）"

  ; ------------------------------------------------------------- registry --
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
SectionEnd

; ---------------------------------------------------------------- uninstall --
Section "Uninstall"
  !insertmacro CheckNotRunning
  !insertmacro StopEverything

  ; ------------------------------------------------------------- user data --
  ; Ask, and default to keeping it.
  ;
  ; The database holds the user's documents, their memories and everything the
  ; agent produced for them. Losing that to an uninstall would be the worst thing
  ; this installer could do, so the destructive answer is the one the user has to
  ; choose deliberately.
  IfFileExists "$INSTDIR\.runtime\*.*" 0 no_data
    ; In silent mode there is nobody to ask, so the safe answer is taken: keep.
    ; A MessageBox is not suppressed by `/S`, so an unguarded one makes an
    ; unattended uninstall hang on a question no one can answer.
    IfSilent keep_data 0
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 \
      "是否同时删除你的数据？$\r$\n$\r$\n这些数据包括：资料库、记忆、档案、AI 产出物。$\r$\n$\r$\n选择「否」会把它们移动到：$\r$\n$LOCALAPPDATA\${DATA_DIR_NAME}$\r$\n$\r$\n选择「是」将永久删除，无法恢复。" \
      IDNO keep_data
      DetailPrint "用户选择删除数据"
      RMDir /r "$INSTDIR\.runtime"
      Goto no_data
    keep_data:
      ; Moved with NSIS's own `Rename`, not `cmd /c move`.
      ;
      ; The shell version failed silently: the uninstaller then removed the
      ; install directory and the user's database went with it. A `nsExec` call
      ; gives no result unless you go looking for one, and this is the one place
      ; in the installer where a silent failure destroys something irreplaceable.
      ; `Rename` sets the error flag, which is checked.
      DetailPrint "保留数据：移动到 $LOCALAPPDATA\${DATA_DIR_NAME}"
      CreateDirectory "$LOCALAPPDATA\${DATA_DIR_NAME}"
      RMDir /r "$LOCALAPPDATA\${DATA_DIR_NAME}\.runtime"
      ClearErrors
      Rename "$INSTDIR\.runtime" "$LOCALAPPDATA\${DATA_DIR_NAME}\.runtime"
      IfErrors 0 move_ok
        ; Do not proceed to delete anything. Leaving the data where it is and
        ; saying so is strictly better than reporting success over a lost
        ; database.
        MessageBox MB_OK|MB_ICONEXCLAMATION \
          "无法移动数据目录，已保留在原处：$\r$\n$INSTDIR\.runtime$\r$\n$\r$\n卸载已停止，请关闭所有相关程序后重试。"
        Abort
      move_ok:
  no_data:

  Delete "$DESKTOP\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  RMDir "$SMPROGRAMS\${APP_NAME}"

  RMDir /r "$INSTDIR\backend"
  RMDir /r "$INSTDIR\frontend"
  RMDir /r "$INSTDIR\scripts"
  RMDir /r "$INSTDIR\worker"
  RMDir /r "$INSTDIR\vendor"
  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\.env"
  Delete "$INSTDIR\.env.example"
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
  ; A stale data directory from a previous uninstall: offer to bring it back.
  ; Decided before the directory page so the choice is made with the path visible,
  ; and applied after extraction so it lands on top of a complete tree.
  StrCpy $R0 "0"
  IfFileExists "$LOCALAPPDATA\${DATA_DIR_NAME}\.runtime\*.*" 0 no_stale
    IfSilent no_stale 0
    MessageBox MB_YESNO|MB_ICONQUESTION \
      "发现上次卸载时保留的数据：$\r$\n$LOCALAPPDATA\${DATA_DIR_NAME}$\r$\n$\r$\n是否恢复到新安装中？" \
      IDNO no_stale
      StrCpy $R0 "1"
  no_stale:
FunctionEnd
