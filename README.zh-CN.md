# BeastXPy

[![CI](https://github.com/ZengZichao/BeastXPy/actions/workflows/ci.yml/badge.svg)](https://github.com/ZengZichao/BeastXPy/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/beastxpy)](https://pypi.org/project/beastxpy/)
[![Python](https://img.shields.io/pypi/pyversions/beastxpy)](https://pypi.org/project/beastxpy/)
[![License](https://img.shields.io/badge/license-MIT_%2B_LGPL--2.1-blue)](./LICENSE)
[![ORCID](https://img.shields.io/badge/ORCID-0000--0001--6553--970X-a6ce39?logo=orcid&logoColor=white)](https://orcid.org/0000-0001-6553-970X)

BeastXPy 用于生成、校验并诊断 **BEAST 1** 分歧时间分析。它把一份严格的 YAML 描述转成 BEAST XML，用随包分发的官方 BEAST 1.10.4 与 BEAST X 10.5.0 jar 校验该文档（Gate 3；各模式分别证明了什么见“验证”一节），运行种子矩阵，并如实报告 MCMC 输出呈现出的样子。

版本 0.1.0。需要 Python 3.10+。

## 安装

```
python -m pip install beastxpy
beastxpy --version
```

两个 BEAST jar 随包提供，因此 Gate 3 无需另外安装 BEAST。校验与运行需要 JDK（`JAVA_HOME`）；真实似然需要原生 BEAGLE（`BEASTX_BEAGLE_1_10_4_LIB`、`BEASTX_BEAGLE_10_5_0_LIB`）。若从源码检出工作，改在该目录运行 `python -m pip install .`。分步安装说明见 [docs/zh/installation.md](docs/zh/installation.md)。

## 六命令工作流

```
beastxpy generate --config examples/config_subst_jc69.yaml --output out.xml --beast-validate
beastxpy check-data --config examples/config_subst_jc69.yaml
beastxpy run --config examples/config_subst_jc69.yaml --workdir runs/demo --seeds 1,2
beastxpy assess runs/demo --json runs/demo/assessment.json
beastxpy report runs/demo --output runs/demo/report.html --lang zh
beastxpy lock --config examples/config_subst_jc69.yaml --output beastxpy.lock
```

这六个是日常工作流；`beastxpy --help` 列出全部 21 个命令（`quick`、`validate`、`trace`、`tree-annotate`、`logcombine`、`compare`、`pipeline`、`reproduce` 等）。`beastxpy capabilities` 打印权威的功能 × 版本矩阵。

## 它不做什么

BeastXPy 不宣称收敛、不做模型选择，也不判断模型是否适合你的数据。`assess` 是诊断工具；`compare` 计算边际似然，解释留给你。参见 [docs/zh/limitations.md](docs/zh/limitations.md)。

## 平台范围与同类工具

BeastXPy 面向 BEAST 1 谱系（1.10.4）及其延续版本 BEAST X 10.5.0，而不是 BEAST 2。BEAST 2 是另一个程序，XML 模式与扩展包生态都不同；既有 BEAST 1 分析和模型（EBSP、StarBEAST1、随机局部钟、经验密码子模型、MDS）需要该谱系。这里主张的新颖性是四道验证闸门、随包官方 jar、诚实多链诊断与锁文件级可复现性的组合，而不是"YAML 转 XML"本身。与 BEAUti、babette（BEAST 2）、BEASTifier、RevBayes 和 BEASTling 的对比见 [docs/zh/index.md#定位](docs/zh/index.md#定位)；英文概览见 [README.md](README.md) 与 [docs/en/index.md](docs/en/index.md)。

## 文档

中文：[docs/zh/index.md](docs/zh/index.md) · [English](README.md) · [docs/en/index.md](docs/en/index.md)

包含安装、快速上手、配置、完整 CLI 参考、四道校验闸门、原生 BEAGLE、流水线、运行后评估、报告、模型比较、可复现性、自定义 XML 片段、容器、故障排查、Python API、开发说明、能力边界与常见问题。

## 验证

仓库自带三项可复现检查：

```
python -m pytest -q
python tools/official_validate.py --matrix "examples/config_*.yaml" --mode java-core
python tools/validate_coverage.py
```

验证证据是分层的。本 README 与 `beastxpy capabilities` 中的结论，除非明确写出 `beagle`，否则一律是 `java-core` 证据：

| 模式 | 解析器 | 似然 | 能证明什么 |
|---|---|---|---|
| `parse`、`java-core` | 官方 BEAST 解析器 + BeastXPy 的 `TreeLikelihood` shim | 不计算（仅构建） | XML 被接受、模型图可以构建 |
| `beagle` | 完整官方解析器集合 | 真实原生 hmsbeagle 似然 | 完整的解析器 + 似然校验 |

只有 `beagle` 才是完整的官方验证。凡是准备在论文中声明的平台与 BEAST 版本，都应在该平台运行全示例矩阵，并报告“平台 × BEAST 版本 × 示例数”：

```
python tools/official_validate.py --matrix "examples/config_*.yaml" \
    --mode beagle --versions 1.10.4 10.5.0
```

模式细节见 [docs/zh/validation.md](docs/zh/validation.md)，其中记录了实际的“平台 × 版本 × 示例数”矩阵（验证机上 BEAST 1.10.4 + BEAGLE 3.1.2 与 BEAST X 10.5.0 + BEAGLE 4.0.1 的 `beagle` 模式均为 16/16）。

没有 JDK 的机器上，`pytest -q` 会有意让 Gate-3 用例失败（fail-not-skip 是被元测试锁定的政策）。只跑不依赖 Java 的子集请用：

```
python -m pytest -q -m "not java"
```

CI 在 3 个操作系统 × 2 个 Python 版本 × 2 个 BEAST 版本上运行完整套件，并额外运行从源码构建 BEAGLE 的 `beagle` 证据作业与 `xml_spec.json` 字节级漂移检查。

## 数据可用性

`examples/` 下的比对是用于跑通工作流的短参考序列，本仓库没有产生新的实证数据；它们不用于生物学推断。论文所用的验证记录见 [docs/zh/validation.md](docs/zh/validation.md)。端到端参考数据案例（配置、生成的 XML、锁文件、评估与报告）在 `examples/empirical/`；它使用同一份随包教程比对，并明确只是工作流演示，不是生物学结论。

## 作者

BeastXPy 由 **曾子超（ZengZichao）** 开发与维护 —zengzichao@sjtu.edu.cn · ORCID [0000-0001-6553-970X](https://orcid.org/0000-0001-6553-970X)。

## 许可

MIT AND LGPL-2.1-only。BEAST 本身是 LGPL-2.1-only，jar 内保留其 `META-INF/LICENSE.txt` 与 `META-INF/NOTICE.txt`。参见 `LICENSE` 与 `NOTICE`。
