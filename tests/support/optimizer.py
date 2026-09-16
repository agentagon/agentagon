"""Offline native-host responses to the actual upstream reflection prompt."""


def candidate_text(request):
    payload = request["payload"]
    if payload.get("protocol") == "gepa-reflection-v1":
        return (
            payload["prompt"]
            .split("## Current Component\n", 1)[1]
            .split("```\n", 1)[1]
            .split("\n```", 1)[0]
        )
    return payload["candidate"]


def proposal_response(request, text):
    if request["payload"].get("protocol") == "gepa-reflection-v1":
        return {"text": "```\n" + text + "\n```"}
    return {"candidate": text}
