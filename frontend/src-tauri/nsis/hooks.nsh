; Tauri NSIS 安装钩子（由 tauri.conf.json 的 bundle.windows.nsis.installerHooks 引入）。
;
; 这里做两件事：
;
; ① 杀后端侧车。模板自带的 CheckIfAppIsRunning 只按 MAINBINARYNAME 关进程，也就是
;    只认识 limo.exe，不认识侧车 LiMoBackend.exe。覆盖安装时 limo.exe 被关掉，侧车
;    继续活着并占着 127.0.0.1:7413，新装好的应用启动时后端起不来 —— 用户看到的是
;    「启动失败」屏，而安装器当时退出码是 0（2026-11-10 实测复现）。
;
;    只在 PREINSTALL 杀侧车：limo.exe 交给模板自己的检查处理，交互模式下它会先问
;    用户「是否关闭正在运行的应用」，比直接杀掉礼貌。卸载时两个都杀 —— 卸载完还留
;    一个占着端口的进程，同样会让人以为卸载没干净。
;
; ② 移除另一份以 MSI 装的 LiMo。两套安装器互不可见对方的卸载登记：NSIS 登记
;    HKLM\...\Uninstall\LiMo，MSI 登记一条 {ProductCode} 键，落点在 %LOCALAPPDATA%\LiMo。
;    交叉安装的结果是两份并存 + 两个开始菜单图标（2026-11-12 实测双向复现）。
;
; ⚠️ **模板里本来就有这段逻辑**（installer.nsi 的 PageReinstall，Tauri bundler #6859），
;    但它写在自定义页面函数里。`/S` 静默安装时 NSIS 不执行任何页面函数 —— 页面被整体
;    跳过，$WixMode 停在默认值 0，后面的 PageLeaveReinstall 也就不会去卸 WiX 那份。
;    手动静默安装、以及某些走 /S 的部署工具，都落在这条缝里。这里补的就是它：
;    不看当前是不是静默，PREINSTALL 一律自查一遍。
;
;    交互与 /P 被动模式下模板已经卸过一次，这里再查会查不到（注册项已删），
;    多打一行 DetailPrint 而已，不会重复执行 msiexec。
;
; ⚠️ 只覆盖 MSI → NSIS 一个方向。反方向没有可挂的钩子（Tauri 不开放 WiX 侧），
;    只能靠下载页写明「二选一」与「换格式先卸载旧的」。Tauri 官方也是这个结论
;    （plugins-workspace#1449：updater 必须与安装时同格式，MSI 装、NSIS perMachine
;    更新是唯一走得通的方向）。
;
; 用到的寄存器：$R5-$R9。PREINSTALL 处在 Section Install 的最开头，模板此时还没
; 动过这几个（它自己在别处用 $R0-$R4），不会撞。

!macro NSIS_HOOK_PREINSTALL
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM LiMoBackend.exe'
  !insertmacro LiMoRemoveForeignMsi
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM LiMoBackend.exe'
!macroend

; 在 root\subkey 下找 MSI 的卸载登记，找到就把产品码（子键名，形如 {GUID}）写进 $R8。
;
; 判据：子键名是 {GUID} 形态，且 DisplayName == ${PRODUCTNAME}、Publisher == ${MANUFACTURER}。
; 键名那条是必需的：本产品自己的 NSIS 卸载项 DisplayName/Publisher 也满足后两条
; （2026-11-12 实测两者都是 "LiMo"/"limo"），只有键名能把它们分开。
;
; ⚠️ EnumRegKey 从 index 0 起枚举子键（模板与官方示例都是 StrCpy $0 0 起手）。
;    从 1 开始会静默跳过第一个子键 —— 不报错，只是可能漏掉唯一那条登记。
!macro LiMoFindMsiUnder ROOT SUBKEY
  ${If} $R8 == ""
    StrCpy $R9 0
    ${Do}
      EnumRegKey $R7 ${ROOT} "${SUBKEY}" $R9
      ${If} $R7 == ""
        ${ExitDo}
      ${EndIf}
      IntOp $R9 $R9 + 1
      ${If} $R9 > 500
        ${ExitDo}
      ${EndIf}
      ; MSI 的产品码子键一律是 {GUID} 形态；本产品自己的 NSIS 卸载项键名是 LiMo。
      ; 用键名而不是 UninstallString 里找 "msiexec"：${StrLoc} 内部会用 $R1/$R2 当
      ; 临时变量，把结果变量传成这几个就会静默归零（2026-11-12 实测：命中 MSI 项却
      ; 返回 0，于是循环继续、最后选中 NSIS 自己那条，执行 msiexec /x LiMo 必然失败）。
      StrCpy $R6 $R7 1
      ${If} $R6 == "{"
        ReadRegStr $R5 ${ROOT} "${SUBKEY}\$R7" "DisplayName"
        ${If} $R5 == "${PRODUCTNAME}"
          ReadRegStr $R5 ${ROOT} "${SUBKEY}\$R7" "Publisher"
          ${If} $R5 == "${MANUFACTURER}"
            StrCpy $R8 $R7
            ${ExitDo}
          ${EndIf}
        ${EndIf}
      ${EndIf}
    ${Loop}
  ${EndIf}
!macroend

; 三处都查：模板只看了 HKLM 的 64 位视图。MSI 的 perUser 安装登记在 HKCU，
; 32 位视图的键可能落在 WOW6432Node 下 —— 只查一处时，恰好装在那两处的那种
; 用户永远检测不到，表现为「更新之后变成两份」。
!macro LiMoRemoveForeignMsi
  StrCpy $R8 ""
  ; ⚠️ 必须切到 64 位注册表视图。安装器是 32 位进程，读 HKLM\SOFTWARE 时会被
  ;    WOW64 重定向到 HKLM\SOFTWARE\WOW6432Node —— 而 MSI 的卸载项登记在 64 位视图，
  ;    不切视图就永远读不到它。模板 PageReinstall 里那段 WiX 检测也没切（整份
  ;    installer.nsi 里没有一处 SetRegView），这是它在真机上从未生效的第二个原因，
  ;    与「/S 不执行页面函数」相互独立，两条都得堵。
  ${If} ${RunningX64}
    SetRegView 64
  ${EndIf}
  !insertmacro LiMoFindMsiUnder HKLM "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
  !insertmacro LiMoFindMsiUnder HKLM "SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
  !insertmacro LiMoFindMsiUnder HKCU "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
  ${If} ${RunningX64}
    ; 恢复默认：后面的安装步骤（写 NSIS 卸载项等）按原来的 32 位视图走，
    ; 免得把本产品的登记搬到另一个视图里去。
    SetRegView 32
  ${EndIf}

  ${If} $R8 == ""
    DetailPrint "[LiMo] 未发现另一份 MSI 安装"
  ${Else}
    DetailPrint "[LiMo] 发现另一份 MSI 安装：$R8，先移除"
    ; MSI 那份的主程序可能还在跑。模板的 CheckIfAppIsRunning 排在本钩子**之后**，
    ; 等它来处理就晚了 —— msiexec 静默卸载遇到被占用的文件会失败在半路。
    nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM limo.exe'
    Sleep 1000
    nsExec::Exec 'msiexec.exe /x $R8 /qn /norestart'
    Pop $R7
    ; 0 成功 / 1605 产品不存在（已被别处卸掉）/ 3010 已卸载但需重启 —— 三者都不算失败。
    ${If} $R7 == 0
      DetailPrint "[LiMo] 另一份 MSI 安装已移除"
    ${ElseIf} $R7 == 1605
      DetailPrint "[LiMo] 另一份 MSI 安装已不存在（1605）"
    ${ElseIf} $R7 == 3010
      DetailPrint "[LiMo] 另一份 MSI 安装已移除，需要重启（3010）"
    ${Else}
      ; 不中断安装：新版本至少能装上。用户可见的后果是控制面板留下两条 LiMo
      ; 与两个同名图标，安装完成页没法提示，只能如实写进安装日志。
      DetailPrint "[LiMo] 另一份 MSI 安装移除失败（返回码 $R7），它仍在，请安装后手动卸载"
    ${EndIf}
  ${EndIf}
!macroend
