from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .environment import default_profile, launch_jianying
from .errors import UserFacingError
from .models import (
    BatchBuildRequest,
    BatchBuildResult,
    BatchItemStatus,
    BatchProgress,
    BatchProgressState,
    BuildRequest,
    BuildResult,
    EpisodeFolder,
)
from .scanner import EpisodeBatchScanner, MediaScanner
from .service import DraftBuildService


SINGLE_MODE = "single"
BATCH_MODE = "batch"


def _format_duration(duration_us: int) -> str:
    total_seconds = duration_us / 1_000_000
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:05.2f}"
    return f"{minutes:02d}:{seconds:05.2f}"


def folder_from_selected_video(selected: str) -> Path:
    """由文件浏览窗口中选中的任意视频定位整集素材目录。"""
    return Path(selected).resolve().parent


def should_launch_after_batch(result: BatchBuildResult) -> bool:
    return result.succeeded_count > 0


class DraftBuilderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.service = DraftBuildService(default_profile())
        self.scanner = MediaScanner()
        self.batch_scanner = EpisodeBatchScanner()
        self.mode_var = tk.StringVar(value=SINGLE_MODE)
        self.source_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self._preview_rows: dict[str, str] = {}
        self._build_ui()
        self._change_mode()

    def _build_ui(self) -> None:
        self.root.title("AI 视频 → 剪映草稿")
        self.root.geometry("860x600")
        self.root.minsize(740, 520)

        frame = ttk.Frame(self.root, padding=24)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(5, weight=1)

        ttk.Label(frame, text="AI 视频素材一键生成剪映草稿", font=("Microsoft YaHei UI", 16, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )

        mode_frame = ttk.Frame(frame)
        mode_frame.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 14))
        self.single_radio = ttk.Radiobutton(
            mode_frame, text="单集生成", value=SINGLE_MODE, variable=self.mode_var,
            command=self._change_mode,
        )
        self.single_radio.pack(side="left")
        self.batch_radio = ttk.Radiobutton(
            mode_frame, text="批量分集", value=BATCH_MODE, variable=self.mode_var,
            command=self._change_mode,
        )
        self.batch_radio.pack(side="left", padx=(22, 0))

        self.source_label = ttk.Label(frame)
        self.source_label.grid(row=2, column=0, columnspan=2, sticky="w")
        self.source_entry = ttk.Entry(frame, textvariable=self.source_var)
        self.source_entry.grid(row=3, column=0, sticky="ew", pady=(6, 14))
        self.browse_button = ttk.Button(frame, command=self._choose_source)
        self.browse_button.grid(row=3, column=1, padx=(10, 0), pady=(6, 14))

        self.preview_label = ttk.Label(frame)
        self.preview_label.grid(row=4, column=0, columnspan=2, sticky="w")
        preview_frame = ttk.Frame(frame)
        preview_frame.grid(row=5, column=0, columnspan=2, sticky="nsew", pady=(6, 14))
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.preview_tree = ttk.Treeview(
            preview_frame,
            columns=("order", "name", "count", "status", "result"),
            show="headings",
            height=9,
        )
        for column, title, width, anchor in (
            ("order", "顺序", 55, "center"),
            ("name", "名称", 230, "w"),
            ("count", "视频数", 70, "center"),
            ("status", "状态", 90, "center"),
            ("result", "结果", 280, "w"),
        ):
            self.preview_tree.heading(column, text=title)
            self.preview_tree.column(column, width=width, minwidth=width, anchor=anchor)
        self.preview_tree.grid(row=0, column=0, sticky="nsew")
        preview_scroll = ttk.Scrollbar(preview_frame, orient="vertical", command=self.preview_tree.yview)
        preview_scroll.grid(row=0, column=1, sticky="ns")
        self.preview_tree.configure(yscrollcommand=preview_scroll.set)

        self.name_label = ttk.Label(frame, text="草稿名称")
        self.name_label.grid(row=6, column=0, columnspan=2, sticky="w")
        self.name_entry = ttk.Entry(frame, textvariable=self.name_var)
        self.name_entry.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(6, 14))

        ttk.Label(
            frame,
            text="素材采用原路径引用。生成后请勿移动、重命名或删除源视频。",
            foreground="#9A5B00",
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(0, 14))

        self.generate_button = ttk.Button(frame, command=self._start_build)
        self.generate_button.grid(row=9, column=0, columnspan=2, sticky="ew", ipady=7)
        ttk.Label(frame, textvariable=self.status_var, wraplength=790).grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(14, 0)
        )

    def _change_mode(self) -> None:
        self.source_var.set("")
        self.name_var.set("")
        self._clear_preview()
        if self.mode_var.get() == BATCH_MODE:
            self.source_label.configure(text="总素材文件夹（每个直属子文件夹视为一集）")
            self.preview_label.configure(text="已识别的分集")
            self.browse_button.configure(text="选择总目录")
            self.generate_button.configure(text="批量生成剪映草稿")
            self.name_entry.configure(state="disabled")
            self.status_var.set("请选择包含各集子文件夹的总素材目录。")
        else:
            self.source_label.configure(text="素材文件夹")
            self.preview_label.configure(text="已识别的视频")
            self.browse_button.configure(text="浏览素材")
            self.generate_button.configure(text="生成剪映草稿")
            self.name_entry.configure(state="normal")
            self.status_var.set("请选择一集视频素材所在的文件夹。")

    def _choose_source(self) -> None:
        if self.mode_var.get() == BATCH_MODE:
            self._choose_batch_folder()
        else:
            self._choose_single_folder()

    def _choose_single_folder(self) -> None:
        current = self.source_var.get().strip()
        initial_dir = current if current and Path(current).is_dir() else None
        selected = filedialog.askopenfilename(
            title="选择该集中的任意一个视频（将导入同文件夹全部视频）",
            initialdir=initial_dir,
            filetypes=[("视频素材", "*.mp4 *.mov *.mkv *.avi *.webm")],
        )
        if not selected:
            return
        folder = folder_from_selected_video(selected)
        try:
            videos = self.scanner.scan(folder)
        except UserFacingError as exc:
            self._show_error(str(exc))
            return
        self.source_var.set(str(folder))
        self.name_var.set(folder.name)
        self._clear_preview()
        for index, video in enumerate(videos, start=1):
            self.preview_tree.insert("", "end", values=(index, video.name, "—", "待生成", ""))
        self.status_var.set(f"已识别 {len(videos)} 个视频，将按以上顺序生成草稿。")

    def _choose_batch_folder(self) -> None:
        current = self.source_var.get().strip()
        initial_dir = current if current and Path(current).is_dir() else None
        selected = filedialog.askdirectory(title="选择包含各集子文件夹的总素材目录", initialdir=initial_dir)
        if not selected:
            return
        parent = Path(selected)
        try:
            episodes = self.batch_scanner.discover(parent)
        except UserFacingError as exc:
            self._show_error(str(exc))
            return
        self.source_var.set(str(parent.resolve()))
        self._populate_batch_preview(episodes)

    def _populate_batch_preview(self, episodes: list[EpisodeFolder]) -> None:
        self._clear_preview()
        ready_count = 0
        for index, episode in enumerate(episodes, start=1):
            if episode.video_count:
                status, result = "待生成", ""
                ready_count += 1
            else:
                status, result = "将跳过", "没有支持的视频"
            iid = self.preview_tree.insert(
                "", "end",
                values=(index, episode.source_dir.name, episode.video_count, status, result),
            )
            self._preview_rows[str(episode.source_dir)] = iid
        self.status_var.set(
            f"已发现 {len(episodes)} 个子文件夹，其中 {ready_count} 集包含可生成的视频。"
        )

    def _clear_preview(self) -> None:
        for item in self.preview_tree.get_children():
            self.preview_tree.delete(item)
        self._preview_rows.clear()

    def _start_build(self) -> None:
        source = self.source_var.get().strip()
        if not source:
            messagebox.showwarning("缺少素材", "请先选择素材文件夹。")
            return
        if self.mode_var.get() == BATCH_MODE:
            try:
                episodes = self.batch_scanner.discover(Path(source))
            except UserFacingError as exc:
                self._show_error(str(exc))
                return
            self._populate_batch_preview(episodes)
            request = BatchBuildRequest(Path(source))
            self._set_running(True)
            self.status_var.set("正在检查环境并准备批量生成，请稍候……")
            threading.Thread(target=self._batch_worker, args=(request,), daemon=True).start()
        else:
            request = BuildRequest(Path(source), self.name_var.get())
            self._set_running(True)
            self.status_var.set("正在检查素材并生成草稿，请稍候……")
            threading.Thread(target=self._single_worker, args=(request,), daemon=True).start()

    def _single_worker(self, request: BuildRequest) -> None:
        try:
            result = self.service.build(request)
        except UserFacingError as exc:
            self.root.after(0, self._show_error, str(exc))
        except Exception as exc:
            self.root.after(0, self._show_error, f"发生未预期错误：{exc}")
        else:
            launch_warning = self._try_launch_jianying()
            self.root.after(0, self._show_single_success, result, launch_warning)

    def _batch_worker(self, request: BatchBuildRequest) -> None:
        try:
            result = self.service.build_batch(request, self._queue_progress)
        except UserFacingError as exc:
            self.root.after(0, self._show_error, str(exc))
        except Exception as exc:
            self.root.after(0, self._show_error, f"发生未预期错误：{exc}")
        else:
            launch_warning = None
            if should_launch_after_batch(result):
                launch_warning = self._try_launch_jianying()
            self.root.after(0, self._show_batch_result, result, launch_warning)

    def _queue_progress(self, progress: BatchProgress) -> None:
        self.root.after(0, self._apply_progress, progress)

    def _apply_progress(self, progress: BatchProgress) -> None:
        iid = self._preview_rows.get(str(progress.source_dir))
        labels = {
            BatchProgressState.PROCESSING: "生成中",
            BatchProgressState.SUCCESS: "成功",
            BatchProgressState.FAILED: "失败",
            BatchProgressState.SKIPPED: "已跳过",
        }
        if iid:
            values = list(self.preview_tree.item(iid, "values"))
            values[3] = labels[progress.state]
            values[4] = progress.message
            self.preview_tree.item(iid, values=values)
            self.preview_tree.see(iid)
        if progress.state is BatchProgressState.PROCESSING:
            self.status_var.set(progress.message)

    def _try_launch_jianying(self) -> str | None:
        try:
            launch_jianying(self.service.profile)
        except UserFacingError as exc:
            return str(exc)
        return None

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.generate_button.configure(state=state)
        self.browse_button.configure(state=state)
        self.source_entry.configure(state=state)
        self.single_radio.configure(state=state)
        self.batch_radio.configure(state=state)
        self.name_entry.configure(
            state="disabled" if running or self.mode_var.get() == BATCH_MODE else "normal"
        )

    def _show_error(self, message: str) -> None:
        self._set_running(False)
        self.status_var.set(f"生成失败：{message}")
        messagebox.showerror("生成失败", message)

    def _show_single_success(self, result: BuildResult, launch_warning: str | None = None) -> None:
        self._set_running(False)
        message = (
            f"草稿名称：{result.draft_dir.name}\n"
            f"片段数量：{result.clip_count}\n"
            f"总时长：{_format_duration(result.duration_us)}\n"
            f"草稿路径：{result.draft_dir}\n\n"
            + (
                f"剪映未能自动启动：{launch_warning}\n请手动打开剪映。"
                if launch_warning else "剪映正在启动。请勿移动或删除源视频。"
            )
        )
        self.status_var.set(f"生成成功：{result.draft_dir.name}（{result.clip_count} 个片段）")
        messagebox.showinfo("生成成功", message)

    def _show_batch_result(
        self, result: BatchBuildResult, launch_warning: str | None = None
    ) -> None:
        self._set_running(False)
        for item in result.items:
            iid = self._preview_rows.get(str(item.source_dir))
            if not iid:
                continue
            values = list(self.preview_tree.item(iid, "values"))
            values[3] = {
                BatchItemStatus.SUCCESS: "成功",
                BatchItemStatus.FAILED: "失败",
                BatchItemStatus.SKIPPED: "已跳过",
            }[item.status]
            values[4] = item.message
            self.preview_tree.item(iid, values=values)

        lines = [
            "批量生成完成", "",
            f"成功：{result.succeeded_count} 集",
            f"失败：{result.failed_count} 集",
            f"跳过：{result.skipped_count} 集",
            f"片段总数：{result.total_clips}",
            f"总时长：{_format_duration(result.total_duration_us)}",
        ]
        failed = [item for item in result.items if item.status is BatchItemStatus.FAILED]
        skipped = [item for item in result.items if item.status is BatchItemStatus.SKIPPED]
        if failed:
            lines.extend(["", "失败："])
            lines.extend(f"{item.requested_name}：{item.message}" for item in failed)
        if skipped:
            lines.extend(["", "跳过："])
            lines.extend(f"{item.requested_name}：{item.message}" for item in skipped)
        if launch_warning:
            lines.extend(["", f"剪映未能自动启动：{launch_warning}", "请手动打开剪映。"])
        elif result.succeeded_count:
            lines.extend(["", "剪映正在启动。请勿移动或删除源视频。"])

        self.status_var.set(
            f"批量完成：成功 {result.succeeded_count} 集，失败 {result.failed_count} 集，"
            f"跳过 {result.skipped_count} 集。"
        )
        message = "\n".join(lines)
        if failed or not result.succeeded_count:
            messagebox.showwarning("批量生成完成", message)
        else:
            messagebox.showinfo("批量生成完成", message)


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("vista")
    except tk.TclError:
        pass
    DraftBuilderApp(root)
    root.mainloop()
