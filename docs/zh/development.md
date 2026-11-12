# 开发

[English](../en/development.md) | [中文](development.md)

## 目录结构

下面按流水线阶段列出 `beastxpy/` 下的每个模块；`cli_<name>.py` 一项列出的是那八个自注册模块。

```
beastxpy/
  __init__.py        the version string
  main.py            CLI registration and dispatch, no analysis logic
  api.py             the facade every command goes through
  _contracts.py      schema ids, exit codes, JSON helpers
  utils.py           shared helpers
  config.py          Gate 0: strict YAML parsing
  partition.py       Gate 0: partition checks
  datacheck.py       pre-flight data and calibration checks
  sequence.py        sequence reading and validation
  models.py          data model definitions
  calibration.py     calibration assembly
  validator.py       Gate 1 (pure Python) and Gate 3 (official jars)
  diagnostics.py     Gate 2 calibration diagnostics and diagnose
  model.py           XML assembly
  xml_writer.py      canonical BEAST 1 XML serialisation
  fragments.py       user-supplied XML spliced into the document
  registry.py        the element/model registry
  runner.py          the seed matrix
  assessment.py      ESS/ACT/PSRF/ASDSF for assess
  tracestats.py      trace statistics for trace
  treetools.py       tree-annotate and logcombine
  compare.py         marginal-likelihood model comparison
  report.py          the self-contained HTML report
  lockfile.py        environment lock files
  reproducibility.py fingerprints, LaTeX methods, pipelines
  capabilities.py    the capability matrix
  coverage/          one module per non-standard analysis family
  cli_<name>.py      the commands that self-register: assess,
                     capabilities, check-data, compare, container, lock,
                     report, reproduce
  data/              capabilities.yaml, xml_spec.json
  tools/             the BEAST validator harness and the official jars
deploy/              container definitions
docs/en, docs/zh     this documentation
examples/            the config matrices
tests/               pytest suite
tools/               validation matrices and helpers
```

`main.py` 不承载分析逻辑：它解析参数、调用 `api.py` 或某个模块，然后打印输出。命令要么在 `main.py` 中注册，要么由一个暴露 `register(subparsers)` 的 `beastxpy/cli_<name>.py` 模块注册；后者会被自动发现，因此新增命令从不需要改动 `main.py`。

## 测试

```
python -m pytest -q
python tools/official_validate.py --matrix "examples/config_*.yaml" --mode java-core
python tools/validate_coverage.py
```

## 代码遵循的规则

* [`CONTRACT.md`](../../CONTRACT.md) 中的行为契约是规范性的：Gate 状态、`assess` 的诚实性规则、数据检查严重程度。
* 兼容 Python 3.10 的类型写法，`from __future__ import annotations`。
* 每次读写文件都显式 `encoding="utf-8"`。
* `subprocess` 一律传参数列表，绝不用 `shell=True`。
* 所有路径用 `pathlib.Path`。
* 面向用户的错误都带下一步建议。
* 无法计算的诊断项报告为不可用，绝不猜测。

## 增加一项能力

在 `beastxpy/data/capabilities.yaml` 增加一行，并补一个真正跑它的测试。不要在别处重复这张矩阵；文档、CLI 与测试都读这个文件。
