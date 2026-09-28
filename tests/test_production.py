from __future__ import annotations

import json
import multiprocessing as mp
import os
from pathlib import Path
import threading
import time

import pytest

from ai_draft_builder.errors import DraftBuildError, EnvironmentCheckError, FatalBuildError, MediaProbeError
from ai_draft_builder.models import BatchBuildRequest, BatchItemStatus, BuildRequest, ClipInfo
from ai_draft_builder.preparation import ProcessPreparer, fingerprint, verify_sources
from ai_draft_builder.registry import DraftRegistrar
from ai_draft_builder.runtime import GenerationLock, TaskJournal, reconcile_records
from ai_draft_builder.scanner import EpisodeBatchScanner
from ai_draft_builder.service import DraftBuildService
from ai_draft_builder.validator import DraftValidator
from conftest import make_clips, write_valid_draft
from test_service import JsonAdapter, ReadyEnvironment


class PathProbe:
    def probe_all(self, paths):
        return [ClipInfo(path, 1_000_000 + index, 1080, 1920) for index, path in enumerate(paths)]


def service(profile, **kwargs):
    return DraftBuildService(profile, probe=PathProbe(), adapter=JsonAdapter(),
                             environment=kwargs.pop("environment", ReadyEnvironment()), **kwargs)


def _lock_worker(root, connection):
    try:
        with GenerationLock(root):
            connection.send("acquired")
    except FatalBuildError:
        connection.send("blocked")
    finally:
        connection.close()


def test_cross_process_lock_excludes_other_process_and_releases(tmp_path):
    context = mp.get_context("spawn")
    for locked in (True, False):
        receiver, sender = context.Pipe(False)
        lock = GenerationLock(tmp_path)
        if locked:
            lock.__enter__()
        process = context.Process(target=_lock_worker, args=(tmp_path, sender))
        try:
            process.start()
            sender.close()
            assert receiver.poll(20)
            assert receiver.recv() == ("blocked" if locked else "acquired")
        finally:
            process.join(10)
            if process.is_alive():
                process.terminate()
                process.join()
            if locked:
                lock.__exit__()
            receiver.close()


def test_selection_and_retry_do_not_duplicate_successes(profile, tmp_path):
    parent = tmp_path / "短剧"
    for index in (1, 2, 3):
        make_clips(parent / f"第{index}集", index)
    build = service(profile)
    result = build.build_batch(BatchBuildRequest(parent, (parent / "第2集",), "剧A-"))
    assert [i.requested_name for i in result.items] == ["剧A-第2集"]
    assert result.total_clips == 2
    content = json.loads((result.items[0].build_result.draft_dir / "draft_content.json").read_text(encoding="utf-8"))
    assert all(Path(v["path"]).parent == parent / "第2集" for v in content["materials"]["videos"])
    assert not (profile.draft_root / "第1集").exists()


def test_stop_finishes_current_episode_and_preserves_pending(profile, tmp_path):
    parent = tmp_path / "短剧"
    for index in (1, 2, 3):
        make_clips(parent / f"第{index}集", 1)
    stop = threading.Event()
    events = []
    def progress(event):
        events.append(event)
        if event.state.value == "processing":
            stop.set()
    result = service(profile).build_batch(BatchBuildRequest(parent), progress, stop)
    assert result.stopped
    assert [i.status for i in result.items] == [BatchItemStatus.SUCCESS,
        BatchItemStatus.NOT_PROCESSED, BatchItemStatus.NOT_PROCESSED]
    record = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert record["finished"] and record["stopped"]
    assert record["items"][0]["draft_id"]


def test_process_start_mid_batch_stops_without_installing_next(profile, tmp_path):
    parent = tmp_path / "短剧"
    for index in (1, 2, 3):
        make_clips(parent / f"第{index}集", 1)
    class ChangingEnvironment(ReadyEnvironment):
        commits = 0
        def ensure_commit_ready(self, _profile):
            self.commits += 1
            if self.commits > 2:
                raise EnvironmentCheckError("剪映正在运行")
    result = service(profile, environment=ChangingEnvironment()).build_batch(BatchBuildRequest(parent))
    assert result.succeeded_count == 1
    assert result.stopped
    assert result.items[2].status is BatchItemStatus.NOT_PROCESSED
    assert not (profile.draft_root / "第2集").exists()


def test_external_index_change_before_replace_is_not_overwritten(profile, tmp_path):
    clips = make_clips(tmp_path / "素材", 1)
    draft = profile.draft_root / "test"
    write_valid_draft(draft, clips, profile)
    validation = DraftValidator().validate(draft, clips, profile)
    root = profile.draft_root / profile.root_meta_name
    original = root.read_bytes()
    changed = json.dumps({"all_draft_store": [], "external": True}).encode()
    def conflict():
        root.write_bytes(changed)
    with pytest.raises(FatalBuildError, match="其他程序"):
        DraftRegistrar(tmp_path / "backups").register(draft_dir=draft, draft_name="test",
            validation=validation, profile=profile, expected_original=original, before_replace=conflict)
    assert root.read_bytes() == changed
    assert next((tmp_path / "backups").glob("*.bak")).read_bytes() == original


@pytest.mark.parametrize("phase,registered", [("preparing", False), ("installing", False),
                                               ("registering", False), ("registering", True)])
def test_crash_recovery_keeps_directories_and_reconciles(profile, tmp_path, phase, registered):
    clips = make_clips(tmp_path / "素材", 1)
    draft = profile.draft_root / "恢复测试"
    draft_id = write_valid_draft(draft, clips, profile)
    tasks = tmp_path / "tasks"
    journal = TaskJournal(tasks, profile.draft_root, [(clips[0].path.parent, "恢复测试")])
    journal.update(0, phase=phase, status="processing", draft_dir=str(draft), draft_id=draft_id)
    if registered:
        DraftRegistrar().register(draft_dir=draft, draft_name="恢复测试", profile=profile,
                                  validation=DraftValidator().validate(draft, clips, profile))
    before = {p.name: p.read_bytes() for p in draft.iterdir()}
    result = reconcile_records(tasks, profile)
    record = json.loads(journal.path.read_text(encoding="utf-8"))
    assert record["items"][0]["status"] == ("success" if registered else "needs_review")
    assert bool(result) is not registered
    assert {p.name: p.read_bytes() for p in draft.iterdir()} == before


def test_unresolved_episode_cannot_be_retried(profile, tmp_path):
    parent = tmp_path / "素材"
    make_clips(parent / "第1集", 1)
    build = service(profile)
    journal = TaskJournal(build.records_dir, profile.draft_root, [(parent / "第1集", "第1集")])
    journal.update(0, status="processing", phase="installing")
    result = build.build_batch(BatchBuildRequest(parent))
    assert result.items[0].status is BatchItemStatus.NEEDS_REVIEW
    assert not (profile.draft_root / "第1集").exists()


def _crash_worker(profile, source, state, phase):
    from ai_draft_builder import service as module
    original_update = TaskJournal.update
    def interrupted_update(journal, index, **fields):
        original_update(journal, index, **fields)
        if fields.get("phase") == phase:
            os._exit(77)
    TaskJournal.update = interrupted_update
    class CrashingRegistrar(DraftRegistrar):
        def register(self, **kwargs):
            result = super().register(**kwargs)
            if phase == "index_replaced":
                os._exit(77)
            return result
    build = module.DraftBuildService(profile, probe=PathProbe(), adapter=JsonAdapter(),
                                     environment=ReadyEnvironment(), state_dir=state,
                                     registrar=CrashingRegistrar())
    build.build(BuildRequest(source, "崩溃测试"))


@pytest.mark.parametrize("phase", ["preparing", "installing", "registering", "index_replaced"])
def test_actual_process_exit_during_transaction_is_recoverable(profile, tmp_path, phase):
    clips = make_clips(tmp_path / "素材", 1)
    state = tmp_path / "state"
    process = mp.get_context("spawn").Process(target=_crash_worker,
        args=(profile, clips[0].path.parent, state, phase))
    process.start()
    process.join(20)
    if process.is_alive():
        process.terminate()
        process.join()
        pytest.fail("崩溃测试进程未结束")
    assert process.exitcode == 77
    result = service(profile, state_dir=state).recover()
    record = json.loads(next((state / "tasks").glob("*.json")).read_text(encoding="utf-8"))
    assert record["items"][0]["status"] == ("success" if phase == "index_replaced" else "needs_review")
    assert bool(result) == (phase != "index_replaced")
    if phase in ("registering", "index_replaced"):
        assert (profile.draft_root / "崩溃测试").is_dir()


def test_source_changed_during_prepare_cleans_only_staging(profile, tmp_path):
    parent = tmp_path / "素材"
    clips = make_clips(parent, 1)
    class ChangingAdapter(JsonAdapter):
        def build(self, *args):
            result = super().build(*args)
            clips[0].path.write_bytes(b"file changed during build")
            return result
    build = DraftBuildService(profile, probe=PathProbe(), adapter=ChangingAdapter(), environment=ReadyEnvironment())
    with pytest.raises(MediaProbeError, match="发生变化"):
        build.build(BuildRequest(parent, "变化测试"))
    assert not (profile.draft_root / "变化测试").exists()
    assert not list((profile.draft_root / ".ai-draft-builder-staging").glob(".txn-*"))


@pytest.mark.parametrize("change", ["modify", "delete"])
def test_source_change_is_detected(tmp_path, change):
    video = make_clips(tmp_path / "素材", 1)[0].path
    snapshot = {video: fingerprint(video)}
    if change == "modify":
        video.write_bytes(b"new video bytes")
    else:
        video.unlink()
    with pytest.raises(MediaProbeError):
        verify_sources(snapshot)


def _stuck_worker(connection, *_args):
    connection.send(("progress", ("构建", "卡住.mp4")))
    time.sleep(30)


def test_worker_timeout_kills_native_processing(profile, tmp_path):
    clips = make_clips(tmp_path / "素材", 1)
    started = time.monotonic()
    with pytest.raises(DraftBuildError, match="没有进展"):
        ProcessPreparer(timeout=2, worker=_stuck_worker).prepare(
            [clips[0].path], tmp_path, "staging", tmp_path / "final", "test", profile, lambda *_: None)
    assert time.monotonic() - started < 15


def test_unreadable_episode_does_not_hide_other_episodes(tmp_path, monkeypatch):
    parent = tmp_path / "素材"
    make_clips(parent / "第1集", 1)
    make_clips(parent / "第2集", 1)
    real = Path.iterdir
    def iterdir(path):
        if path.name == "第1集":
            raise PermissionError("denied")
        return real(path)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    episodes = EpisodeBatchScanner().discover(parent)
    assert episodes[0].error
    assert episodes[1].video_count == 1


@pytest.mark.parametrize("names", [["第003集", "第001集", "第002集"], ["第10集", "第1集", "第2集"]])
def test_episode_numeric_order(tmp_path, names):
    for name in names:
        (tmp_path / name).mkdir()
    expected = ["第001集", "第002集", "第003集"] if "003" in names[0] else ["第1集", "第2集", "第10集"]
    assert [e.source_dir.name for e in EpisodeBatchScanner().discover(tmp_path)] == expected


def test_real_material_batch_has_distinct_references_and_retry(profile, tmp_path):
    import shutil
    root = Path(os.environ.get("AIDRAFT_TEST_MEDIA", str(Path(__file__).parents[1] / "test_materials/公开单集五镜头")))
    videos = sorted(root.glob("*.mp4"))
    if len(videos) < 2:
        pytest.skip("真实素材集不可用；发布验证需设置 AIDRAFT_TEST_MEDIA")
    parent = tmp_path / "真实分集"
    for number, video in enumerate(videos[:2], 1):
        episode = parent / f"第{number}集"
        episode.mkdir(parents=True)
        shutil.copyfile(video, episode / video.name)
    broken = parent / "第3集"
    broken.mkdir()
    (broken / "坏视频.mp4").write_bytes(b"broken")
    build = DraftBuildService(profile, environment=ReadyEnvironment())
    result = build.build_batch(BatchBuildRequest(parent))
    assert (result.succeeded_count, result.failed_count) == (2, 1)
    for item in result.items[:2]:
        draft = item.build_result.draft_dir
        content = json.loads((draft / profile.draft_content_name).read_text(encoding="utf-8"))
        assert all(Path(v["path"]).parent == item.source_dir for v in content["materials"]["videos"])
        assert content["duration"] == item.build_result.duration_us
    shutil.copyfile(videos[0], broken / "坏视频.mp4")
    second = build.build_batch(BatchBuildRequest(parent, (broken,)))
    assert second.succeeded_count == 1
    assert not (profile.draft_root / "第1集 (1)").exists()
