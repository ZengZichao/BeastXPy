# 参考数据案例

[English](README.md) | [中文](README.zh-CN.md)

本目录在随包的 `examples/primates.fasta` 比对（12 个分类单元、898 bp）上运行完整的 BeastXPy 工作流。这些序列是本仓库已经包含的一份篇幅很短的已发表/参考教程比对；没有生成新的实证数据，也没有下载任何数据。

**用途定位。** 这是一次工作流与可复现性演示。它不是关于灵长类分歧时间的生物学推断，也不是发表级分析。该比对是一份很短的教程比对，链长也是为可复现演示而选择的；记录的 `assess` 判定为 `pass`，但这只表示没有越过所配置的诊断阈值。

## 文件

| 文件 | 内容 |
|---|---|
| `config_primates_reference.yaml` | 两个分区（各为 HKY+G）、严格钟、Yule 树先验、两个化石标定 |
| `results/config.yaml` | 记录的那次运行所用的确切配置 |
| `results/generated.xml` | 由 `generate --beast-validate` 产出的 BEAST XML |
| `results/generated.fingerprint.json` | 该文档的 Gate-3 记录（`status=passed`、`mode=beagle`） |
| `results/generated_methods.tex` | 依据校验结果写出的 LaTeX 方法段落 |
| `results/beastxpy.lock` | 环境锁（jar、Java、BEAGLE、依赖） |
| `results/assessment.json` | `assess` 信封（ESS、PSRF、ASDSF、判定） |
| `results/report.html`、`results/report.json` | 自包含报告及其机器可读信封 |
| `results/case_study_summary.json` | 命令、退出码、耗时与运行后评估摘要 |

**路径说明。** `results/config.yaml` 是记录的那次运行所用的确切配置，因此其中的 `file: ../primates.fasta` 路径是相对于 `examples/empirical/` 的。请在该目录下运行它（或把它复制到该目录）；直接在 `results/` 下运行会在 Gate 0 失败，因为此时 `../primates.fasta` 指向一个并不存在的文件。

`results/` 由以下命令产出：

```
export JAVA_HOME=/path/to/jdk-17
export BEASTX_BEAGLE_1_10_4_LIB=/path/to/beagle-3.1.x/lib
python tools/run_reference_case.py \
    --workdir validation_runs/primates_reference \
    --results examples/empirical/results
```

该脚本拒绝编造诊断：若某个统计量无法计算，运行后评估 JSON 会记录 `not_computable` 并给出原因，摘要照抄该值。

## 记录结果（2026-10-22）

BEAST X 10.5.0、BEAGLE 4.0.1、Temurin JDK 17.0.20.1、Linux x86_64；四个种子并行，2,000,000 步，两个分区（各为 HKY+G）、严格钟、Yule 先验与两个化石标定。

| 指标 | 结果 |
|---|---|
| `assess` 判定 | `pass`（ESS 200、PSRF 1.01 阈值） |
| 每条链最小 ESS | 698.7 / 775.4 / 748.9 / 767.0 |
| 最大秩归一化 split R-hat | 1.00145 |
| ASDSF | 0.000115 |
| 根高 | 中位数 49.83，95% HPD [42.86, 60.19] |
| Catarrhini tmrca | 中位数 26.10，95% HPD [25.29, 27.85] |
| Homininae tmrca | 中位数 7.56，95% HPD [7.17, 8.07] |
| MCMC 墙钟时间 / 总计 | 192.8 s / 216.0 s |

这是参考数据上的工作流演示，不是生物学定年结果。

## 与 BEAUti 的后验对照

独立的 BEAUti 后验对照需要运行 BEAUti，而 BEAUti 是图形界面程序；本仓库的校验环境既没有显示器，也没有 BEAUti 自动化。因此这里检查的是 `tests/golden/beauti_reference/` 与 `tests/test_beauti_reference.py` 中 XML 惯例的结构等价性（逐元素、逐属性，每一个有意差异都已有记录）。后验摘要的对照仍未完成，已列入 `docs/zh/limitations.md`；此处不作宣称。
