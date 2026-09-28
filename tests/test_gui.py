from pathlib import Path
import tkinter as tk
from types import SimpleNamespace

import pytest

import ai_draft_builder.gui as gui
from ai_draft_builder.models import BatchBuildRequest, BatchBuildResult, BatchItemResult, BatchItemStatus, EpisodeFolder


@pytest.fixture(scope="module")
def tk_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def app(monkeypatch, tk_root):
    root = tk_root
    monkeypatch.setattr(gui.DraftBuilderApp, "_background", lambda *_: None)
    instance = gui.DraftBuilderApp(root)
    instance._set_busy(False)
    instance.mode_var.set(gui.BATCH_MODE)
    instance._change_mode()
    yield instance
    for after_id in root.tk.call("after", "info"):
        root.after_cancel(after_id)
    for widget in root.winfo_children():
        widget.destroy()


def test_checkbox_freezes_selected_paths_and_disables_inputs(app, tmp_path):
    paths = [tmp_path / f"第{i}集" for i in (1, 2)]
    app.source_var.set(str(tmp_path))
    app._show_scan(tmp_path, True, [EpisodeFolder(path, 2) for path in paths])
    ids = list(app.rows)
    app._toggle(ids[1])
    captured = []
    app._background = lambda target, *args: captured.append(args)
    app._start_build()
    assert captured[0][0].selected_dirs == (paths[0],)
    assert app.busy and app.building
    assert str(app.browse_button["state"]) == "disabled"
    app._start_build()
    assert len(captured) == 1


def test_close_waits_for_current_operation_and_cancel_continues(app, monkeypatch):
    app._set_busy(True, building=True)
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *_: False)
    app._on_close()
    assert not app.stop_event.is_set() and not app.close_after
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *_: True)
    app._on_close()
    assert app.stop_event.is_set() and app.close_after


@pytest.mark.parametrize("count,stopped,auto,expected", [(0, False, True, 0), (2, False, True, 1),
                                                       (2, True, True, 0), (2, False, False, 0)])
def test_worker_launches_once_only_after_normal_completion(app, monkeypatch, count, stopped, auto, expected):
    result = BatchBuildResult((), count, 1, 0, 3, 5_000_000, stopped)
    app.service = SimpleNamespace(profile=None, build_batch=lambda *_: result)
    launches = []
    monkeypatch.setattr(gui, "launch_jianying", lambda profile: launches.append(profile))
    app._build_worker(BatchBuildRequest(Path("C:/test")), auto)
    assert len(launches) == expected
    callback, args = app.events.get_nowait()
    assert callback == app._show_result and args[0] is result


def test_retry_excludes_success_pending_and_review(app, tmp_path):
    states = [BatchItemStatus.SUCCESS, BatchItemStatus.FAILED, BatchItemStatus.NOT_PROCESSED,
              BatchItemStatus.NEEDS_REVIEW]
    items = tuple(BatchItemResult(tmp_path / str(i), str(i), state, None, "") for i, state in enumerate(states))
    app.last_result = BatchBuildResult(items, 1, 1, 0, 1, 100)
    app.retry_request = BatchBuildRequest(tmp_path, name_prefix="剧A-")
    captured = []
    app._run_request = captured.append
    app._retry()
    assert captured[0].selected_dirs == (tmp_path / "1",)
    assert captured[0].name_prefix == "剧A-"


def test_recovery_rows_cannot_be_selected(app, tmp_path):
    episode = tmp_path / "第1集"
    app.unresolved = [{"source_dir": str(episode)}]
    app._show_scan(tmp_path, True, [EpisodeFolder(episode, 2)])
    iid = next(iter(app.rows))
    app._toggle(iid)
    app._select_all(True)
    assert not app.rows[iid]["selected"]
