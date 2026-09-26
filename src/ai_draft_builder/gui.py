from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .environment import default_profile, launch_jianying
from .errors import UserFacingError
from .models import BuildRequest, BuildResult
from .service import DraftBuildService


def _format_duration(duration_us: int) -> str:
    total_seconds = duration_us / 1_000_000
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:05.2f}"
    return f"{minutes:02d}:{seconds:05.2f}"


class DraftBuilderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.service = DraftBuildService(default_profile())
        self.source_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.status_var = tk.StringVar(value="请选择一集视频素材所在的文件夹。")
        self._build_ui()

    def _build_ui(self) -> None:
        self.root.title("AI 视频 → 剪映草稿")
        self.root.geometry("680x330")
        self.root.minsize(620, 300)

        frame = ttk.Frame(self.root, padding=24)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="AI 视频素材一键生成剪映草稿", font=("Microsoft YaHei UI", 16, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 20)
        )
        ttk.Label(frame, text="素材文件夹").grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Entry(frame, textvariable=self.source_var).grid(row=2, column=0, sticky="ew", pady=(6, 14))
        ttk.Button(frame, text="选择文件夹", command=self._choose_folder).grid(
            row=2, column=1, padx=(10, 0), pady=(6, 14)
        )

        ttk.Label(frame, text="草稿名称").grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Entry(frame, textvariable=self.name_var).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(6, 16)
        )
        ttk.Label(
            frame,
            text="素材采用原路径引用。生成后请勿移动、重命名或删除源视频。",
            foreground="#9A5B00",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 16))

        self.generate_button = ttk.Button(
            frame, text="生成剪映草稿", command=self._start_build
        )
        self.generate_button.grid(row=6, column=0, columnspan=2, sticky="ew", ipady=7)
        ttk.Label(frame, textvariable=self.status_var, wraplength=620).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(16, 0)
        )

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(title="选择一集 AI 视频素材文件夹")
        if selected:
            path = Path(selected)
            self.source_var.set(str(path))
            self.name_var.set(path.name)
            self.status_var.set("已选择素材文件夹，可以生成草稿。")

    def _start_build(self) -> None:
        source = self.source_var.get().strip()
        if not source:
            messagebox.showwarning("缺少素材", "请先选择素材文件夹。")
            return
        request = BuildRequest(Path(source), self.name_var.get())
        self.generate_button.configure(state="disabled")
        self.status_var.set("正在检查素材并生成草稿，请稍候……")
        threading.Thread(target=self._build_worker, args=(request,), daemon=True).start()

    def _build_worker(self, request: BuildRequest) -> None:
        try:
            result = self.service.build(request)
        except UserFacingError as exc:
            self.root.after(0, self._show_error, str(exc))
        except Exception as exc:
            self.root.after(0, self._show_error, f"发生未预期错误：{exc}")
        else:
            launch_warning = None
            try:
                launch_jianying(self.service.profile)
            except UserFacingError as exc:
                # 草稿已经安全生成；启动失败不能回滚或误报为生成失败。
                launch_warning = str(exc)
            self.root.after(0, self._show_success, result, launch_warning)

    def _show_error(self, message: str) -> None:
        self.generate_button.configure(state="normal")
        self.status_var.set(f"生成失败：{message}")
        messagebox.showerror("生成失败", message)

    def _show_success(self, result: BuildResult, launch_warning: str | None = None) -> None:
        self.generate_button.configure(state="normal")
        message = (
            f"草稿名称：{result.draft_dir.name}\n"
            f"片段数量：{result.clip_count}\n"
            f"总时长：{_format_duration(result.duration_us)}\n"
            f"草稿路径：{result.draft_dir}\n\n"
            + (
                f"剪映未能自动启动：{launch_warning}\n请手动打开剪映。"
                if launch_warning
                else "剪映正在启动。请勿移动或删除源视频。"
            )
        )
        self.status_var.set(f"生成成功：{result.draft_dir.name}（{result.clip_count} 个片段）")
        messagebox.showinfo("生成成功", message)


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("vista")
    except tk.TclError:
        pass
    DraftBuilderApp(root)
    root.mainloop()
