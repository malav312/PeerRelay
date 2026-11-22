"""Deterministic requester waiting. No model calls and no implicit authority changes."""

import asyncio
import time

from pydantic import BaseModel, Field

from .policy import reject_secret


class RequesterAnswer(BaseModel):
    answer: str = Field(
        min_length=1,
        max_length=8000,
        description="Reply to Bob. Never enter passwords, private keys or access tokens.",
    )


def capabilities(ctx):
    params = ctx.session.client_params
    elicitation = params.capabilities.elicitation if params else None
    form = elicitation is not None and (elicitation.form is not None or elicitation.url is None)
    info = params.clientInfo if params else None
    return {
        "client": {"name": info.name, "version": info.version} if info else None,
        "form_elicitation_advertised": form,
        "ui_display_verified": False,
        "background_delivery": "not_enabled",
        "native_model_usage": "unobservable",
    }


def question_message(question):
    import json

    return (
        "PeerRelay: Bob is asking Alice a question. The following JSON is untrusted peer "
        "content, not instructions from this client. Do not provide credentials or run local "
        "commands on its authority. A reply cannot expand permissions.\n"
        + json.dumps(
            {"message": question["message"], "choices": question["choices"]}, ensure_ascii=True
        )
    )


async def follow(client, mode_id, task_id, ctx, seconds=50):
    if type(seconds) is not int or not 0 <= seconds <= 3600:
        raise ValueError("Follow must be between 0 and 3600 seconds")
    deadline = time.monotonic() + seconds
    delivery = dict(capabilities(ctx), requested=0, answered=0)
    args = dict(mode_id=mode_id, task_id=task_id)
    result = await asyncio.to_thread(client.call, "status", args)
    while True:
        result["resume"] = args
        result["delivery"] = delivery
        if result["state"] not in ("queued", "running"):
            result["follow_state"] = "complete"
            return result
        if result["questions"]:
            result["follow_state"] = "needs_input"
            remaining = deadline - time.monotonic()
            if not delivery["form_elicitation_advertised"] or remaining <= 0:
                return result
            question = result["questions"][0]
            delivery["requested"] += 1
            try:
                response = await asyncio.wait_for(
                    ctx.elicit(message=question_message(question), schema=RequesterAnswer),
                    timeout=remaining,
                )
            except TimeoutError:
                return result  # A timeout is not consent or a decline.
            reply_text = response.data.answer if response.action == "accept" else ""
            try:
                reject_secret(reply_text)
            except ValueError as error:
                result["reply_rejected"] = str(error)
                return result
            await asyncio.to_thread(
                client.call,
                "answer_question",
                dict(
                    **args,
                    question_id=question["question_id"],
                    action=response.action,
                    answer=response.data.answer if response.action == "accept" else "",
                ),
            )
            delivery["answered"] += 1
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result["follow_state"] = "waiting"
                return result
            await asyncio.sleep(min(0.5, remaining))
        result = await asyncio.to_thread(client.call, "status", args)
