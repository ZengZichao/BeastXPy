# Python API

[English](../en/api.md) | [中文](api.md)

命令行是受支持的接口；Python 门面为 notebook 与脚本提供同样的能力。

```
from beastxpy import BeastXPy

bxp = BeastXPy()
xml = bxp.generate_xml("config.yaml", beast_validate=True)
result = bxp.generate_xml_result("config.yaml", beast_validate=True)
print(result.documents[0].gate3.status)
```

## 方法

| 方法 | 用途 |
|---|---|
| `parse_config(path)` | Gate 0 |
| `generate_xml(path, ...)` | 生成并返回主文档 |
| `generate_xml_result(path, ...)` | 生成并返回所有文档与闸门结果 |
| `validate_xml(path, beast=True)` | Gate 1，可选 Gate 3 |
| `diagnose(...)` | Gate 2 细节 |
| `run_analysis(...)` | 种子矩阵 |
| `analyze_trace(...)` | trace 统计 |
| `annotate_tree(...)`、`combine_logs(...)` | 树与日志后处理 |
| `generate_methods(path)`、`generate_fingerprint(path)` | 论文用产物 |
| `generate_pipeline(...)` | Snakemake 或 Nextflow 文本 |
| `check_environment()` | 探测 Java、jar、Python |

## 错误

`run_analysis(..., parallel=N)` 与 `beastxpy run --parallel N` 使用同一套并发种子执行。

`GateError` 携带闸门名、发现项与消息；`ContractError` 携带 `fix` 属性说明下一步。两者都可以直接展示给用户。

## 面向输出的命令

`assess`、`check-data`、`report`、`compare`、`lock`、`reproduce`、`capabilities` 与 `container` 以模块和 CLI 命令的形式提供。它们没有被包装成 `BeastXPy` 方法，因为它们作用于产物而不是配置。
