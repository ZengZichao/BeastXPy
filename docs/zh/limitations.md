# 能力边界

[English](../en/limitations.md) | [中文](limitations.md)

本页说明 BeastXPy **不**做什么。这是最重要的一页。

## 推断

* **不宣称收敛。** `assess` 报告诊断量。`pass` 只表示没有越过配置的阈值，仅此而已。
* **不做模型选择。** `compare` 计算边际似然；如何解释是你的工作。
* **不做模型批评。** BeastXPy 不检验你的替换模型、钟或先验是否适合数据。
* **不做后验预测检验。**

## 校验

* Gate 3 证明官方解析器接受文档并能建立模型图，不证明分析在科学上站得住。
* `parse` 与 `java-core` 模式使用 BeastXPy 自带的似然解析器（见[校验](validation.md)）。只有 `beagle` 模式使用原样官方解析器集合。
* 校验覆盖清单中的两个 BEAST 版本，平台范围为 CI 矩阵所列平台。

## 被拒绝的分析

`codon`（通过 `<convert dataType="codon">` 的 GY94）与 `recombination`（ARG）在 Gate 0 被拒绝。两处失败都在官方 BEAST 解析器内，两个版本皆然。BeastXPy 不为它们提供兼容 shim，也不会把 shim 说成官方支持。`srd06` 是受支持的密码子路径。

## 数据与规模

* BeastXPy 负责组装 XML，不实现似然。
* 超大比对的限制来自 BEAST 与 BEAGLE，而不是 BeastXPy。
* `compare` 需要原生 BEAGLE，缺失时失败关闭。

## 可复现性

锁固定版本与文件摘要，但不固定操作系统数学库或 CPU 浮点行为，因此不承诺跨机器逐位一致。

## 引用元数据

`CITATION.cff` 已写明作者（曾子超，ZengZichao）及 ORCID iD（0000-0001-6553-970X）。DOI 将在发布存档后加入该记录。

## 平台

支持 Windows、Linux 与 macOS。Nextflow 流水线在 Windows 上不被原生支持。

## 范围与路线图

* **参考数据演示，不是新的实证研究。** `examples/empirical/` 的端到端案例使用随包的 898 bp 教程比对，是工作流与可复现性演示，不能作为生物学结论引用；链长是为可复现演示选择的，而非发表级推断。
* **BEAUti 对照是结构性的。** 仓库把生成的 XML 与手工誊录的 BEAUti 1.10.4 惯例做元素/属性级比较，并逐条列出有意差异。本验证环境没有执行实时 BEAUti 的后验摘要对照：BEAUti 没有受支持的无头导出；直接驱动其生成器 API（1.10.4 与 10.5.0 构建）产出的 XML 会被 BEAST 自己拒绝（`coalescentTree` 引用了实际发出的 `coalescentSimulator`）。实时对照需要图形界面，仍未完成。
* **仅面向 BEAST 1 / BEAST X。** BeastXPy 面向 BEAST 1 谱系（1.10.4）及其延续版本（BEAST X 10.5.0）。对 BEAST 2 的支持——不同的 XML 模式、解析器集合与包生态——是可能的未来方向，但目前刻意不作承诺。
* **`assess` 支持裸日志。** 可重复的 `--trace` 与配套的 `--trees` 让 BeastXPy 之外的运行无需复制成 `<workdir>/<seed>/` 布局即可评估。该模式没有 `run.log`，因此不提供 operator 表；链标号按输入顺序（1..N）。
* **命名。** "BeastXPy" 意为"面向 BEAST 1 / BEAST X 的 Python 工作流层"。它是独立项目，不隐含 BEAST 或 BEAGLE 开发者社区的背书或隶属关系。
