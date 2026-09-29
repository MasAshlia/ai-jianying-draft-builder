# AI Draft Builder 0.3.0 本机安装与剪映 11.5 兼容报告

## 来源与安装

- GitHub 仓库：`MasAshlia/ai-jianying-draft-builder`
- GitHub 最新提交：`3fda40486198c8cc10d8cbe74cd96a22a508f843`
- 提交说明：`Prepare 0.3.0 workstation test release`
- 程序版本：`0.3.0`
- 本机源码目录：`C:\AIDraftBuilder-0.3.0`
- 本机兼容分支：`compat/jianying-11.5-test`
- Python：3.12.10，独立虚拟环境位于 `.venv`
- 未修改、提交或推送 GitHub main。
- 旧版目录 `C:\AIDraftBuilder` 及其未提交兼容工作未被覆盖。

## 本机剪映适配

- 实际剪映版本：`11.5.0.14471`
- 实际程序：`C:\Users\Administrator\AppData\Local\JianyingPro\Apps\11.5.0.14471\JianyingPro.exe`
- 草稿目录：`C:\Users\Administrator\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft`
- 兼容模式：`verified_legacy_import`
- 写入草稿的兼容格式版本：`11.3.0`

本次仍采用向后兼容导入策略，不猜测或仿写剪映 11.5 的加密原生草稿。仅精确接受 `11.5.0.14471`，生成已验证的 11.3 明文草稿，由剪映 11.5 首次打开时读取和升级。

人工兼容性验收已于 2026-09-29 确认成功。正式兼容行为：

- 支持单集和批量模式。
- 支持 `E48` 等合法自定义草稿名称。
- 同名草稿沿用 0.3.0 的不覆盖策略，生成 `名称 (1)`、`名称 (2)`。
- 根索引注册前继续使用 0.3.0 的进程、磁盘、源文件和索引冲突检查。
- 成功结果返回并显示根索引备份位置。
- 正常完成后可按界面选项自动启动剪映。
- 未修改 pyJianYingDraft、排序、时间线、素材引用或根索引字段结构。

## 自动化验证

- 基线测试：63 通过，1 项因缺少真实 MP4 跳过。
- 正式兼容模式使用两段隔离合成 MP4：76 通过，0 失败，0 跳过。
- 发布包子进程烟雾测试：通过。
- 烟雾测试片段：2 个，总时长 2,200,000 微秒。
- 发布内容审计：通过，无源码、测试素材、虚拟环境或个人构建路径。
- GUI 启动检查：通过，窗口标题 `AI 视频 → 剪映草稿 0.3.0`。

## 构建产物

- onedir 程序：`C:\AIDraftBuilder-0.3.0\dist\AIDraftBuilder\AIDraftBuilder.exe`
- EXE SHA-256：`9978D09BFC063B46996B803DB65BCF78713173BFED8C145E5DBE0EA427EFEFE7`
- 发布 ZIP：`C:\AIDraftBuilder-0.3.0\dist\AIDraftBuilder-v0.3.0-win64.zip`
- ZIP SHA-256：`1252BA20EF32B0AB9C5FF2CD65FD941E9B7F8FD7F147C018AB3763EC08F34943`

构建脚本增加 `--clean`，避免 PyInstaller 复用旧分析缓存。发布审计对通用 Windows 账户名 `Administrator` 不再做裸词误报，但仍保留完整个人路径检测。

## 当前写入状态

- 当前剪映仍在运行，共检测到 9 个 `JianyingPro.exe` 进程。
- 环境检查已验证会拒绝生成，并提示完全退出剪映。
- 当前根索引 SHA-256：`417692BE57EA73C2A40C55697D26089E61F829F34D280BD68A87F175695C7A3A`
- 当前根索引草稿数：16。剪映运行期间根索引发生了外部变化，本次构建未调用草稿写入。
- 本轮没有向真实剪映草稿目录写入，没有创建根索引备份，没有删除或修改现有草稿。
- `C:\AIDraftBuilderTest\build-smoke` 仅含两段合成构建素材；发布烟雾测试输出位于隔离的构建目录，不注册到剪映。

## 人工兼容验收结论

用户已确认兼容性验收成功。精确版本 `11.5.0.14471` 可作为 `verified_legacy_import` 正式构建目标；支持范围不自动扩大到其他 11.5.x 或更高版本，且仍不宣称支持 11.5 原生加密草稿写出。
