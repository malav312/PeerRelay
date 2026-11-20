import importlib.util
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from agent_relay.filesystem import Filesystem
from agent_relay.schemas import Tool


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="peerrelay-approval-test-")
        self.addCleanup(temporary.cleanup)
        self.area = Path(temporary.name).resolve()
        # Subprocesses must exercise this checkout, not another editable install.
        environment = mock.patch.dict(
            os.environ, {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.root = self.area / "root"
        self.root.mkdir()
        # Test journals are separate from test registered roots and from live journals.
        self.fs = Filesystem(
            {"project": {"path": str(self.root), "mode": "rw"}}, self.area / "state"
        )
        self.target = self.root / "sample.py"
        self.target.write_text("def add(a, b):\n    return a - b\n")
        self.target.chmod(0o751)
        self.replacement = "def add(a, b):\n    return a + b\n"

    def tearDown(self):
        self.fs.close()

    def service(self):
        self.assertIsNotNone(
            importlib.util.find_spec("agent_relay.approvals"),
            "local approval service is not implemented",
        )
        from agent_relay.approvals import Approvals

        return Approvals(self.fs)

    def propose(self):
        current = self.fs.read(Tool(kind="read", root="project", path="sample.py"))
        ident = str(uuid.uuid4())
        payload = Tool(
            kind="propose_replace",
            root="project",
            path="sample.py",
            content=self.replacement,
            expected_version=current["version"],
        ).model_dump()
        self.assertEqual(self.fs.execute(ident, payload, {"project": "rw"})["status"], "proposed")
        return ident

    def test_inspect_and_apply_preserves_mode(self):
        ident = self.propose()
        service = self.service()
        view = service.inspect(ident)
        self.assertEqual(view["path"], "sample.py")
        self.assertIn("-    return a - b", view["diff"])
        self.assertIn("+    return a + b", view["diff"])
        self.assertEqual(view["proposed_content"], self.replacement)
        self.assertEqual(service.decide(ident, view["digest"], "approve")["status"], "applied")
        self.assertEqual(self.target.read_text(), self.replacement)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o751)

    def test_reject_is_terminal_and_changes_nothing(self):
        ident = self.propose()
        service = self.service()
        view = service.inspect(ident)
        before = self.target.stat()
        self.assertEqual(service.decide(ident, view["digest"], "reject")["status"], "rejected")
        self.assertEqual(service.decide(ident, view["digest"], "approve")["status"], "rejected")
        self.assertEqual(self.target.stat(), before)

    def test_stale_rejected(self):
        ident = self.propose()
        service = self.service()
        view = service.inspect(ident)
        self.target.write_text("user edit\n")
        self.assertEqual(service.decide(ident, view["digest"], "approve")["status"], "stale")
        self.assertEqual(self.target.read_text(), "user edit\n")

    def test_digest_mismatch_does_not_decide(self):
        ident = self.propose()
        service = self.service()
        with self.assertRaises(ValueError):
            service.decide(ident, "wrong", "approve")
        self.assertEqual(service.inspect(ident)["status"], "pending")

    def test_duplicate_approval_does_not_replace_again(self):
        ident = self.propose()
        service = self.service()
        view = service.inspect(ident)
        first = service.decide(ident, view["digest"], "approve")
        before = self.target.stat()
        self.assertEqual(service.decide(ident, view["digest"], "approve"), first)
        self.assertEqual(self.target.stat(), before)
        self.assertEqual([e["state"] for e in service.inspect(ident)["events"]].count("applied"), 1)

    def test_literal_backslash_newlines_are_not_normalized(self):
        self.replacement = r"def add(a, b):\n    return a + b\n"
        ident = self.propose()
        view = self.service().inspect(ident)
        self.assertEqual(view["proposed_content"], self.replacement)
        self.assertIn("\\\\n", view["proposed_escaped"])

    def test_symlink_swap_refused(self):
        ident = self.propose()
        service = self.service()
        view = service.inspect(ident)
        other = self.area / "outside.py"
        other.write_text("untouched")
        self.target.unlink()
        self.target.symlink_to(other)
        self.assertIn(
            service.decide(ident, view["digest"], "approve")["status"], ("stale", "rejected")
        )
        self.assertEqual(other.read_text(), "untouched")

    def test_hardlink_swap_refused(self):
        ident = self.propose()
        service = self.service()
        view = service.inspect(ident)
        os.link(self.target, self.root / "alias.py")
        self.assertIn(
            service.decide(ident, view["digest"], "approve")["status"], ("stale", "rejected")
        )
        self.assertIn("a - b", self.target.read_text())

    def test_parent_traversal_and_reserved_names_denied(self):
        for path in ("../outside.py", "/tmp/outside.py", ".peerrelay-apply-test"):
            payload = Tool(
                kind="propose_replace",
                root="project",
                path=path,
                content="bad",
                expected_version="bad",
            ).model_dump()
            self.assertEqual(
                self.fs.execute(str(uuid.uuid4()), payload, {"project": "rw"})["status"], "denied"
            )

    def test_reserved_create_denied(self):
        payload = Tool(
            kind="create_file",
            root="project",
            path=".peerrelay-apply-hidden",
            content="bad",
            expected_version="absent",
        ).model_dump()
        self.assertEqual(
            self.fs.execute(str(uuid.uuid4()), payload, {"project": "rw"})["status"], "denied"
        )
        self.assertFalse((self.root / ".peerrelay-apply-hidden").exists())

    def test_mutated_proposal_bytes_fail_integrity_check(self):
        ident = self.propose()
        service = self.service()
        view = service.inspect(ident)
        with self.fs.journal.connect() as db:
            binding = json.loads(
                db.execute("SELECT binding FROM local_proposals WHERE id=?", (ident,)).fetchone()[0]
            )
            binding["payload"]["content"] = "unapproved bytes"
            db.execute(
                "UPDATE local_proposals SET binding=? WHERE id=?", (json.dumps(binding), ident)
            )
        with self.assertRaises(ValueError):
            service.decide(ident, view["digest"], "approve")
        self.assertIn("a - b", self.target.read_text())

    def test_same_bytes_new_inode_is_stale(self):
        ident = self.propose()
        service = self.service()
        view = service.inspect(ident)
        content = self.target.read_text()
        self.target.unlink()
        self.target.write_text(content)
        self.assertEqual(service.decide(ident, view["digest"], "approve")["status"], "stale")

    def test_changed_staging_bytes_not_published(self):
        ident = self.propose()
        service = self.service()
        view = service.inspect(ident)

        sync = os.fsync

        def mutate(fd):
            sync(fd)
            next(self.root.glob(".peerrelay-apply-*")).write_text("unapproved")

        with mock.patch("agent_relay.approvals.os.fsync", side_effect=mutate):
            self.assertNotEqual(
                service.decide(ident, view["digest"], "approve")["status"], "applied"
            )
        self.assertIn("a - b", self.target.read_text())

    def test_purge_scrubs_proposal_and_prevents_approval(self):
        ident = self.propose()
        service = self.service()
        view = service.inspect(ident)
        self.fs.journal.purge([ident])
        self.assertEqual(service.decide(ident, view["digest"], "approve")["status"], "rejected")
        with self.fs.journal.connect() as db:
            row = db.execute(
                "SELECT binding,base_content FROM local_proposals WHERE id=?", (ident,)
            ).fetchone()
        self.assertNotIn("return", row[0])
        self.assertIsNone(row[1])

    def test_diff_without_final_newline_unambiguous(self):
        self.target.write_text("old")
        self.replacement = "new"
        ident = self.propose()
        view = self.service().inspect(ident)
        self.assertIn("-old\n", view["diff"])
        self.assertIn("+new\n", view["diff"])
        self.assertIn("No newline", view["diff"])

    def test_base_content_tampering_fails(self):
        ident = self.propose()
        service = self.service()
        view = service.inspect(ident)
        with self.fs.journal.connect() as db:
            db.execute(
                "UPDATE local_proposals SET base_content=? WHERE id=?", ("misleading", ident)
            )
        with self.assertRaises(ValueError):
            service.inspect(ident)
        with self.assertRaises(ValueError):
            service.decide(ident, view["digest"], "approve")

    def test_casefolded_reserved_create_denied(self):
        payload = Tool(
            kind="create_file",
            root="project",
            path=".PEERRELAY-APPLY-hidden",
            content="bad",
            expected_version="absent",
        ).model_dump()
        self.assertEqual(
            self.fs.execute(str(uuid.uuid4()), payload, {"project": "rw"})["status"], "denied"
        )

    def test_missing_root_still_allows_status_and_rejection(self):
        ident = self.propose()
        service = self.service()
        view = service.inspect(ident)
        self.fs.close()
        self.root.rename(self.area / "moved-root")
        self.fs = Filesystem(
            {"project": {"path": str(self.root), "mode": "rw"}},
            self.area / "state",
            allow_unavailable_roots=True,
        )
        service = self.service()
        self.assertFalse(service.inspect(ident)["current_matches"])
        self.assertEqual(service.decide(ident, view["digest"], "reject")["status"], "rejected")

    def test_journal_failure_after_replace_is_uncertain(self):
        ident = self.propose()
        service = self.service()
        view = service.inspect(ident)
        transition = service._transition

        def fail(row, state, detail):
            if state == "applied":
                raise sqlite3.OperationalError("simulated journal failure")
            return transition(row, state, detail)

        service._transition = fail
        self.assertEqual(service.decide(ident, view["digest"], "approve")["status"], "uncertain")
        service._transition = transition
        self.assertEqual(service.inspect(ident)["status"], "uncertain")
        self.assertEqual(self.target.read_text(), self.replacement)

    def test_exclusive_stage_collision_preserves_existing_file(self):
        ident = self.propose()
        service = self.service()
        view = service.inspect(ident)
        collision = self.root / ".peerrelay-apply-collision"
        collision.write_text("do not remove")
        with mock.patch("agent_relay.approvals.secrets.token_hex", return_value="collision"):
            self.assertEqual(
                service.decide(ident, view["digest"], "approve")["status"], "uncertain"
            )
        self.assertEqual(collision.read_text(), "do not remove")
        self.assertIn("a - b", self.target.read_text())

    def test_root_symlink_still_allows_status_without_application(self):
        ident = self.propose()
        service = self.service()
        view = service.inspect(ident)
        self.fs.close()
        moved = self.area / "moved-root"
        self.root.rename(moved)
        self.root.symlink_to(moved)
        self.fs = Filesystem(
            {"project": {"path": str(self.root), "mode": "rw"}},
            self.area / "state",
            allow_unavailable_roots=True,
        )
        service = self.service()
        self.assertFalse(service.inspect(ident)["current_matches"])
        self.assertEqual(service.decide(ident, view["digest"], "approve")["status"], "rejected")

    def child(self, ident, digest, crash=None):
        script = """
import os,sys
from agent_relay.filesystem import Filesystem
from agent_relay.approvals import Approvals
fs=Filesystem({'project':{'path':sys.argv[1],'mode':'rw'}},sys.argv[2])
service=Approvals(fs)
if sys.argv[5]!='none':
    replace=os.replace
    def interrupted_replace(*args, **kwargs):
        if sys.argv[5]=='before_replace': os._exit(73)
        replace(*args, **kwargs)
        if sys.argv[5]=='after_replace': os._exit(73)
    os.replace=interrupted_replace
print(service.decide(sys.argv[3],sys.argv[4],'approve')['status'],flush=True)
"""
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(self.root),
                str(self.area / "state"),
                ident,
                digest,
                crash or "none",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_two_processes_apply_once(self):
        ident = self.propose()
        service = self.service()
        view = service.inspect(ident)
        children = [self.child(ident, view["digest"]) for _ in range(2)]
        for p in children:
            out, err = p.communicate(timeout=15)
            self.assertEqual(p.returncode, 0, err)
            self.assertEqual(out.strip(), "applied")
        self.assertEqual(
            [e["state"] for e in service.inspect(ident)["events"]].count("applying"), 1
        )

    def crash_case(self, stage, expected_content):
        ident = self.propose()
        service = self.service()
        view = service.inspect(ident)
        p = self.child(ident, view["digest"], stage)
        _, err = p.communicate(timeout=15)
        self.assertEqual(p.returncode, 73, err)
        self.fs.close()
        self.fs = Filesystem(
            {"project": {"path": str(self.root), "mode": "rw"}}, self.area / "state"
        )
        service = self.service()
        self.assertEqual(service.inspect(ident)["status"], "uncertain")
        before = self.target.stat()
        self.assertEqual(service.decide(ident, view["digest"], "approve")["status"], "uncertain")
        self.assertEqual(self.target.stat(), before)
        self.assertEqual(self.target.read_text(), expected_content)

    def test_crash_before_replace_never_replayed(self):
        self.crash_case("before_replace", self.target.read_text())

    def test_crash_after_replace_never_replayed(self):
        self.crash_case("after_replace", self.replacement)


if __name__ == "__main__":
    unittest.main()
