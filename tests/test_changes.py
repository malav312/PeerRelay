import json
import os

import pytest

from agent_relay.service import Denied, Relay


@pytest.fixture
def change_task(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "edited.txt").write_text("user edit\nkeep\n")
    (root / "removed.txt").write_text("old\n")
    (root / "untouched.txt").write_text("preexisting change\n")
    clock = [1000.0]
    relay = Relay(root, tmp_path / "state", clock=lambda: clock[0], grant="exec")
    mode = relay.call("requester", "requester_mode", {"execution": True})["mode_id"]
    lease = relay.call("responder", "response_mode", {})["lease_id"]
    task = relay.call(
        "requester",
        "submit",
        {
            "mode_id": mode,
            "operation_id": "first",
            "prompt": "Edit files",
        },
    )["task_id"]
    relay.call("responder", "claim", {"lease_id": lease})
    yield relay, root, clock, mode, lease, task
    relay.close()


def finish(relay, lease, task):
    return relay.call(
        "responder",
        "complete",
        {
            "lease_id": lease,
            "task_id": task,
            "summary": "Done",
        },
    )


def get_changes(relay, mode, task):
    return relay.call("requester", "changes", {"mode_id": mode, "task_id": task})


def test_task_baseline_counts_created_deleted_and_no_status_diff(change_task):
    relay, root, _, mode, lease, task = change_task
    (root / "edited.txt").write_text("task edit\nkeep\n")
    (root / "removed.txt").unlink()
    (root / "created.txt").write_text("new\n")
    # Claim retries must not recapture the baseline after execution started.
    relay.call("responder", "claim", {"lease_id": lease})
    finish(relay, lease, task)
    status = relay.call("requester", "status", {"mode_id": mode, "task_id": task})
    assert status["changes"]["files_changed"] == 3
    assert status["changes"]["additions"] == 2
    assert status["changes"]["deletions"] == 2
    assert "task edit" not in json.dumps(status)
    assert "edited.txt" not in json.dumps(status)
    artifact = get_changes(relay, mode, task)
    assert {f["path"] for f in artifact["files"]} == {
        "edited.txt",
        "removed.txt",
        "created.txt",
    }
    edited = next(f for f in artifact["files"] if f["path"] == "edited.txt")
    assert "-user edit" in edited["diff"] and "+task edit" in edited["diff"]
    assert artifact["scope"] == "changes_during_task"
    (root / "edited.txt").write_text("later unrelated edit\n")
    assert get_changes(relay, mode, task) == artifact


def test_changes_preserve_path_policy_and_never_follow_links(change_task, tmp_path):
    relay, root, _, mode, lease, task = change_task
    outside = tmp_path / "outside"
    outside.write_text("outside sentinel\n")
    (root / "alias").symlink_to(outside)
    os.link(outside, root / "hardlink")
    (root / ".env").write_text("secret sentinel")
    (root / "hidden.txt").write_text("ignored sentinel")
    (root / ".peerrelayignore").write_text("hidden.txt\n")
    (root / "created.txt").write_text("allowed\n")
    finish(relay, lease, task)
    artifact = get_changes(relay, mode, task)
    assert [f["path"] for f in artifact["files"]] == ["created.txt"]
    (root / ".peerrelayignore").write_text("hidden.txt\ncreated.txt\n")
    assert get_changes(relay, mode, task)["files"] == []


def test_binary_and_oversized_files_are_reported_without_content(change_task):
    relay, root, _, mode, lease, task = change_task
    (root / "binary.bin").write_bytes(b"secret\x00binary")
    (root / "large.txt").write_text("large sentinel" * 30000)
    finish(relay, lease, task)
    artifact = get_changes(relay, mode, task)
    assert artifact["files_changed"] == 2
    assert artifact["omitted_files"] == 2
    assert {f["omitted"] for f in artifact["files"]} == {"binary", "size_limit"}
    assert "sentinel" not in json.dumps(artifact)
    assert artifact["additions"] == 0 and artifact["deletions"] == 0


def test_owned_artifacts_expire_and_mode_exit_revokes(change_task):
    relay, root, clock, mode, lease, task = change_task
    (root / "created.txt").write_text("new\n")
    finish(relay, lease, task)
    other = relay.call("requester", "requester_mode", {})["mode_id"]
    with pytest.raises(Denied):
        get_changes(relay, other, task)
    with pytest.raises(Denied):
        relay.call("responder", "changes", {"mode_id": mode, "task_id": task})
    clock[0] += 86401
    assert get_changes(relay, mode, task)["state"] == "unavailable"
    relay.call("requester", "exit_mode", {"mode_id": mode})
    with pytest.raises(Denied):
        get_changes(relay, mode, task)


def test_cancel_discards_baseline_and_never_captures_later_edits(change_task):
    relay, root, _, mode, _, task = change_task
    relay.call("requester", "cancel", {"mode_id": mode, "task_id": task})
    (root / "created.txt").write_text("unrelated after cancel\n")
    assert get_changes(relay, mode, task)["state"] == "unavailable"
    assert (
        relay.db.execute("SELECT baseline FROM task_changes WHERE task=?", (task,)).fetchone()[0]
        is None
    )


def test_restart_keeps_frozen_review_but_drops_unfinished_baseline(change_task):
    relay, root, clock, mode, lease, task = change_task
    (root / "created.txt").write_text("finished\n")
    finish(relay, lease, task)
    expected = get_changes(relay, mode, task)
    second = relay.call(
        "requester",
        "submit",
        {
            "mode_id": mode,
            "operation_id": "second",
            "prompt": "Another task",
        },
    )["task_id"]
    relay.call("responder", "claim", {"lease_id": lease})
    relay.close()
    resumed = Relay(root, root.parent / "state", clock=lambda: clock[0], grant="exec")
    try:
        assert get_changes(resumed, mode, task) == expected
        assert get_changes(resumed, mode, second)["state"] == "unavailable"
        assert (
            resumed.db.execute(
                "SELECT baseline FROM task_changes WHERE task=?",
                (second,),
            ).fetchone()[0]
            is None
        )
    finally:
        resumed.close()


def test_root_replacement_denies_stored_artifact(change_task):
    relay, root, _, mode, lease, task = change_task
    (root / "created.txt").write_text("private artifact\n")
    finish(relay, lease, task)
    root.rename(root.with_name("old-project"))
    root.mkdir()
    assert get_changes(relay, mode, task)["state"] == "unavailable"


def test_incomplete_scan_does_not_report_unvisited_files_deleted(change_task, monkeypatch):
    from agent_relay import changes

    relay, root, _, mode, lease, task = change_task
    monkeypatch.setattr(changes, "MAX_ENTRIES", 1)
    (root / "created.txt").write_text("new\n")
    finish(relay, lease, task)
    assert get_changes(relay, mode, task)["state"] == "unavailable"


def test_baseline_policy_cannot_be_removed_to_expose_preexisting_ignored_file(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".peerrelayignore").write_text("hidden.txt\n")
    (root / "hidden.txt").write_text("private sentinel")
    relay = Relay(root, tmp_path / "state", grant="exec")
    try:
        mode = relay.call("requester", "requester_mode", {"execution": True})["mode_id"]
        lease = relay.call("responder", "response_mode", {})["lease_id"]
        task = relay.call(
            "requester",
            "submit",
            {
                "mode_id": mode,
                "operation_id": "first",
                "prompt": "Edit files",
            },
        )["task_id"]
        relay.call("responder", "claim", {"lease_id": lease})
        (root / ".peerrelayignore").unlink()
        finish(relay, lease, task)
        assert get_changes(relay, mode, task)["files_changed"] == 0
    finally:
        relay.close()


def test_proposal_only_mode_does_not_attribute_external_edits(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    relay = Relay(root, tmp_path / "state")
    try:
        mode = relay.call("requester", "requester_mode", {})["mode_id"]
        lease = relay.call("responder", "response_mode", {})["lease_id"]
        task = relay.call(
            "requester",
            "submit",
            {
                "mode_id": mode,
                "operation_id": "first",
                "prompt": "Propose a change",
            },
        )["task_id"]
        relay.call("responder", "claim", {"lease_id": lease})
        (root / "created.txt").write_text("other writer\n")
        finish(relay, lease, task)
        assert get_changes(relay, mode, task)["state"] == "unavailable"
    finally:
        relay.close()


def test_dependency_cache_tree_does_not_exhaust_source_review_budget(change_task, monkeypatch):
    from agent_relay import changes

    relay, root, _, mode, lease, task = change_task
    monkeypatch.setattr(changes, "MAX_ENTRIES", 8)
    deps = root / "node_modules"
    deps.mkdir()
    for index in range(12):
        (deps / str(index)).write_text("dependency content")
    (root / "created.txt").write_text("source change\n")
    finish(relay, lease, task)
    review = get_changes(relay, mode, task)
    assert review["state"] == "available"
    assert [f["path"] for f in review["files"]] == ["created.txt"]


def test_artifact_count_is_bounded_per_relay(change_task, monkeypatch):
    from agent_relay import changes

    relay, root, _, mode, lease, task = change_task
    monkeypatch.setattr(changes, "MAX_ARTIFACTS", 2, raising=False)
    finish(relay, lease, task)
    for index in range(2):
        other = relay.call(
            "requester",
            "submit",
            {
                "mode_id": mode,
                "operation_id": str(index),
                "prompt": "Edit",
            },
        )["task_id"]
        relay.call("responder", "claim", {"lease_id": lease})
        (root / "created.txt").write_text(str(index))
        finish(relay, lease, other)
    assert get_changes(relay, mode, task)["state"] == "unavailable"
