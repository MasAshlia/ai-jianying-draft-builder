from __future__ import annotations

import logging
import os
from pathlib import Path
from queue import Empty, SimpleQueue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .environment import default_profile, launch_jianying
from .models import (BatchBuildRequest, BatchBuildResult, BatchItemStatus, BatchProgress,
                     BuildRequest, BuildResult, COMPATIBILITY_TEST_DRAFT_NAME,
                     JianyingProfile)
from .runtime import application_dir, configure_logging
from .scanner import EpisodeBatchScanner, MediaScanner
from .service import DraftBuildService


SINGLE_MODE, BATCH_MODE = "single", "batch"
STATUS_LABELS = {"processing": "处理中", "success": "成功", "failed": "失败", "skipped": "已跳过",
                 "not_processed": "未处理", "needs_review": "待核查", "pending": "待生成"}


def _format_duration(duration_us: int) -> str:
    minutes, seconds = divmod(duration_us / 1_000_000, 60)
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours}:{minutes:02d}:{seconds:05.2f}" if hours else f"{minutes:02d}:{seconds:05.2f}"


def folder_from_selected_video(selected: str) -> Path:
    return Path(selected).resolve().parent


def should_launch_after_batch(result: BatchBuildResult) -> bool:
    return result.succeeded_count > 0 and not result.stopped


def should_launch_after_single(profile: JianyingProfile) -> bool:
    return not profile.is_legacy_import_probe


def retry_failed_dirs(result: BatchBuildResult) -> tuple[Path, ...]:
    return tuple(item.source_dir for item in result.items if item.status is BatchItemStatus.FAILED)


class DraftBuilderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.service = DraftBuildService(default_profile())
        self.scanner, self.batch_scanner = MediaScanner(), EpisodeBatchScanner()
        self.mode_var = tk.StringVar(value=SINGLE_MODE)
        self.source_var, self.name_var, self.status_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.auto_open_var = tk.BooleanVar(value=not self.service.profile.is_legacy_import_probe)
        self.events = SimpleQueue()
        self.stop_event = threading.Event()
        self.busy = False
        self.building = False
        self.close_after = False
        self.rows: dict[str, dict] = {}
        self.last_result = None
        self.retry_request = None
        self.unresolved: list[dict] = []
        self._build_ui()
        self._change_mode()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._drain)
        self._set_busy(True)
        self._background(self._recover_worker)

    def _build_ui(self):
        self.root.title(f"AI 视频 → 剪映草稿 {__version__}")
        self.root.geometry("980x650")
        self.root.minsize(780, 540)
        frame = ttk.Frame(self.root, padding=20)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(5, weight=1)
        ttk.Label(frame, text="AI 视频素材一键生成剪映草稿", font=("Microsoft YaHei UI", 16, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        modes = ttk.Frame(frame)
        modes.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 12))
        self.single_radio = ttk.Radiobutton(modes, text="单集生成", value=SINGLE_MODE,
                                            variable=self.mode_var, command=self._change_mode)
        self.single_radio.pack(side="left")
        self.batch_radio = ttk.Radiobutton(modes, text="批量分集", value=BATCH_MODE,
                                           variable=self.mode_var, command=self._change_mode)
        self.batch_radio.pack(side="left", padx=20)
        self.source_label = ttk.Label(frame)
        self.source_label.grid(row=2, column=0, sticky="w")
        self.source_entry = ttk.Entry(frame, textvariable=self.source_var)
        self.source_entry.grid(row=3, column=0, sticky="ew", pady=6)
        self.browse_button = ttk.Button(frame, text="浏览素材", command=self._choose_source)
        self.browse_button.grid(row=3, column=1, padx=(10, 0))

        toolbar = ttk.Frame(frame)
        toolbar.grid(row=4, column=0, columnspan=2, sticky="ew", pady=6)
        self.all_button = ttk.Button(toolbar, text="全选", command=lambda: self._select_all(True))
        self.all_button.pack(side="left")
        self.none_button = ttk.Button(toolbar, text="取消全选", command=lambda: self._select_all(False))
        self.none_button.pack(side="left", padx=5)
        ttk.Button(toolbar, text="查看详情", command=self._details).pack(side="left", padx=5)
        ttk.Button(toolbar, text="打开草稿文件夹", command=self._open_draft).pack(side="left", padx=5)
        ttk.Button(toolbar, text="打开日志目录", command=self._open_logs).pack(side="right")

        container = ttk.Frame(frame)
        container.grid(row=5, column=0, columnspan=2, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        self.preview_tree = ttk.Treeview(container, columns=("selected", "order", "name", "count", "status", "result"),
                                        show="headings", height=10)
        for column, title, width in (("selected", "选择", 50), ("order", "顺序", 50), ("name", "名称", 210),
                                     ("count", "视频数", 65), ("status", "状态", 85), ("result", "结果", 430)):
            self.preview_tree.heading(column, text=title)
            self.preview_tree.column(column, width=width, minwidth=width, stretch=False)
        self.preview_tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(container, orient="vertical", command=self.preview_tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(container, orient="horizontal", command=self.preview_tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.preview_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.preview_tree.bind("<Button-1>", self._toggle_row)
        self.preview_tree.bind("<space>", self._toggle_keyboard)
        self.preview_tree.bind("<Double-1>", lambda _event: self._details())

        self.name_label = ttk.Label(frame, text="草稿名称")
        self.name_label.grid(row=6, column=0, sticky="w", pady=(10, 0))
        self.name_entry = ttk.Entry(frame, textvariable=self.name_var)
        self.name_entry.grid(row=7, column=0, columnspan=2, sticky="ew", pady=6)
        self.auto_open_check = ttk.Checkbutton(frame, text="完成后打开剪映", variable=self.auto_open_var)
        self.auto_open_check.grid(row=8, column=0, sticky="w")
        ttk.Label(frame, text="生成期间保持剪映关闭；生成后请勿移动、重命名或删除源视频。",
                  foreground="#9A5B00").grid(row=9, column=0, columnspan=2, sticky="w", pady=8)
        actions = ttk.Frame(frame)
        actions.grid(row=10, column=0, columnspan=2, sticky="ew")
        self.generate_button = ttk.Button(actions, text="生成剪映草稿", command=self._start_build)
        self.generate_button.pack(side="left", fill="x", expand=True, ipady=6)
        self.retry_button = ttk.Button(actions, text="重试失败分集", command=self._retry)
        self.retry_button.pack(side="left", padx=8, ipady=6)
        self.stop_button = ttk.Button(actions, text="完成当前集后停止", command=self._stop)
        self.stop_button.pack(side="left", ipady=6)
        ttk.Label(frame, textvariable=self.status_var, wraplength=900).grid(
            row=11, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def _post(self, callback, *args):
        self.events.put((callback, args))

    def _drain(self):
        try:
            for _ in range(100):
                callback, args = self.events.get_nowait()
                callback(*args)
                if self.close_after and not self.busy:
                    self.root.destroy()
                    return
        except Empty:
            pass
        except Exception:
            logging.getLogger(__name__).exception("界面消息处理失败")
        self.root.after(80, self._drain)

    def _background(self, target, *args):
        threading.Thread(target=target, args=args, daemon=False).start()

    def _set_busy(self, busy, building=False):
        self.busy, self.building = busy, building
        for widget in (self.single_radio, self.batch_radio, self.source_entry, self.browse_button,
                       self.name_entry, self.generate_button, self.auto_open_check):
            widget.configure(state="disabled" if busy else "normal")
        for widget in (self.all_button, self.none_button):
            widget.configure(state="normal" if not busy and self.mode_var.get() == BATCH_MODE else "disabled")
        if self.service.profile.is_legacy_import_probe:
            self.batch_radio.configure(state="disabled")
            self.auto_open_check.configure(state="disabled")
        self.retry_button.configure(state="normal" if not busy and self.last_result and retry_failed_dirs(self.last_result) else "disabled")
        self.stop_button.configure(state="normal" if building else "disabled")

    def _change_mode(self):
        if self.busy:
            return
        self.source_var.set("")
        self.name_var.set("")
        self.last_result, self.retry_request = None, None
        self._clear_rows()
        batch = self.mode_var.get() == BATCH_MODE
        self.source_label.configure(text="总素材目录（直属子文件夹各为一集）" if batch else "素材文件夹")
        self.name_label.configure(text="草稿名称前缀（可选，例如：短剧A-）" if batch else "草稿名称")
        self.browse_button.configure(text="选择总目录" if batch else "浏览素材")
        self.generate_button.configure(text="生成勾选分集" if batch else "生成剪映草稿")
        self.status_var.set("请选择素材目录。")
        self._set_busy(False)

    def _clear_rows(self):
        for iid in self.preview_tree.get_children():
            self.preview_tree.delete(iid)
        self.rows.clear()

    def _choose_source(self):
        if self.busy:
            return
        batch = self.mode_var.get() == BATCH_MODE
        if batch:
            selected = filedialog.askdirectory(title="选择包含各集子文件夹的总目录")
        else:
            selected = filedialog.askopenfilename(title="选择本集任意一个视频，将识别同文件夹全部视频",
                filetypes=[("视频素材", "*.mp4 *.mov *.mkv *.avi *.webm")])
        if not selected:
            return
        source = Path(selected) if batch else folder_from_selected_video(selected)
        self.source_var.set(str(source))
        self.last_result, self.retry_request = None, None
        if not batch:
            self.name_var.set(
                COMPATIBILITY_TEST_DRAFT_NAME
                if self.service.profile.is_legacy_import_probe else source.name
            )
        self._set_busy(True)
        self.status_var.set("正在扫描素材……")
        self._background(self._scan_worker, source, batch)

    def _scan_worker(self, source, batch):
        try:
            items = self.batch_scanner.discover(source) if batch else self.scanner.scan(source)
            self._post(self._show_scan, source, batch, items)
        except Exception as exc:
            logging.getLogger(__name__).exception("素材目录扫描失败：%s", source)
            self._post(self._show_error, f"扫描失败：{exc}")

    def _show_scan(self, source, batch, items):
        self._clear_rows()
        self.preview_source = source.resolve()
        for index, item in enumerate(items, 1):
            path = item.source_dir if batch else item
            count = item.video_count if batch else 1
            error = item.error if batch else ""
            review = any(Path(record["source_dir"]).resolve() == path.resolve() for record in self.unresolved)
            status = "needs_review" if review else "failed" if error else "pending" if count else "skipped"
            message = "上次任务中断，请先核查任务记录" if review else error or ("" if count else "没有支持的视频")
            row = {"path": path, "count": count, "status": status, "message": message,
                   "selected": bool(count) and not review, "result": None, "order": index}
            iid = self.preview_tree.insert("", "end")
            self.rows[iid] = row
            self._render_row(iid)
        self._set_busy(False)
        self.status_var.set(f"已识别 {len(items)} {'个分集目录' if batch else '个视频'}。")

    def _render_row(self, iid):
        row = self.rows[iid]
        select = ("☑" if row["selected"] else "☐") if self.mode_var.get() == BATCH_MODE else "—"
        self.preview_tree.item(iid, values=(select, row["order"], row["path"].name, row["count"],
                                           STATUS_LABELS[row["status"]], row["message"]))

    def _toggle_row(self, event):
        if self.preview_tree.identify_column(event.x) == "#1":
            self._toggle(self.preview_tree.identify_row(event.y))

    def _toggle_keyboard(self, _event):
        self._toggle(self.preview_tree.focus())
        return "break"

    def _toggle(self, iid):
        if self.busy or self.mode_var.get() != BATCH_MODE or iid not in self.rows:
            return
        row = self.rows[iid]
        if row["status"] == "needs_review":
            return
        row["selected"] = not row["selected"]
        self._render_row(iid)

    def _select_all(self, selected):
        if self.busy:
            return
        for iid, row in self.rows.items():
            row["selected"] = selected and row["status"] != "needs_review" and bool(row["count"])
            self._render_row(iid)

    def _start_build(self):
        if self.busy:
            return
        source = self.source_var.get().strip()
        if not source:
            messagebox.showwarning("缺少素材", "请先选择素材目录。")
            return
        batch = self.mode_var.get() == BATCH_MODE
        if batch:
            if not hasattr(self, "preview_source") or Path(source).resolve() != self.preview_source:
                self._set_busy(True)
                self.status_var.set("正在扫描新目录，请扫描完成后勾选分集。")
                self._background(self._scan_worker, Path(source), True)
                return
            paths = tuple(row["path"] for row in self.rows.values() if row["selected"])
            if not paths:
                messagebox.showwarning("未选择分集", "请至少勾选一集。")
                return
            request = BatchBuildRequest(Path(source), paths, self.name_var.get())
            self.retry_request = request
        else:
            request = BuildRequest(Path(source), self.name_var.get())
        self._run_request(request)

    def _retry(self):
        if self.busy or not self.last_result or not self.retry_request:
            return
        request = BatchBuildRequest(self.retry_request.parent_dir, retry_failed_dirs(self.last_result),
                                    self.retry_request.name_prefix)
        self._run_request(request)

    def _run_request(self, request):
        self.stop_event.clear()
        self._set_busy(True, building=True)
        self.status_var.set("正在检查环境并准备生成……")
        self._background(self._build_worker, request, self.auto_open_var.get())

    def _build_worker(self, request, auto_open):
        try:
            if isinstance(request, BatchBuildRequest):
                result = self.service.build_batch(request, lambda p: self._post(self._apply_progress, p), self.stop_event)
                launch = should_launch_after_batch(result)
            else:
                result = self.service.build(request)
                launch = should_launch_after_single(self.service.profile)
            warning = None
            launched = False
            if auto_open and launch and not self.stop_event.is_set():
                try:
                    launch_jianying(self.service.profile)
                    launched = True
                except Exception as exc:
                    logging.getLogger(__name__).exception("草稿已生成，启动剪映失败")
                    warning = str(exc)
            self._post(self._show_result, result, launched, warning)
        except Exception as exc:
            logging.getLogger(__name__).exception("生成任务失败")
            self._post(self._show_error, str(exc))

    def _apply_progress(self, progress: BatchProgress):
        for iid, row in self.rows.items():
            if row["path"] == progress.source_dir:
                row.update(status=progress.state.value, message=progress.message)
                self._render_row(iid)
                self.preview_tree.see(iid)
                break
        self.status_var.set(progress.message)

    def _show_result(self, result, launched, warning):
        if isinstance(result, BatchBuildResult):
            self.last_result = result
            for item in result.items:
                for iid, row in self.rows.items():
                    if row["path"] == item.source_dir:
                        row.update(status=item.status.value, message=item.message, result=item.build_result,
                                   selected=item.status in (BatchItemStatus.FAILED, BatchItemStatus.NOT_PROCESSED))
                        self._render_row(iid)
            pending = sum(item.status is BatchItemStatus.NOT_PROCESSED for item in result.items)
            review = sum(item.status is BatchItemStatus.NEEDS_REVIEW for item in result.items)
            summary = (f"{'任务已停止' if result.stopped else '批量完成'}：成功 {result.succeeded_count} 集，"
                       f"失败 {result.failed_count} 集，跳过 {result.skipped_count} 集，"
                       f"未处理 {pending} 集，待核查 {review} 集。\n"
                       f"片段 {result.total_clips} 个，总时长 {_format_duration(result.total_duration_us)}。\n"
                       f"任务记录：{result.report_path}")
        else:
            summary = (f"生成成功：{result.draft_dir.name}\n片段 {result.clip_count} 个，"
                       f"总时长 {_format_duration(result.duration_us)}\n草稿路径：{result.draft_dir}\n"
                       f"根索引备份：{result.root_meta_backup_path or '未返回备份路径'}")
            for iid, row in self.rows.items():
                row.update(status="success", message=str(result.draft_dir), result=result)
                self._render_row(iid)
        if warning:
            summary += f"\n草稿已保留，剪映未能自动启动：{warning}"
        elif launched:
            summary += "\n剪映正在启动，请勿移动或删除源素材。"
        elif self.service.profile.is_legacy_import_probe:
            summary += "\n兼容性探针不会自动启动剪映，请人工打开并验收。"
        self._set_busy(False)
        self.status_var.set(summary)
        if not self.close_after:
            self._text_window("生成结果", summary)

    def _show_error(self, message):
        self._set_busy(False)
        self.status_var.set(message)
        if not self.close_after:
            messagebox.showerror("操作未完成", message)

    def _stop(self):
        if self.building:
            self.stop_event.set()
            self.stop_button.configure(state="disabled")
            self.status_var.set("已请求停止，正在等待当前集安全结束……")

    def _on_close(self):
        if not self.busy:
            self.root.destroy()
        elif messagebox.askyesno("任务正在进行", "是否等待当前操作安全结束后退出？选择“否”将继续工作。"):
            self.close_after = True
            self.stop_event.set()
            self.status_var.set("正在等待当前操作安全结束，然后退出……")

    def _recover_worker(self):
        try:
            unresolved = self.service.recover()
            self._post(self._show_recovery, unresolved)
        except Exception as exc:
            logging.getLogger(__name__).exception("启动核查失败")
            self._post(self._show_error, str(exc))

    def _show_recovery(self, unresolved):
        self.unresolved = unresolved
        self._set_busy(False)
        if unresolved:
            self.status_var.set(f"发现 {len(unresolved)} 个待核查任务，相关分集暂不自动重试。")
            self._text_window("上次任务待核查", "\n\n".join(
                f"{item['source_dir']}\n{item['message']}\n记录：{item['report_path']}" for item in unresolved))

    def _details(self):
        iid = self.preview_tree.focus()
        if iid in self.rows:
            row = self.rows[iid]
            text = f"素材：{row['path']}\n状态：{STATUS_LABELS[row['status']]}\n{row['message']}"
            if row["result"]:
                result = row["result"]
                text += f"\n草稿：{result.draft_dir}\n片段：{result.clip_count}\n时长：{_format_duration(result.duration_us)}"
            self._text_window("详情", text)

    def _text_window(self, title, content):
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry("750x350")
        text = tk.Text(window, wrap="word", padx=12, pady=12)
        scroll = ttk.Scrollbar(window, orient="vertical", command=text.yview)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.configure(yscrollcommand=scroll.set)
        text.insert("1.0", content)
        text.configure(state="disabled")

    def _open_draft(self):
        row = self.rows.get(self.preview_tree.focus())
        if row and row["result"]:
            self._open_path(row["result"].draft_dir)

    def _open_logs(self):
        self._open_path(application_dir())

    def _open_path(self, path):
        try:
            os.startfile(str(path))
        except OSError as exc:
            messagebox.showerror("无法打开目录", str(exc))


def main() -> None:
    from multiprocessing import freeze_support
    freeze_support()
    import sys
    if "--smoke-test" in sys.argv:
        from .smoke import main as smoke_main
        smoke_main()
        return
    root = tk.Tk()
    try:
        configure_logging()
        ttk.Style(root).theme_use("vista")
        DraftBuilderApp(root)
    except Exception as exc:
        messagebox.showerror("启动失败", f"无法启动工具，请检查日志目录权限：{exc}")
        root.destroy()
        return
    root.mainloop()
