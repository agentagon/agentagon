# Extend trace normalization

This walkthrough shows how to extend an existing provider adapter without changing the canonical trace format. It connects an input fixture to validation and a saved measurement.

**Worked proposal, not shipped behavior:** accept a synthetic Braintrust export variant with `metrics.total_tokens` as a fallback for `metrics.tokens`. The current adapter reads `metrics.tokens`. This example does not assert that the additional field is part of Braintrust's API.

Use an editable development checkout as described in [CONTRIBUTING.md](../../CONTRIBUTING.md). Make the example changes on a separate branch or disposable copy. The documentation itself does not enable the new field.

## 1. Trace the existing behavior

Read the `braintrust` branch of `_native()` in [normalize.py](../../src/agentagon/telemetry/normalize.py). It maps provider fields into a common intermediate representation. `normalize()` converts those values into a canonical span.

The importer in [ingest.py](../../src/agentagon/telemetry/ingest.py) validates that span against [trace.json](../../contracts/v1/trace.json), groups it into a trace and calls [measure()](../../src/agentagon/core/signals.py). Measurements are retained with the audit and used in reports.

The existing `first()` helper chooses the first value that is not `None`. This preserves a reported zero. `number()` rejects invalid measurements; `normalize()` additionally rejects fractional token counts. Missing values remain `None`.

## 2. Describe the new behavior with a fixture test

Add this test to [tests/test_telemetry.py](../../tests/test_telemetry.py). That module already imports `json`, `pytest`, `normalize` and `validate_record`, and uses the `fixtures` fixture from [conftest.py](../../tests/conftest.py).

```python
@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({}, None),
        ({"total_tokens": 12}, 12),
        ({"tokens": 10, "total_tokens": 12}, 10),
        ({"tokens": 0, "total_tokens": 12}, 0),
        ({"tokens": None, "total_tokens": 12}, 12),
    ],
)
def test_braintrust_total_tokens_alias(fixtures, fields, expected):
    row = json.loads((fixtures / "braintrust.json").read_text())[1]
    row["metrics"].update(fields)
    span = normalize(row, "braintrust", "demo", "fixture")
    validate_record("trace", span, definition="span")
    assert span["usage"]["total_tokens"] == expected
```

Run only this new test:

```sh
python -m pytest tests/test_telemetry.py::test_braintrust_total_tokens_alias -q
```

Before the implementation change, the two fallback cases fail: the current code returns `None` for them. The missing-value, original-field and zero-precedence cases pass.

## 3. Change the provider mapping

In the `braintrust` branch of `_native()`, replace:

```python
"total_tokens": metrics.get("tokens"),
```

with:

```python
"total_tokens": first(metrics.get("tokens"), metrics.get("total_tokens")),
```

Run the focused test again. All five cases should now pass. The canonical span still uses `usage.total_tokens`, so this change needs neither a new schema field nor a reporting special case.

Keep the precedence explicit: a present `tokens` value wins, including zero. Do not use `or` as a fallback, and do not calculate a missing total by summing partial usage fields.

## 4. Verify the retained measurement

A mapping test alone does not prove that the importer keeps the field. Add this test to the same module; `import_traces` is already imported there.

```python
def test_braintrust_total_tokens_alias_in_report_input(workspace, imported, fixtures, tmp_path):
    rows = json.loads((fixtures / "braintrust.json").read_text())
    rows[1]["metrics"]["total_tokens"] = 12
    export = tmp_path / "alias.json"
    export.write_text(json.dumps(rows), encoding="utf-8")
    import_traces(workspace, imported, export)
    audit = workspace.read_audit(imported)
    usage = audit["traces"][0]["measurements"]["summary"]["usage"]
    assert usage["total_tokens"]["value"] == 12
    assert usage["total_tokens"]["basis"] == "provider_reported"
```

The `imported` fixture starts an audit with synthetic data and no host judgments. It can therefore accept changed trace input. Once analysis has begun, the importer requires a new audit to change evidence.

Run all six example cases together:

```sh
python -m pytest tests/test_telemetry.py -k total_tokens_alias -q
```

Also cover invalid fallback values such as negative numbers, strings, booleans and fractional tokens. They must be rejected rather than treated as zero or valid usage. The existing malformed-row tests show how import diagnostics preserve valid siblings and incomplete coverage.

## 5. Check the surrounding contract

Run the existing normalization and edge-case tests, then focused style checks:

```sh
python -m pytest tests/test_telemetry.py tests/test_edge_cases.py -q
python -m ruff check src/agentagon/telemetry/normalize.py tests/test_telemetry.py
python -m ruff format --check src/agentagon/telemetry/normalize.py tests/test_telemetry.py
```

For a real provider change, confirm the field against authoritative provider documentation or an appropriately redacted export. Update [accepted export formats](../../skills/audit/references/formats.md) with the exact mapping and precedence, and link relevant coverage from the [implementation map](capabilities.md#2-runtime-evidence).

An input alias can fit the existing canonical schema. A new provider, output field, rubric or record shape has wider implications: review provider choices, contracts, saved-state compatibility and packaging before changing them. Use [release preparation](releasing.md) and the [full checks](../../CONTRIBUTING.md#run-checks-before-submitting) before submitting a feature.
