#!/usr/bin/env python3
"""Deterministic acceptance demo: real disposable relay, scripted native turns.

No native executable, credentials, network or model provider is used. Scripted
turns replace native inference only; all delivery and relay state is real code.
"""

import asyncio
import json
import tempfile
from pathlib import Path

from agent_relay.delivery import Binding, DeliveryController, RelayMonitor
from agent_relay.service import Relay


class LocalRole:
    def __init__(self, relay, role):
        self.relay, self.role = relay, role

    def call(self, method, params):
        return self.relay.call(self.role, method, params)


class ScriptedNative:
    def __init__(self, thread_id):
        self.thread_id, self.turns = thread_id, []

    async def call(self, method, params):
        assert params["threadId"] == self.thread_id
        if method == "thread/read":
            return {
                "thread": {"id": self.thread_id, "status": {"type": "idle"}, "turns": self.turns}
            }
        assert method == "turn/start"
        turn = {"id": f"turn-{len(self.turns)}", "status": "completed"}
        self.turns.append(turn)
        return {"turn": turn}


async def demo(directory):
    project = directory / "project"
    project.mkdir()
    relay = Relay(project, directory / "relay", grant="ro")
    controllers = []
    try:
        requester, responder = LocalRole(relay, "requester"), LocalRole(relay, "responder")
        mode = requester.call("requester_mode", {})["mode_id"]
        lease = responder.call("response_mode", {})["lease_id"]
        requester_native, responder_native = (
            ScriptedNative("requester"),
            ScriptedNative("responder"),
        )
        a = DeliveryController.create(
            directory / "requester.sqlite",
            Binding("requester", "gen-a", "requester", mode_id=mode),
            requester_native,
        )
        controllers.append(a)
        b = DeliveryController.create(
            directory / "responder.sqlite",
            Binding("responder", "gen-b", "responder", lease_id=lease),
            responder_native,
        )
        controllers.append(b)
        am, bm = RelayMonitor(a, requester), RelayMonitor(b, responder)
        for _ in range(20):
            await am.poll()
            await bm.poll()
            assert not await a.dispatch() and not await b.dispatch()
        idle_turns = len(requester_native.turns) + len(responder_native.turns)
        task = requester.call(
            "submit", {"mode_id": mode, "operation_id": "demo-task", "prompt": "Choose a separator"}
        )["task_id"]
        am.watch_task(task)  # Future MCP binding registers the returned exact task ID.
        await bm.poll()
        assert await b.dispatch()
        assert responder.call("claim", {"lease_id": lease})["task_id"] == task
        question = responder.call(
            "ask_question",
            {
                "lease_id": lease,
                "task_id": task,
                "operation_id": "demo-question",
                "message": "Which separator?",
                "choices": ["Any string", "One character"],
            },
        )["question_id"]
        await am.poll()
        assert await a.dispatch()
        await bm.poll()
        assert not await b.dispatch()
        requester.call(
            "answer_question",
            {
                "mode_id": mode,
                "task_id": task,
                "question_id": question,
                "action": "accept",
                "answer": "custom separator",
            },
        )
        await bm.poll()
        assert await b.dispatch()
        reply = responder.call(
            "question_status", {"lease_id": lease, "task_id": task, "question_id": question}
        )
        responder.call("complete", {"lease_id": lease, "task_id": task, "summary": "Done."})
        await am.poll()
        assert await a.dispatch()
        for _ in range(20):
            await am.poll()
            await bm.poll()
            assert not await a.dispatch() and not await b.dispatch()
        status = requester.call("status", {"mode_id": mode, "task_id": task})
        responder.call("disconnect", {"lease_id": lease})
        await bm.poll()
        assert not await b.dispatch()
        return {
            "idle_turns": idle_turns,
            "responder_turns": len(responder_native.turns),
            "requester_turns": len(requester_native.turns),
            "question_answer": reply["answer"],
            "task_state": status["state"],
            "after_disconnect": b.state,
            "provider_calls": 0,
        }
    finally:
        for controller in controllers:
            controller.close()
        relay.close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="peerrelay-demo-") as directory:
        print(json.dumps(asyncio.run(demo(Path(directory).resolve())), indent=2))
