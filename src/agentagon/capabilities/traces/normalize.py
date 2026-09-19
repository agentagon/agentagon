"""Adapters for provider-neutral span and message-part contracts.

Supported profiles are native span/run/observation JSON records, their documented
list/trace wrappers, and OTLP ExportTraceServiceRequest JSON. No network access.
"""

import json
import re
from collections.abc import Iterator
from typing import Any

from agentagon.core.records import AuditError, digest, identifier, number, timestamp_ns

SECRET_KEYS = re.compile(
    r"^(?:(?:braintrust|langfuse|langsmith|phoenix)[_-])?(?:api[_-]?key|secret[_-]?key|access[_-]?token|authorization|password|client_secret)$",
    re.I,
)
SECRET_VALUES = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._~+/-]{12,})")


def redact(value: Any) -> Any:
    """Remove common credential fields/patterns; this is not a general PII detector."""
    if isinstance(value, dict):
        return {k: "[REDACTED]" if SECRET_KEYS.match(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str) and value.strip().startswith(("{", "[")):
        decoded = decode(value)
        if isinstance(decoded, (dict, list)):
            cleaned = redact(decoded)
            if cleaned != decoded:
                return json.dumps(cleaned, ensure_ascii=False)
    return SECRET_VALUES.sub("[REDACTED]", value) if isinstance(value, str) else value


def decode(value: Any) -> Any:
    if isinstance(value, str) and value.strip().startswith(("{", "[")):
        try:
            return json.loads(value, parse_constant=_bad_number)
        except (json.JSONDecodeError, AuditError):
            return value
    return value


def _bad_number(value: str) -> None:
    raise AuditError("non-finite JSON number")


def mapping(value: Any) -> dict[str, Any]:
    value = decode(value)
    return value if isinstance(value, dict) else {}


def first(*values: Any) -> Any:
    return next((v for v in values if v is not None), None)


def otel_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if "arrayValue" in value:
        return [otel_value(v) for v in value["arrayValue"].get("values", [])]
    if "kvlistValue" in value:
        return attributes(value["kvlistValue"].get("values", []))
    for key in ("stringValue", "intValue", "doubleValue", "boolValue", "bytesValue"):
        if key in value:
            return int(value[key]) if key == "intValue" else value[key]
    return value


def attributes(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {
            v["key"]: otel_value(v.get("value"))
            for v in value
            if isinstance(v, dict) and "key" in v
        }
    return {}


def unpack(
    value: Any, provider: str, locator: str = "$", context: dict | None = None
) -> Iterator[tuple[dict, str]]:
    """Flatten supported wrappers without discarding resource/scope context."""
    context = context or {}
    if isinstance(value, list):
        for index, item in enumerate(value):
            try:
                yield from unpack(item, provider, f"{locator}[{index}]", context)
            except (AuditError, TypeError, KeyError, AttributeError):
                yield {"_invalid_record": True}, f"{locator}[{index}]"
        return
    if not isinstance(value, dict):
        raise AuditError(f"{locator}: expected an object")
    if "resourceSpans" in value:
        if provider not in {"otlp", "phoenix"}:
            raise AuditError("OTLP envelope does not match selected provider")
        for ri, resource in enumerate(value["resourceSpans"]):
            if not isinstance(resource, dict):
                yield {"_invalid_record": True}, f"{locator}.resourceSpans[{ri}]"
                continue
            for si, scope in enumerate(resource.get("scopeSpans", [])):
                if not isinstance(scope, dict):
                    yield (
                        {"_invalid_record": True},
                        f"{locator}.resourceSpans[{ri}].scopeSpans[{si}]",
                    )
                    continue
                ctx = {
                    "_resource": attributes(resource.get("resource", {}).get("attributes")),
                    "_scope": scope.get("scope", {}),
                }
                yield from unpack(
                    scope.get("spans", []),
                    provider,
                    f"{locator}.resourceSpans[{ri}].scopeSpans[{si}].spans",
                    ctx,
                )
        return
    keys = {
        "braintrust": ("data", "traces", "spans"),
        "langfuse": ("data", "observations", "traces"),
        "langsmith": ("data", "runs", "traces", "child_runs"),
        "phoenix": ("data", "traces", "spans"),
        "otlp": (),
    }[provider]
    is_row = any(k in value for k in ("span_id", "spanId", "run_type", "parentObservationId"))
    is_row = is_row or (
        provider == "phoenix" and bool(mapping(value.get("context")).get("span_id"))
    )
    is_row = is_row or (provider == "langfuse" and "id" in value and "traceId" in value)
    if is_row:
        row = {**context, **value}
        yield row, locator
        if provider == "langsmith" and isinstance(value.get("child_runs"), list):
            yield from unpack(
                value["child_runs"],
                provider,
                locator + ".child_runs",
                {"trace_id": value.get("trace_id", value.get("id"))},
            )
        return
    for key in keys:
        if isinstance(value.get(key), list):
            ctx = dict(context)
            if key in {"spans", "observations"}:
                ctx["_trace_id"] = first(value.get("trace_id"), value.get("id"))
                ctx["_trace_context"] = {
                    name: field for name, field in value.items() if name != key
                }
            yield from unpack(value[key], provider, locator + "." + key, ctx)
            return
    raise AuditError(f"{locator}: unsupported {provider} export shape")


def message_parts(content: Any) -> list[dict]:
    if content is None:
        return []
    if not isinstance(content, list):
        return [{"kind": "text", "value": content}]
    result = []
    for part in content:
        if not isinstance(part, dict):
            result.append({"kind": "text", "value": part})
            continue
        kind = part.get("type", part.get("kind", "text"))
        if kind in {"tool_use", "tool_call"}:
            result.append(
                {
                    "kind": "tool_call",
                    "tool_name": part.get("name"),
                    "tool_call_id": part.get("id"),
                    "arguments": first(part.get("input"), part.get("arguments")),
                }
            )
        elif kind in {"tool_result", "function_result", "tool_call_response"}:
            result.append(
                {
                    "kind": "tool_result",
                    "tool_call_id": first(part.get("tool_use_id"), part.get("id")),
                    "value": first(part.get("content"), part.get("response")),
                    "is_error": part.get("is_error"),
                }
            )
        elif kind in {"thinking", "reasoning"}:
            result.append(
                {"kind": "reasoning", "value": first(part.get("thinking"), part.get("text"))}
            )
        elif kind in {"image", "image_url", "input_image", "audio", "file"}:
            result.append({"kind": "media", "value": part})
        else:
            result.append({"kind": kind, "value": first(part.get("text"), part.get("value"), part)})
    return result


def messages(value: Any, prefix: str, *, output: bool = False) -> list[dict]:
    value = decode(value)
    if isinstance(value, dict):
        if "messages" in value:
            value = value["messages"]
        elif "choices" in value:
            value = [v.get("message", v) for v in value["choices"]]
        elif "generations" in value:
            value = [
                v
                for group in value["generations"]
                for v in (group if isinstance(group, list) else [group])
            ]
        else:
            value = (
                [value] if any(k in value for k in ("role", "type", "kwargs", "message")) else []
            )
    if not isinstance(value, list):
        return []
    result = []
    # LangSmith serializes message batches and LangChain constructor messages.
    flattened = [
        item for group in value for item in (group if isinstance(group, list) else [group])
    ]
    for index, item in enumerate(flattened):
        if not isinstance(item, dict):
            continue
        item = mapping(first(item.get("message"), item))
        constructor = item.get("id") if item.get("type") == "constructor" else None
        constructor_role = None
        if isinstance(constructor, list) and constructor:
            constructor_role = {
                "HumanMessage": "user",
                "AIMessage": "assistant",
                "AIMessageChunk": "assistant",
                "SystemMessage": "system",
                "ToolMessage": "tool",
                "FunctionMessage": "tool",
            }.get(constructor[-1])
        item = mapping(first(item.get("kwargs"), item))
        role = item.get("role", item.get("type", "assistant" if output else "unknown"))
        role = constructor_role or role
        role = {"human": "user", "ai": "assistant", "function": "tool"}.get(role, role)
        parts = message_parts(first(item.get("content"), item.get("parts"), item.get("text")))
        for call in item.get(
            "tool_calls", mapping(item.get("additional_kwargs")).get("tool_calls", [])
        ):
            function = mapping(call.get("function", call))
            parts.append(
                {
                    "kind": "tool_call",
                    "tool_call_id": call.get("id"),
                    "tool_name": function.get("name"),
                    "arguments": decode(first(function.get("arguments"), function.get("args"))),
                }
            )
        if item.get("refusal"):
            parts.append({"kind": "refusal", "value": item["refusal"]})
        result.append(
            {
                "id": f"{prefix}/{index}",
                "provider_id": item.get("id"),
                "role": role,
                "parts": parts,
                "tool_call_id": item.get("tool_call_id"),
            }
        )
    return result


def _kind(value: Any) -> str:
    return {
        "generation": "llm",
        "chat": "llm",
        "embedding": "llm",
        "embeddings": "llm",
        "chat_model": "llm",
        "chain": "task",
        "retriever": "task",
        "retrieval": "task",
        "invoke_agent": "agent",
        "execute_tool": "tool",
        "workflow": "task",
    }.get(str(value).lower(), str(value or "generic").lower())


def _status(value: Any, error: Any) -> str:
    if error not in (None, "", False):
        return "error"
    if isinstance(value, dict):
        value = value.get("code")
    if value in (2, "ERROR", "error", "failure", "failed"):
        return "error"
    if value in (1, "OK", "ok", "success", "successful", "completed"):
        return "ok"
    return "unknown"


def _native(row: dict, provider: str) -> dict:
    meta = mapping(row.get("metadata"))
    if provider == "braintrust":
        metrics = mapping(row.get("metrics"))
        attr = mapping(row.get("span_attributes"))
        return dict(
            trace_id=first(row.get("root_span_id"), row.get("span_id")),
            span_id=row.get("span_id"),
            parents=row.get("span_parents", []),
            kind=attr.get("type"),
            start=first(metrics.get("start"), row.get("created")),
            end=metrics.get("end"),
            input=row.get("input"),
            output=row.get("output"),
            error=row.get("error"),
            status=attr.get("status"),
            model=first(meta.get("model"), row.get("model")),
            usage={
                "input_tokens": first(metrics.get("prompt_tokens"), metrics.get("input_tokens")),
                "output_tokens": first(
                    metrics.get("completion_tokens"), metrics.get("output_tokens")
                ),
                "total_tokens": metrics.get("tokens"),
                "cache_read_tokens": metrics.get("prompt_cached_tokens"),
                "cost_usd": first(metrics.get("cost"), metrics.get("total_cost")),
            },
            session_id=first(meta.get("session_id"), meta.get("thread_id")),
            attributes=attr,
            tool_call_id=meta.get("tool_call_id"),
            name=first(attr.get("name"), row.get("name")),
        )
    if provider == "langfuse":
        usage = mapping(first(row.get("usageDetails"), row.get("usage")))
        costs = mapping(row.get("costDetails"))
        return dict(
            trace_id=first(row.get("traceId"), row.get("_trace_id")),
            span_id=row.get("id"),
            parents=[row["parentObservationId"]] if row.get("parentObservationId") else [],
            kind=row.get("type"),
            start=row.get("startTime"),
            end=row.get("endTime"),
            input=decode(row.get("input")),
            output=decode(row.get("output")),
            error=row.get("statusMessage") if row.get("level") == "ERROR" else None,
            status="error" if row.get("level") == "ERROR" else row.get("status"),
            model=first(row.get("providedModelName"), row.get("model")),
            usage={
                "input_tokens": first(
                    row.get("inputUsage"), usage.get("input"), usage.get("promptTokens")
                ),
                "output_tokens": first(
                    row.get("outputUsage"), usage.get("output"), usage.get("completionTokens")
                ),
                "total_tokens": usage.get("total"),
                "cache_read_tokens": first(
                    usage.get("cache_read_input_tokens"), usage.get("input_cached_tokens")
                ),
                "cost_usd": first(
                    costs.get("total"), row.get("totalCost"), row.get("calculatedTotalCost")
                ),
            },
            session_id=first(
                row.get("sessionId"),
                meta.get("session_id"),
                mapping(row.get("traceContext")).get("sessionId"),
                mapping(row.get("_trace_context")).get("sessionId"),
            ),
            tool_call_id=meta.get("tool_call_id"),
            name=row.get("name"),
            attributes=meta,
        )
    if provider == "langsmith":
        extra = mapping(row.get("extra"))
        metadata = mapping(extra.get("metadata"))
        invocation = mapping(extra.get("invocation_params"))
        return dict(
            trace_id=first(row.get("trace_id"), row.get("id")),
            span_id=row.get("id"),
            parents=row.get("parent_run_ids")
            or ([row["parent_run_id"]] if row.get("parent_run_id") else []),
            kind=row.get("run_type"),
            start=row.get("start_time"),
            end=row.get("end_time"),
            input=decode(row.get("inputs")),
            output=decode(row.get("outputs")),
            error=row.get("error"),
            status=row.get("status")
            or ("ok" if row.get("end_time") and row.get("error") is None else "unknown"),
            model=first(metadata.get("ls_model_name"), invocation.get("model")),
            usage={
                "input_tokens": row.get("prompt_tokens"),
                "output_tokens": row.get("completion_tokens"),
                "total_tokens": row.get("total_tokens"),
                "cost_usd": _decimal_cost(row.get("total_cost")),
            },
            session_id=first(
                metadata.get("session_id"),
                metadata.get("thread_id"),
                metadata.get("conversation_id"),
            ),
            tool_call_id=first(
                metadata.get("tool_call_id"), mapping(row.get("inputs")).get("tool_call_id")
            ),
            name=row.get("name"),
            attributes=metadata,
        )
    return _otel(row)


def _decimal_cost(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as exc:
            raise AuditError("invalid decimal cost") from exc
    return value


def _indexed_messages(attr: dict, direction: str) -> list[dict]:
    prefix = f"llm.{direction}_messages."
    grouped: dict[str, dict] = {}
    for key, value in attr.items():
        if key.startswith(prefix):
            index, _, field = key[len(prefix) :].partition(".")
            grouped.setdefault(index, {})[field.removeprefix("message.")] = value
    for message in grouped.values():
        calls: dict[str, dict] = {}
        for field, value in list(message.items()):
            match = re.fullmatch(
                r"tool_calls\.(\d+)\.tool_call\.(id|function\.name|function\.arguments)", field
            )
            if match:
                index, name = match.groups()
                call = calls.setdefault(index, {"function": {}})
                if name == "id":
                    call["id"] = value
                else:
                    call["function"][name.removeprefix("function.")] = value
        if calls:
            message["tool_calls"] = [calls[key] for key in sorted(calls, key=int)]
    return [grouped[k] for k in sorted(grouped, key=lambda v: int(v) if v.isdigit() else 0)]


def _otel(row: dict) -> dict:
    attr = attributes(row.get("attributes"))
    ctx = mapping(row.get("context"))
    parent = first(row.get("parentSpanId"), row.get("parent_id"), row.get("parent_span_id"))
    if isinstance(parent, dict):
        parent = first(parent.get("span_id"), parent.get("spanId"))
    return dict(
        trace_id=first(
            row.get("traceId"), row.get("trace_id"), ctx.get("trace_id"), row.get("_trace_id")
        ),
        span_id=first(row.get("spanId"), row.get("span_id"), ctx.get("span_id")),
        parents=[parent] if parent and parent != "0000000000000000" else [],
        kind=first(
            attr.get("openinference.span.kind"),
            attr.get("gen_ai.operation.name"),
            row.get("span_kind"),
        ),
        start=first(
            row.get("startTimeUnixNano"),
            row.get("start_time_unix_nano"),
            row.get("start_time"),
            row.get("startTime"),
        ),
        end=first(
            row.get("endTimeUnixNano"),
            row.get("end_time_unix_nano"),
            row.get("end_time"),
            row.get("endTime"),
        ),
        nanos="startTimeUnixNano" in row or "start_time_unix_nano" in row,
        input=decode(
            first(attr.get("gen_ai.input.messages"), attr.get("input.value"), row.get("input"))
        ),
        output=decode(
            first(attr.get("gen_ai.output.messages"), attr.get("output.value"), row.get("output"))
        ),
        input_messages=_indexed_messages(attr, "input"),
        output_messages=_indexed_messages(attr, "output"),
        error=attr.get("exception.message"),
        status=first(row.get("status"), row.get("status_code")),
        model=first(attr.get("gen_ai.request.model"), attr.get("llm.model_name")),
        usage={
            "input_tokens": first(
                attr.get("gen_ai.usage.input_tokens"), attr.get("llm.token_count.prompt")
            ),
            "output_tokens": first(
                attr.get("gen_ai.usage.output_tokens"), attr.get("llm.token_count.completion")
            ),
            "total_tokens": attr.get("llm.token_count.total"),
            "cache_read_tokens": attr.get("gen_ai.usage.cache_read.input_tokens"),
            "cost_usd": first(attr.get("llm.cost.total"), attr.get("gen_ai.usage.cost")),
        },
        session_id=first(attr.get("session.id"), attr.get("gen_ai.conversation.id")),
        name=row.get("name"),
        tool_call_id=first(attr.get("tool.call.id"), attr.get("gen_ai.tool.call.id")),
        attributes=attr,
    )


def normalize(row: dict, provider: str, project: str, raw_ref: str) -> dict:
    if row.get("_invalid_record"):
        raise AuditError("malformed record")
    native = _native(row, provider)
    for key in ("span_id", "trace_id"):
        if not isinstance(native.get(key), str) or not native[key].strip():
            raise AuditError(f"missing {key}")
    if not isinstance(native["parents"], list) or any(
        not isinstance(v, str) for v in native["parents"]
    ):
        raise AuditError("invalid parent span IDs")
    if provider == "otlp":
        if not re.fullmatch(r"[0-9a-fA-F]{32}", native["trace_id"]) or not re.fullmatch(
            r"[0-9a-fA-F]{16}", native["span_id"]
        ):
            raise AuditError("OTLP IDs must be hexadecimal (32 trace / 16 span characters)")
        if int(native["trace_id"], 16) == 0 or int(native["span_id"], 16) == 0:
            raise AuditError("OTLP IDs must be nonzero")
        native["trace_id"] = native["trace_id"].lower()
        native["span_id"] = native["span_id"].lower()
        native["parents"] = [parent.lower() for parent in native["parents"]]
    trace_key = identifier("trace", provider, project, native["trace_id"])
    span_key = identifier("span", trace_key, native["span_id"])
    kind = _kind(native.get("kind"))
    kind = kind if kind in {"agent", "task", "llm", "tool"} else "generic"
    incoming = messages(native.get("input_messages") or native.get("input"), span_key + "/input")
    outgoing = messages(
        native.get("output_messages") or native.get("output"), span_key + "/output", output=True
    )
    usage = {key: number(value) for key, value in native["usage"].items()}
    if any(
        value is not None and int(value) != value
        for key, value in usage.items()
        if key.endswith("tokens")
    ):
        raise AuditError("token counts must be integers")
    return {
        "span_id": native["span_id"],
        "trace_id": native["trace_id"],
        "id": span_key,
        "trace_key": trace_key,
        "parent_span_ids": native["parents"],
        "kind": kind,
        "name": native.get("name") or "",
        "operation": native.get("kind") or "unspecified",
        "status": _status(native.get("status"), native.get("error")),
        "error": native.get("error"),
        "started_ns": timestamp_ns(native.get("start"), nanos=native.get("nanos", False)),
        "ended_ns": timestamp_ns(native.get("end"), nanos=native.get("nanos", False)),
        "input": native.get("input"),
        "output": native.get("output"),
        "messages": incoming + outgoing,
        "tool_call_id": native.get("tool_call_id"),
        "session_id": native.get("session_id"),
        "model": native.get("model"),
        "usage": usage,
        "attributes": native.get("attributes", {}),
        "metadata": mapping(row.get("metadata")),
        "resource": row.get("_resource", row.get("resource", {})),
        "scope": row.get("_scope", row.get("scope", {})),
        "events": row.get("events", []),
        "links": row.get("links", []),
        "raw_ref": raw_ref,
        "source_digest": digest(row),
    }
