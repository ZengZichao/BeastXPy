# BeastXPy 行为契约

[English](CONTRACT.md) | [中文](CONTRACT.zh-CN.md)

本文档公开声明代码与测试所强制执行的接口和诚实性规则。源码注释中以 `CONTRACT.md §<section>` 的形式引用它。章节编号保持稳定；散文措辞可以在不重新编号的情况下澄清。

编号说明：对外引用保留其历史编号。本次修订中没有 §3、§4 和 §6；§5 F1 与 §5 F2 是 §5 下并列的两个子条款。

## §1 范围

BeastXPy 是面向 BEAST 1.10.4 与 BEAST X 10.5.0 的工作流层。它不替代 BEAST，不断言模型在生物学上充分，也不宣称 MCMC 收敛。它报告的每一个数字，要么由指定的输入产物计算得到，要么以 `not_computable` 报告并附上原因。

## §2 Gate

| Gate | 问题 | 引擎 | 失败行为 |
|---|---|---|---|
| 0 | YAML 是否是合法且自洽的分析？ | 纯 Python | 生成/运行中止，exit 1 |
| 1 | 生成的 XML 结构上是否是合法的 BEAST 1？ | 纯 Python | 生成/运行中止，exit 1 |
| 2 | 标定与数据是否可用且不矛盾？ | 纯 Python | 错误会中止；警告与提示照常报告 |
| 3 | 官方 BEAST jar 是否接受并实例化它？ | Java | 未运行时 exit 2，被 BEAST 拒绝时 exit 3 |

Gate 3 有四个底层状态：`executed-pass`、`executed-fail`、`not-run` 和 `environment-missing`。公开 API 把 `environment-missing` 归并到 `not-run`，同时保留 `raw_status` 与 `environment_error`。未执行的 Gate 绝不显示为 `OK`。

Gate-3 的模式始终会被记录，并在 CLI 输出中显示：

| 模式 | 似然 |
|---|---|
| `parse` | 未评估（项目的 `treeLikelihood` shim） |
| `instantiate` | 未评估 |
| `java-core` | 未评估（仅构造对象的 shim） |
| `beagle` | 由原生的官方解析器集合评估 |

只有 `beagle` 是完整的官方校验。README、`beastxpy capabilities` 与报告中的说法，在模式不是 `beagle` 时都会点明具体模式。

## §5 F1 —— 运行后评估的诚实性规则

`beastxpy assess` 只做诊断。下列各项是硬性要求：

1. 可用链少于两条时，PSRF 与 ASDSF 报告为 `status: "not_computable"`，并附上通俗语言的说明。不编造数值，不插补数值，也不从另一次运行沿用数值。
2. 某个统计量的输入列不存在时，它报告为 `not_computable` 并指明缺失的列。绝不用猜测的数字替代。
3. Bulk/tail ESS 至少需要两条输入链。不会把单条链对半拆分来制造第二条链；单链运行使用 Geyer ESS。
4. 不存在 Geweke 分数时，自动 burn-in 返回 `not_computable`；它绝不会静默地把一条未经评估的链当作 burn-in 为零。
5. `pass` 判定意味着“没有越过任何配置的阈值”，而不是“链已收敛”。报告在每种语言里都这样说明。
6. JSON 信封始终携带 §7 中列出的顶层键。

## §5 F2 —— 数据检查发现项的严重级别

`beastxpy check-data` 通过同一张表构建发现项。错误绝不降级为警告。

| 发现项 id | 严重级别 |
|---|---|
| `duplicate-taxon` | 错误 |
| `empty-taxon` | 错误 |
| `duplicate-sequence` | 警告 |
| `taxon-name-format` | 警告 |
| `illegal-character` | 错误 |
| `high-ambiguity` | 警告 |
| `partition-overlap` | 错误 |
| `partition-gap` | 警告 |
| `partition-out-of-range` | 错误 |
| `partition-taxon-mismatch` | 错误 |
| `calibration-taxon-missing` | 错误 |
| `calibration-empty-taxa` | 错误 |
| `tip-date-format` | 错误 |
| `tip-date-order` | 警告 |
| `root-height-conflict` | 错误 |
| `codon-length` | 错误 |
| `codon-stop` | 警告 |
| `empirical-tree-taxa` | 错误 |
| `config-parse-error` | 错误 |
| `internal-error` | 错误 |

退出码：0 = 没有发现项或仅有提示，1 = 有警告，2 = 有错误。

## §7 —— `assess` 命令接口

```
beastxpy assess <workdir> [--trace PATH ...] [--trees PATH ...]
                [--burnin FRACTION|auto] [--ess MIN]
                [--psrf MAX] [--strict] [--json PATH]
                [--html PATH] [-q]
```

退出码：

| 码 | 含义 |
|---|---|
| 0 | 判定为 `pass` 或 `warn`（非严格模式） |
| 1 | 判定为 `warn`（在 `--strict` 之下） |
| 2 | 输入不可读或未找到任何链 |
| 3 | 判定为 `fail` |

JSON 文档的 schema 为 `beastxpy.assessment/v1`，并始终包含下列顶层键：

```
schema_version, generated_at, tool_version, workdir, verdict, strict,
thresholds, counts, chains, multichain, reasons, limitations
```

`counts` 是 `reasons` 的发现项计数：它始终包含 `error`、`warning` 和 `info`，每一项都是对应严重级别的发现项数量。

每条链的条目始终包含 `seed`、`status`、`trace_log`、`tree_log`、`run_log`、`run_meta`、`samples`、`burnin_fraction`、`burnin_samples` 和 `parameters`。`multichain` 对象始终包含 `available`、`n_chains`、`psrf` 和 `asdsf`；每个统计量对象都携带 `status`，以及 `values`/`value` 或一个 `reason`。

## §8 自动先验

凡是因为配置未指定而由 BeastXPy 添加的先验，都会：

1. 在生成的 XML 中以一条以 `BXP auto prior (BEAUti-1.10.4 convention)` 开头的注释标出；
2. 作为 Gate-2 的 `info` 发现项报告，代码为 `AUTO_PRIOR`，可在 `generate --beast-validate` 的输出中看到；
3. 列在 `report` 的 HTML 章节“自动先验”中；
4. 可在 YAML 的 `parameter_priors` 下覆盖。

## §9 相关文档

- [校验模式与证据](docs/zh/validation.md)
- [运行后评估规则](docs/zh/assessment.md)
- [数据检查](docs/zh/cli.md#beastxpy-check-data)
- [原生 BEAGLE](docs/zh/native-beagle.md)
