# OpenSweep 1.0.1 交接（2026-10-25）

## 当前状态

源码中的启动页崩溃及缺少翻译已修复，91 项自动测试通过。已构建 1.0.1 Windows 程序，安装包和便携包输出到本次 Codex 工作区的 outputs 目录。原始交接文档及修改前源码保存在 OpenSweep-source-before-fix.zip。

**另一台电脑“关闭自启后重启仍运行”的最终行为尚未验证。不能把本机测试通过等同于那台电脑的问题已实测解决。**

## 这次修复

- core/startup_store.py：在原用户/机器范围备份 Run 值，移出实际 Run 启动命令；保存原值类型。启动文件夹入口同卷移出，恢复时防止覆盖冲突。
- core/startup.py：读取备份项仍显示关闭；服务关闭改为 Disabled，并保存恢复原启动模式及延迟启动。保留关闭的服务，识别受监控的旧版 Manual 服务。修改后读取实际状态，失败不记为成功。
- core/guard.py：识别只有禁用标记但启动命令仍在的旧项，以及重建后仍带关闭标记的项。打开程序时或登录检查时加强受监控的旧关闭项。
- gui/pages/startup.py：修复 PageHeader 多余位置参数；按钮放到一个右侧容器，恢复启动页。
- core/boottrace.py、gui/boot_dialog.py：启动诊断是只读实时快照，包括所有启动入口、进程及父进程、版本、时间和守护记录。路径匹配仅为候选，不将同目录当成启动证明，不再限制默认只取开机后15分钟。
- cli.py / __main__.py：新增 startup-report --output PATH，只读 JSON 报告。
- locales/*.json：补齐十种语言，修正文案对“整个软件绝不再启动”的过度承诺。
- packaging/opensweep.spec：排除从开发环境 PATH 误收集的同名第三方 ICU 及其数据库。它们导致图形程序导入 QtCore 时 DLL 异常。tools/check_package.py 可实际运行冻结后的守护关闭入口和命令行诊断，防止只测试源码而漏掉打包故障。
- 程序和安装包版本更新为 1.0.1，CHANGELOG.md 已更新。

## 验证

- pytest -q：91 passed（含原59项及新增32项）。
- 真实 Windows 一次性夹具：Run 值原类型恢复、计划任务关闭/开启读取、服务 Disabled/原 Manual 模式恢复均通过。
- Qt 离屏渲染三套皮肤的启动页和诊断窗口已目视检查；十种语言构造测试通过。
- 打包后的 opensweep-cli.exe --version 返回 1.0.1；startup-report 实际运行成功；OpenSweep.exe --guard 在独立 APPDATA、guard=false 配置下正常退出（不改动系统启动设置）。
- 尚未进行原问题电脑重启测试、真实登录守护托盘通知验证和安装升级实测。

## 复现开发检查

在源码根目录，使用已有的 Python 环境（pytest、PySide6、psutil、send2trash、PyInstaller）：

~~~powershell
$env:QT_QPA_PLATFORM="offscreen"
python -m pytest -q
python -m opensweep startup-report --output startup-report.json
python -m PyInstaller packaging/opensweep.spec --noconfirm
iscc /DAppVersion=1.0.1 packaging/opensweep.iss
~~~

项目的 pyproject.toml 已为 pytest 配置 src 路径；直接 python -m opensweep 需要设置 PYTHONPATH=src 或先安装项目。长路径环境建议复制到短路径构建；不要在原 Claude 临时目录建立虚拟环境。

## 下一步

让原问题电脑运行 1.0.1（首次管理员运行，刷新启动页），重启后观察。若仍有程序启动，保留启动诊断报告，核对该程序的多个入口、父进程、是否在修改前已启动、是否为 Windows 会话恢复。

普通用户登录时无管理员权限，守护不能直接修改机器级项；界面会提示处理。守护是一次检查，不能保证赶在所有程序之前，也不终止已运行程序。

回退前应使用 1.0.1 重新勾选恢复对应入口。备份和详细说明见随附 docs/startup-fix-1.0.1.md。
