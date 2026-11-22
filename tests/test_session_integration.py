"""Resources created by a bound role belong to the bootstrap session too."""

import pytest

from agent_relay.bootstrap import Setups
from agent_relay.setup import initialize


def test_bootstrap_shutdown_closes_bound_changes_viewer(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    pair = tmp_path / "pair"
    initialize(project, pair, "127.0.0.1", 9443)
    viewer = None
    try:
        with Setups(tmp_path / "setups") as setups:
            receipt = setups.begin("requester", "chat-requester-123456")
            session = setups.session(receipt["setup_id"], "chat-requester-123456")
            session.reuse(str(pair / "requester/client.json"))
            bound = session.role_server()
            viewer = bound.changes_viewer
            viewer.link("disposable-mode", "disposable-task")
            thread = viewer.thread
            assert thread.is_alive()
            # Bootstrap intentionally leaves channel delivery disabled.
            assert not bound.watches.enabled
        assert viewer.closed
        assert not thread.is_alive()
        with pytest.raises(ValueError, match="closed"):
            viewer.link("disposable-mode", "disposable-task")
    finally:
        if viewer is not None:
            viewer.close()
