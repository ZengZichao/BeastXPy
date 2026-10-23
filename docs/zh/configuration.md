# 配置

[English](../en/configuration.md) | [中文](configuration.md)

一个 BeastXPy 分析就是一份 YAML。解析是严格的（Gate 0）：未知键、类型错误、带引号的布尔值、重复键、互相矛盾的设置都是**错误**，不是警告。一个拼写错误会在 BEAST 运行之前就把你拦下来。

## 第 1 步 —— 认识顶层键

| 键 | 作用 |
|---|---|
| `metadata` | `analysis_name`、溯源字符串 |
| `alignments` | 输入文件及其数据类型 |
| `partitions` | 位点区间与每分区模型 |
| `tree_prior` | Yule、生灭、各类溯祖、skyline、EBSP |
| `calibrations` | 节点标定及其分布与出处 |
| `parameter_priors` | 命名参数的先验 |
| `mcmc` | `chain_length`、`pre_burnin`、`auto_optimize`、operator 权重 |
| `loggers` | trace 与 tree 日志文件及记录间隔 |
| `output` | 输出 XML 名、是否嵌入指纹 |
| `control` | `beast_version`、`seed`、`strict`、`overlap_measure` |
| `tip_dates` | 异时采样 |
| `diagnostics` | 标定诊断设置 |
| `analysis` | 非标准分析族（见下） |
| `custom_fragments` | 需显式开启的手写 XML（见[扩展](extensions.md)） |

语义上必填的只有 `alignments` 和 `partitions`；其余都有默认值（`tree_prior: yule`、`control.beast_version: "1.10.4"`、`chain_length: 10000000`……）。

## 第 2 步 —— 从最小示例起步

```yaml
metadata:
  analysis_name: demo
alignments:
  - id: a1
    file: primates.fasta        # relative to THIS yaml file
    data_type: nucleotide
partitions:
  - id: p1
    alignment_id: a1
    from: 1                     # 1-based, inclusive
    to: 898
    site_model:
      substitution_model: {type: jc69}
    clock_model: {type: strict}
tree_prior: {type: yule}
mcmc: {chain_length: 1000000}
loggers:
  trace_log: {file_name: demo.log, log_every: 1000}
  tree_log: {file_name: demo.trees, log_every: 1000}
output: {file: demo.xml}
control: {beast_version: "1.10.4", seed: 1}
```

要点：`file` 相对本 YAML 文件解析；`from`/`to` 是 1 基闭区间。不落盘生成（XML 打到 stdout）：

```
beastxpy generate --config demo.yaml
```

加 `--output out.xml` 写文件，加 `--beast-validate` 触发 Gate 3。

## 第 3 步 —— 选择模型名

`beastxpy list-models` 打印注册名；YAML 只接受这些值：

| 类别 | 名称 |
|---|---|
| 替换模型（核苷酸） | `jc69`、`hky`、`tn93`、`gtr` |
| 替换模型（氨基酸） | `wag`、`jtt`、`dayhoff`、`blosum62`、`cprev`、`mtrev` |
| 分子钟 | `strict`、`ucln`（对数正态）、`uce`（指数）、`rlc`（随机局部） |
| 树先验 | `yule`、`birth_death`、`coalescent_constant`、`coalescent_exponential`、`coalescent_logistic`、`coalescent_expansion`、`skyline`、`ebsp` |
| 分布 | `normal`、`lognormal`、`uniform`、`exponential`、`gamma`、`inverse_gamma`、`beta`、`poisson`、`one_on_x`、`half_normal`、`laplace`、`half_t` |

树先验别名（`birth-death`、`constant`、`exponential`、`bayesian_skyline`、`extended_bayesian_skyline` 等）会被接受并归一化。每个模型背后的一手文献汇总在 [references](references.md)。氨基酸模型配核苷酸比对（或反过来）会被拒绝。仅 BEAST X 10.5.0 存在的元素在目标为 1.10.4 时被拒绝。

各模型参数：

* `hky`：`kappa`；`tn93`：`kappa1`、`kappa2`；`gtr`：`rates`（6 个速率，`{mode: estimated|fixed, value: [...]}`）与 `frequencies`。
* `frequencies`：`{mode: estimated|empirical|fixed, value: [...], dimension: 4}`。氨基酸模型只接受 `mode: empirical`。`mode: empirical` 只在分区自身的 `from`/`to` 窗口内统计碱基频率（与 `<patterns>` 使用同一窗口），而不是在整个比对上统计；单分区分析时两者相同。窗口与 `<patterns>` 一致，因此分区只覆盖部分位点时模型与数据仍然对齐。非 ACGT 字符被忽略，四个频率归一化到和为 1。
* `strict`：`clock_rate`；`ucln`：`ucld_mean`、`ucld_stdev`；`uce`：`exponential_mean`；`rlc`：`rlc_indicators`（>= 2）。
* 树先验各带自己的参数（`birth_rate`、`population_size`、`growth_rate`、`t50`、`skyline_groups`……）；用了不属于当前先验的参数是错误。

## 第 4 步 —— 添加标定

每个标定都必须给分布——没有默认均匀分布兜底：

```yaml
calibrations:
  - name: root
    taxa: null                # null = the root calibration
    distribution:
      type: lognormal
      parameters: {mean: 3.4, stdev: 0.5}   # mean on the LOG scale, must be > 0
  - name: human_chimp
    taxa: [Human, Chimp]
    calibration_method: crown # or stem
    distribution: {type: normal, parameters: {mean: 6.0, stdev: 0.5}}
    provenance:
      source: fossil
      calibration_type: soft  # hard bounds violate -> error, soft -> warning
      original_age_min: 5.0
      original_age_max: 7.0
```

即：`taxa: null` 表示根标定；lognormal 的 `mean` 在对数尺度上且必须为正；`calibration_method` 取 `crown` 或 `stem`；`calibration_type` 为 `hard` 时违反原始化石区间是错误，`soft` 是警告。最容易踩坑的分布参数规则：

| 类型 | 参数 | 说明 |
|---|---|---|
| `lognormal` | `mean`、`stdev`、可选 `meanInRealSpace` | 即使在对数尺度上 `mean` 也必须**大于 0** |
| `uniform` | `lower`、`upper` | |
| `exponential` | `mean`（或 `rate`，按 `1/rate` 存储） | |
| `gamma` | `shape`、`scale` | 别名 `alpha`/`beta` |
| `poisson` | `mean` | `offset` **必填** |
| 其他 | 见 `--help` 与 `check-data` 报错提示 | `offset` 仅用于 lognormal/exponential/gamma/inverse_gamma/beta |

`chi_square` 是可识别的拼写，但因不受支持而被拒绝（两个版本的官方 BEAST 都没有卡方先验解析器）。矛盾或部分重叠的单系 clade 是 Gate 0/Gate 2 错误；细节用 `beastxpy diagnose` 查看。

**`meanInRealSpace` 陷阱。** 除非写 `parameters.meanInRealSpace: true`，BEAST 把 lognormal 的 `mean`/`stdev` 当作对数空间参数；BEAUti 的标定构建器默认按实空间均值解释。若未显式声明该标志，Gate 2 会给出 `info` 级提示（`LOGNORMAL_LOG_SPACE_MEAN`），写明实际的先验中位数与 95% 区间。例如 offset 25、`mean: 1.0, stdev: 0.5` 的中位数约为 27.7 Ma，而不是 25 Ma。若你的 `mean` 就是 Ma 日期，请显式设置该标志。

## 第 5 步 —— 设置先验、链长与日志

```yaml
parameter_priors:
  - parameter: clock_rate
    distribution: {type: exponential, parameters: {mean: 1.0}}
  - parameter: kappa          # or an exact emitted id like gene2.clock.rate
    distribution: {type: lognormal, parameters: {mean: 1.0, stdev: 1.25}}
mcmc:
  chain_length: 100000000     # real analyses: aim for ESS > 200
  pre_burnin: 0               # must be < chain_length
  auto_optimize: true
loggers:
  trace_log: {file_name: demo.log, log_every: 10000}
  tree_log: {file_name: demo.trees, log_every: 10000}  # names must differ
```

`log_every` 默认为 `chain_length // 1000`；经验法则是记录 1000-10000 个样本以获得稳定的 ESS 估计。

`auto_optimize: true` 与 BEAUti 一致，会生成 `<mcmc autoOptimize="true">` 且不写 `targetAcceptance`，因此 BEAST 使用自身默认目标（树算子约 0.234，标量算子约 0.3）。BeastXPy 不覆盖这些默认值。

**时间单位。** Yule、birth-death 与溯祖起始树模型带有 BEAST 属性 `units="substitutions"`。在 BEAST 1 中该属性只是描述性的，不会转换节点高度；标定与 tip 日期仍按写入的 Ma 解释。保留它是为了与官方解析器示例兼容，并不表示时间轴以替换数计。`pre_burnin` 必须小于 `chain_length`；两个日志文件名必须不同；`parameter` 也可以用精确生成的参数 id（如 `gene2.clock.rate`）。

## 自动先验从哪里来

`parameter_priors` 没有点名某个参数时，`ModelAssembler` 会发出默认先验。这些是 **起点**，不是普适推荐；依据列明确说明该值复制自 BEAUti 还是 BeastXPy 的约定。请按分析选择，需要时用 `parameter_priors` 覆盖。

| 参数 | 发出的默认值 | 依据 |
|---|---|---|
| `yule.birthRate`、`bMinusD` | lognormal（对数均值 2、对数标准差 1.5） | BEAUti 1.10.4 `PartitionTreePrior` 默认；Drummond 等 (2012) |
| `kappa`、`kappa1`、`kappa2` | lognormal（对数均值 1、对数标准差 1.25） | BEAUti 1.10.4 `PartitionSubstitutionModel` 默认；Drummond 等 (2012) |
| `ucld.stdev`、任意 `*.stdev` | exponential（均值 1/3） | BEAUti 松弛钟默认；Drummond 等 (2006) |
| `siteModel.alpha` | lognormal（对数均值 1、对数标准差 1.25） | BeastXPy 约定；BEAUti 使用均值 0.5 的 exponential |
| `clock.rate`、`ucld.mean` | oneOnX | BeastXPy 约定；BEAUti 使用 CTMC 速率参考先验 |
| `growthRate` | normal（均值 0、标准差 1） | BeastXPy 约定；BEAUti 使用 Laplace 先验 |
| `popSize` | uniform（0, 1e8） | BeastXPy 约定；BEAUti 使用 oneOnX |
| `relativeDeathRate` | uniform（0, 1） | BEAUti 1.10.4 `PartitionTreePrior` 与 BEAST 隐式界 |
| `pInv`、GTR 速率 | 在声明界内的 uniform | BEAST 参数界约定 |
| simplex 频率 | 在单纯形上的 uniform | BeastXPy 约定；BEAUti 使用 Dirichlet 先验 |
| 其他无界参数 | lognormal（对数均值 1、对数标准差 1.25） | BeastXPy 回退值，刻意弱信息 |

BEAUti 归因行已对照写 XML 模板的 1.10.4 代码路径核验。BeastXPy 约定行是有意选择，并非 BEAUti 默认；生成的 XML 注释与 Gate 2 会给出相同依据。回退值在真实分析中应替换。

自动先验在每个环节都可见：

1. **XML。** 每个自动先验元素前都有以 `BXP auto prior (BEAUti-1.10.4 convention)` 开头的注释，注明参数与发出的分布。
2. **Gate 2。** `generate --beast-validate` 会把每个自动先验列为 `info` 发现，代码为 `AUTO_PRIOR`。
3. **报告。** `beastxpy report` 有 **自动先验（Automatic priors）** 一节，列出生成 XML 中发现的全部注释。
4. **覆盖。** 可在 `parameter_priors` 中替换任意一项。

默认值没有隐藏。

## 第 6 步 —— 版本、种子、tip dates

* `control.beast_version`：`"1.10.4"`（默认）、`"10.5.0"` 或 `"both"`；命令行 `--beast-version` 可覆盖。
* `control.seed`：默认随机种子（0..2^31-1）；`run --seeds` 可覆盖。
* `control.initial_tree`：`coalescent`（默认）、`upgma` 或 `newick`；后两者不能与标定同用。
* `tip_dates`：分类单元 → 年代的映射，如 `tip_dates: {Bovine: 1.0}`，或逐分类单元 `{value: 1.0, direction: forward, units: years}`。时间轴必须可识别：至少给出一个标定、一个 tip 日期，或让钟速率可估计。

## 覆盖分析族与片段

设置 `analysis.type` 会切换到非标准分析族（离散性状、祖先状态、BDSS、StarBEAST、MLE 等）。每族都有版本门控，部分直接被拒绝：

```
beastxpy capabilities
```

打印权威清单。`codon` 与 `recombination` 在 Gate 0 被拒绝，原因是官方 BEAST 解析器无法接受它们；每个版本的具体原因记录在清单里。手写 XML 走 `custom_fragments` —— 见[扩展](extensions.md)。

## Gate 0 拒绝文件时

错误会指明小节、键和原因：

```
Gate 0: FAILED -- partitions[0].site_model.gamma_shape: gamma_shape requires gamma_categories > 1
```

照着修就行。`beastxpy check-data --config demo.yaml` 只跑数据侧预检（重复分类单元、含糊序列、标定冲突），不生成任何东西。
