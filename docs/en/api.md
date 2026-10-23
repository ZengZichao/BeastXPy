# Python API

[English](api.md) | [中文](../zh/api.md)

The command line is the supported interface; the Python facade mirrors it for notebooks and scripts.

```
from beastxpy import BeastXPy

bxp = BeastXPy()
xml = bxp.generate_xml("config.yaml", beast_validate=True)
result = bxp.generate_xml_result("config.yaml", beast_validate=True)
print(result.documents[0].gate3.status)
```

## Methods

| Method | Purpose |
|---|---|
| `parse_config(path)` | Gate 0 |
| `generate_xml(path, ...)` | generate, return the primary document |
| `generate_xml_result(path, ...)` | generate, return every document and gate result |
| `validate_xml(path, beast=True)` | Gate 1, optionally Gate 3 |
| `diagnose(...)` | Gate 2 detail |
| `run_analysis(...)` | the seed matrix |
| `analyze_trace(...)` | trace statistics |
| `annotate_tree(...)`, `combine_logs(...)` | tree and log post-processing |
| `generate_methods(path)`, `generate_fingerprint(path)` | manuscript artefacts |
| `generate_pipeline(...)` | Snakemake or Nextflow text |
| `check_environment()` | probe Java, jars, Python |

## Errors

`run_analysis(..., parallel=N)` uses the same concurrent seed execution as `beastxpy run --parallel N`.

`GateError` carries the gate name, the findings and a message. `ContractError` carries a `fix` attribute with the next step. Both are safe to show a user.

## Output-oriented commands

`assess`, `check-data`, `report`, `compare`, `lock`, `reproduce`, `capabilities` and `container` are available as modules and as CLI commands. They are not wrapped as `BeastXPy` methods, because they operate on outputs rather than on a configuration.
