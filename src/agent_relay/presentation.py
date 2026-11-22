"""Small client-facing views; durable task and permission state stays in the relay."""

from collections import Counter


def question_message(question):
    message = question["message"]
    choices = question.get("choices") or []
    if choices:
        message += "\n\n" + "\n".join(
            f"{number}. {choice}" for number, choice in enumerate(choices, 1)
        )
    return message


def task_view(result, *, details=False):
    view = dict(result)
    state = result["state"]
    view["display"] = (
        "Need your input."
        if result.get("questions")
        else "Done."
        if state == "succeeded"
        else {
            "queued": "Queued.",
            "running": "Working.",
            "cancelled": "Cancelled.",
            "interrupted": "Interrupted.",
            "failed": "Failed.",
        }.get(state, state)
    )
    if not details:
        commands = view.pop("commands", [])
        view["command_counts"] = dict(Counter(item["state"] for item in commands))
        # Keep all failures and unresolved outcomes visible. Successful history is
        # available through task_status(details=True), not echoed on every wait.
        view["command_issues"] = [
            item
            for item in commands
            if item["state"] not in ("queued", "starting", "running")
            and not (item["state"] == "exited" and item["exit_code"] == 0)
        ]
        view["questions"] = [
            {key: item[key] for key in ("question_id", "message", "choices")}
            for item in result.get("questions", [])
        ]
    return view
