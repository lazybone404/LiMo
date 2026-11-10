; Tauri NSIS 安装钩子（由 tauri.conf.json 的 bundle.windows.nsis.installerHooks 引入）。
;
; 为什么需要它：模板自带的 CheckIfAppIsRunning 只按 MAINBINARYNAME 关进程，也就是
; 只认识 limo.exe，不认识侧车 LiMoBackend.exe。覆盖安装时 limo.exe 被关掉，侧车
; 继续活着并占着 127.0.0.1:7413，新装好的应用启动时后端起不来 —— 用户看到的是
; 「启动失败」屏，而安装器当时退出码是 0（2026-11-10 实测复现）。
;
; 只在 PREINSTALL 杀侧车：limo.exe 交给模板自己的检查处理，交互模式下它会先问
; 用户「是否关闭正在运行的应用」，比直接杀掉礼貌。卸载时两个都杀 —— 卸载完还留
; 一个占着端口的进程，同样会让人以为卸载没干净。

!macro NSIS_HOOK_PREINSTALL
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM LiMoBackend.exe'
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM LiMoBackend.exe'
!macroend
