# 开发

[English](../en/development.md) | [中文](development.md)

## 目录结构

```
beastxpy/            package
  main.py            CLI registration and dispatch only
  api.py             the facade
  _contracts.py      schema ids, exit codes, JSON helpers
  config.py          Gate 0
  validator.py       Gate 1 and Gate 3
  model.py           XML assembly
  registry.py        the element/model registry
  runner.py          the seed matrix
  coverage/          one module per non-standard analysis family
  data/              capabilities.yaml, spec data
  tools/jars/        the official BEAST jars
deploy/              container definitions
docs/en, docs/zh     this documentation
examples/            the config matrices
tests/               pytest suite
tools/               validation matrices and helpers
```

`main.py` 不承载业务逻辑。一个命令就是一个 `beastxpy/cli_<name>.py` 模块，暴露 `register(subparsers)`；发现是自动的。

## 测试

```
python -m pytest -q
python tools/official_validate.py --matrix "examples/config_*.yaml" --mode java-core
python tools/validate_coverage.py
```

## 代码遵循的规则

* [`CONTRACT.md`](../../CONTRACT.md) 中的行为契约是规范性的：闸门状态、`assess` 的诚实性规则、数据检查严重程度。
* 兼容 Python 3.10 的类型写法，`from __future__ import annotations`。
* 每次读写文件都显式 `encoding="utf-8"`。
* `subprocess` 一律传参数列表，绝不用 `shell=True`。
* 所有路径用 `pathlib.Path`。
* 面向用户的错误必须带下一步建议。
* 算不出的诊断报告为不可用，绝不猜测。

## 增加一项能力

在 `beastxpy/data/capabilities.yaml` 增加一行，并补一个真正跑它的测试。不要在别处重复这张矩阵；文档、CLI 与测试都读这个文件。
