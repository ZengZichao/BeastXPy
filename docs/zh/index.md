# BeastXPy 0.1.0

[English](../en/index.md) | [中文](index.md)

BeastXPy 用于生成、校验并诊断 **BEAST 1** 分歧时间分析。它把一份严格的 YAML 分析描述转成 BEAST XML，用官方的 BEAST 1.10.4 与 BEAST X 10.5.0 jar 校验该文档，运行种子矩阵，并帮助你判断 MCMC 输出说明了什么。

它是一层克制、有明确取向的封装，而不是 BEAST 的替代品：

* **严格配置。** 未知键直接报错而不是被忽略；先验名字写错，在 BEAST 启动前就会被拦下。
* **四道闸门。** Gate 0 解析配置，Gate 1 做 XML 结构校验，Gate 2 检查标定，Gate 3 用官方 BEAST jar 实际解析并实例化。
* **诚实的诊断。** `assess` 报告 ESS、ACT、PSRF 与 ASDSF；输入不足时写 `not_computable`，绝不编造数值。
* **可复现产物。** 指纹、环境锁、单文件 HTML 报告，以及 Snakemake/Nextflow 流水线。

## 作者

BeastXPy 由 **曾子超（ZengZichao）** 开发与维护（zengzichao@sjtu.edu.cn，ORCID [0000-0001-6553-970X](https://orcid.org/0000-0001-6553-970X)）。

## BeastXPy 不做什么

它不判断模型是否适合你的数据，不宣称收敛，也不替你选模型。`compare` 会计算边际似然，但如何解释贝叶斯因子是你的工作。参见 [能力边界](limitations.md)。

## 定位

BeastXPy 是围绕 BEAST 1 / BEAST X 构建的独立工作流层；它不是 BEAST 开发者的产品，也与后者无隶属关系。与你可能在用的工具的关系：

| 工具 | 目标平台 | 功能 | BeastXPy 的关系 |
|---|---|---|---|
| [BEAUti](https://github.com/beast-dev/beast-mcmc)（Drummond 等 2012） | BEAST 1 与 BEAST 2 | 交互式构建 BEAST XML 的图形界面 | 用严格、可评审的 YAML 取代图形界面；`list-models` 列出支持的每个模型 |
| [babette](https://cran.r-project.org/package=babette) | BEAST 2 | 通过 beautier 写 BEAST 2 XML、通过 beastier 运行的 R 包 | 不同平台上的同类自动化目标；其 XML 模式、包和解析器与 BEAST 1 不通用 |
| [BEASTifier](https://github.com/josephwb/BEASTifier) | BEAST 1 | 为模拟实验批量生成 XML 的 C++ 工具 | 互补的批量生成器；没有验证闸门、诊断、锁定或报告 |
| [RevBayes](https://revbayes.github.io/)（Höhna 等 2016） | RevBayes | 交互式模型描述语言与推断引擎 | 另一种推断平台，不是 BEAST XML 生成器 |
| [BEASTling](https://github.com/lmaurits/BEASTling) | BEAST 2 | 面向语言学的配置文件式 XML 生成 | 同样的配置文件思路，但面向 BEAST 1 / BEAST X、以系统发育学为主，并带验证门 |
| [Tracer](https://github.com/beast-dev/tracer)（Rambaut 等 2018） | BEAST 1 与 BEAST 2 | 汇总 MCMC 日志的桌面应用 | `assess`/`trace` 无头复现 ESS/ACT/HPD，并新增跨种子 PSRF/ASDSF；见 [references](references.md) |
| LogCombiner / TreeAnnotator（BEAST） | BEAST 1 | 日志/树后处理工具 | 由 `logcombine`/`tree-annotate` 封装，种子矩阵无需手动操作 |
| [BEAGLE](https://github.com/beagle-dev/beagle-lib)（Ayres 等 2012） | BEAST 1 与 BEAST 2 | 似然计算库 | 自动探测、核对版本并暴露给 `run` |

我们未能核实 "SnowBEAST" 这一名称的权威公开仓库，因此不将其列为可比工具；上表只列有可核实公开文档的工具。

### 为什么选择 BEAST 1 / BEAST X

BEAST 2 是新分析的主流平台，但它是另一个程序：不同的 XML 模式、包管理器，以及不同的解析器与算子生态。BEAST 1 XML 不能由 BEAST 2 加载，重新实现 BEAST 2 扩展包会是另一个项目。BeastXPy 有意面向 BEAST 1 谱系：

* **既有分析。** 许多已发表的分化时间研究使用 BEAST 1.10.4 或其延续版本 BEAST X 10.5.0。重跑、审计或扩展这些分析需要该谱系，而不是翻译成 BEAST 2。
* **仍属于 BEAST 1 扩展包的模型。** EBSP、StarBEAST1、随机局部钟、经验密码子模型与 MDS 属于 BEAST 1 生态；BeastXPy 的能力矩阵明确列出所支持的子集。
* **可审计的等价性。** 随包分发的官方 jar 让 Gate 3 用用户实际运行的同一 BEAST 构建来验证生成的文档，无需额外安装。

这里主张的新颖性不是"YAML 转 XML"，而是四道验证闸门、随包官方 jar、诚实的多链诊断与锁文件级可复现性在同一条可审计流水线中的组合。目标用户是已有 BEAST 1 / BEAST X 分析的研究组、需要为期刊投稿准备可审计验证证据的实验室，以及复现遗留 BEAST 1 模型的方法开发者。BEAST 2 支持不在范围内，也不作承诺；见 [能力边界](limitations.md)。

本文提到的每个统计量与模型的一手文献：[references](references.md)。

## 文档

工作流页面按顺序阅读；参考页面各自独立。

| 页面 | 内容 |
|---|---|
| [installation](installation.md) | 分步安装：Python、JDK、jar、原生 BEAGLE |
| [quickstart](quickstart.md) | 全流程命令与预期输出：配置 -> 预检 -> 生成 -> 运行 -> 评估 -> 报告 -> 锁定 |
| [configuration](configuration.md) | 每个 YAML 键、模型名、标定分布 |
| [cli](cli.md) | 全部 21 个命令、参数、退出码与 JSON 模式 |
| [validation](validation.md) | 四道闸门各自证明了什么 |
| [native-beagle](native-beagle.md) | BEAGLE 安装、版本配对、`run` 如何使用它 |
| [pipelines](pipelines.md) | Snakemake 与 Nextflow 输出 |
| [assessment](assessment.md) | ESS/ACT/PSRF/ASDSF 及如何解读 |
| [reporting](reporting.md) | 离线 HTML 报告 |
| [compare](compare.md) | 路径采样、步进 stone 与贝叶斯因子 |
| [reproducibility](reproducibility.md) | 指纹、锁文件与重放 |
| [extensions](extensions.md) | 自定义 XML 片段 |
| [container](container.md) | Docker 与 Apptainer |
| [troubleshooting](troubleshooting.md) | 症状、原因、修复 |
| [api](api.md) | Python 门面 |
| [development](development.md) | 测试、目录结构、贡献规则 |
| [release](release.md) | 切版与发布：版本升级、PyPI、Zenodo |
| [limitations](limitations.md) | 当前能力边界与路线图 |
| [references](references.md) | 每个统计量、阈值与模型的一手文献 |
| [faq](faq.md) | 简短问答 |

命令行是主要接口。关于"什么能用"的每一条结论都有能力矩阵支撑，你可以自己打印：

```
beastxpy capabilities
```
