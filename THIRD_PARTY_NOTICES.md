# 第三方组件说明

- [pyJianYingDraft 0.3.0](https://github.com/GuanYixuan/pyJianYingDraft)：Apache License 2.0。用于草稿对象模型和 JSON 生成。
- [pymediainfo 7.0.1](https://github.com/sbraz/pymediainfo)：MIT License。用于读取视频时长和尺寸；Windows wheel 携带 MediaInfo 动态库及其许可证文件。
- [uiautomation 2.0.29](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows)：Apache License 2.0。它是 pyJianYingDraft 的传递依赖；本项目不调用其自动化能力。
- [PyInstaller 6.16.0](https://pyinstaller.org/)：GPLv2-or-later，带允许分发非自由程序的 bootloader 例外。仅用于构建 Windows 产物。

完整许可证文本随各 Python 包安装元数据或打包目录中的相关文件提供。发布前应保留本说明及第三方许可证文件。

测试包的构建处理：第三方 Python 模块以 PyInstaller 字节码归档分发，不额外收集 `.py` 源码副本；NumPy 的构建诊断模块中，上游 CI 机器绝对路径被替换为占位文字，并省略 DELVEWHEEL 构建命令记录。数值运行库和功能未修改，原始许可证保留在 licenses 目录。

