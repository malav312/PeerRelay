"""Small client-facing views; durable task and permission state stays in the relay."""

from collections import Counter

from .outcomes import completion_view


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
    completion = completion_view(result.get("completion"))
    commands = result.get("commands", [])
    # Preserve failures even when the responder reports successful verification.
    issues = [
        item
        for item in commands
        if item["state"] not in ("queued", "starting", "running")
        and not (item["state"] == "exited" and item["exit_code"] == 0)
    ]
    attention = bool(issues) or (
        completion is not None
        and (
            completion["status"] != "completed"
            or completion["verification"] in ("failed", "not_run")
            or completion["limitations"]
        )
    )
    if result.get("questions"):
        display = "Need your input."
    elif state == "succeeded":
        display = "Needs attention." if attention else "Done."
        changes = result.get("changes", {})
        if changes.get("state") == "available":
            count = changes["files_changed"]
            noun = "file" if count == 1 else "files"
            display += f" {count} {noun} changed, +{changes['additions']}/-{changes['deletions']}."
    else:
        display = {
            "queued": "Queued.",
            "running": "Working.",
            "cancelled": "Cancelled.",
            "interrupted": "Interrupted.",
            "failed": "Failed.",
        }.get(state, state)
    view["display"] = display
    if not details:
        if completion is not None:
            view.pop("summary", None)
            view["completion"] = completion
        view.pop("commands", None)
        view["command_counts"] = dict(Counter(item["state"] for item in commands))
        # Successful history is available through task_status(details=True).
        view["command_issues"] = issues
        view["questions"] = [
            {key: item[key] for key in ("question_id", "message", "choices")}
            for item in result.get("questions", [])
        ]
    return view
