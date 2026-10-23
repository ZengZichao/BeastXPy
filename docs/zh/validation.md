# 校验

[English](../en/validation.md) | [中文](validation.md)

BeastXPy 有四道闸门。每道回答不同的问题，这个区别在读结果时很重要。

| 闸门 | 问题 | 引擎 |
|---|---|---|
| 0 | YAML 是否是合法且自洽的分析？ | 纯 Python |
| 1 | 生成的 XML 结构上是否是合法 BEAST 1？ | 纯 Python |
| 2 | 标定是否可用且不矛盾？ | 纯 Python |
| 3 | 官方 BEAST jar 是否接受并实例化？ | Java |

（BEAST：Drummond & Rambaut 2007、Drummond 等 2012——见 [references](references.md)。）

## 第 1 步 —— 读懂每一道门的输出

`beastxpy generate --config demo.yaml --output demo.xml --beast-validate` 在 stderr 上逐门打印。以下是[快速上手](quickstart.md)演示配置的真实输出：

```
Gate 0 (parse-time, before the 3 generation gates): demo.yaml
Gate 0: OK
Gate 1/3: OK (0 warning(s), 0 error(s))
Gate 2/3: OK (4 finding(s))
  Gate 2 [info] LOGNORMAL_LOG_SPACE_MEAN calibration root: calibration 'root' does not set meanInRealSpace, so BEAST interprets mean=3.4 and stdev=0.5 in log space (BEAST's default; BEAUti defaults to a real-space mean). With offset 0 the actual prior median is 29.96 and the 95% interval is [11.25, 79.84]. Set parameters.meanInRealSpace: true if you intended mean to be the real-space mean (e.g. a date in Ma).
  Gate 2 [info] AUTO_PRIOR clock.rate: automatic prior applied: clock.rate ~ oneOnXPrior (BeastXPy convention); override under parameter_priors
  Gate 2 [info] AUTO_PRIOR frequencies: automatic prior applied: frequencies ~ uniformPrior(lower=0, upper=1) (BeastXPy convention); override under parameter_priors
  Gate 2 [info] AUTO_PRIOR yule.birthRate: automatic prior applied: yule.birthRate ~ logNormalPrior(mean=2, stdev=1.5, offset=0.0, meanInRealSpace=false) (BEAUti-1.10.4 convention); override under parameter_priors
Gate 3/3: BEAST 1.10.4 validation: OK [mode=java-core (construct-only shim; likelihood NOT evaluated by the official core); tree_likelihood_rule_source=shim]
  WARNING: this was NOT a full official validation: the likelihood was not evaluated (mode=java-core (construct-only shim; likelihood NOT evaluated by the official core); tree_likelihood_rule_source=shim). Install native BEAGLE and re-run for the stronger `beagle` mode.
```

解读要点：

* `Gate 2 [info]` 是提示而不是错误：它如实说明对数尺度标定均值的实际含义，并逐一列出 BeastXPy 自动添加的先验（可在 `parameter_priors` 中覆盖）。
* Gate 3 行方括号里是实际使用的校验模式。在有原生 BEAGLE 的机器上，这里会显示 `[mode=beagle ...]`，WARNING 行消失，证据即完整官方验证。

退出码 0 表示**每个被请求的门都真实运行且通过**。退出码的区分：1 = Gate 0-2 失败或配置错误；2 = Gate 3 无法运行（缺 Java/jar）；3 = Gate 3 运行且 BEAST 拒绝了文档。

Gate 1 的元素名匹配**区分大小写**，因为 BEAST 的解析器注册表区分大小写。包含 `<TreeModel>` 的文档会以 `ELEMENT_CASE_MISMATCH` 被拒绝（finding 会指出正确的 `<treeModel>`），而不是被静默接受。

## 第 2 步 —— 校验已有 XML

```
beastxpy validate --xml demo.xml --beast --beast-version 1.10.4
```

此时标签略有不同：`Gate 1/2` 是纯 Python 结构校验，`Gate 2/2` 是 BEAST 校验。退出码：0 通过；1 结构错误；2 BEAST 校验无法运行；3 被 BEAST 拒绝。

## 第 3 步 —— 复现仓库自带矩阵

```
python tools/official_validate.py --matrix "examples/config_*.yaml" --mode java-core
python tools/validate_coverage.py
```

以上三步是日常使用的表面；本页其余部分解释 Gate 3 的证据到底证明什么、不证明什么。

## Gate 3 实际做了什么

`--beast-validate` 用 BEAST jar 执行 XML 解析、MCMC 对象构建，并计算一次有限的初始后验。`passed` 表示官方解析器接受了文档，模型图可以建起来。

底层结果有四种状态，而不是两种：`executed-pass`、`executed-fail`（BEAST 运行并拒绝）、`not-run`（校验根本没开始：超时、XML 缺失、验证器类缺失）与 `environment-missing`（没有可用的 Java 或 jar），每种都带原因。公开的 CLI/API 门面把 `environment-missing` 折叠进 `not-run`，但在 JSON 的 `raw_status` 与 `environment_error` 中保留区分，四态信息不会丢失。没运行过的门绝不会被打印成 `OK`。

## 校验模式

Gate 3 以本机支持的最强模式运行：

| 模式 | 做什么 | 似然使用的解析器 |
|---|---|---|
| `parse` | 只做 XML 解析 | 官方集合 + 项目 `treeLikelihood` shim（见下） |
| `instantiate` | 解析 + 构建 MCMC 对象图 | 官方 |
| `java-core` | 解析 + 实例化 + 初始后验 | 官方集合 + 项目 `treeLikelihood` shim（见下） |
| `beagle` | 解析 + 实例化 + 真实原生似然 | 官方 |

**`parse` 与 `java-core` 模式只对 `<treeLikelihood>` 子树替换成 BeastXPy 自带的解析器。** BEAST 内置的纯 Java BEAGLE 没有实现位点对数似然入口，因此改用旧版 Java 似然核心。该解析器属于校验工装，不是 BEAST 功能，也不是受支持的分析路径。它的语法规则逐行镜像两个随包 BEAST 版本的 `dr.evomodelxml.treelikelihood.BeagleTreeLikelihoodParser.rules`；反射测试（`tests/test_beast_validation.py::test_shim_syntax_rules_equal_official_parser`）会在两者偏离时失败。Gate 3 还会在 environment 阶段与 JSON 结果中记录 `tree_likelihood_rule_source=shim` 或 `official`。镜像规则接受 `<branchModel>` 子元素，但 shim 无法计算它；此时会发出 `SHIM_BRANCH_MODEL` 警告并只使用 `<siteModel>`。只有 `beagle` 模式运行原样的官方解析器集合，所以原生 BEAGLE 验证才是更强的证据。选择按 BEAST 版本自动进行：能发现原生 hmsbeagle 库（`BEASTX_BEAGLE_<version>_LIB`、`BEASTX_BEAGLE_LIB`、`BEAGLE_HOME`，或 jar 同级的 `lib/beagle`）时用 `beagle`，否则用 `java-core`。库存在但加载失败时会给出提示并退回较弱核心——不可用的 BEAGLE 是环境问题，不是生成 XML 的缺陷。

### 证据强度（一个声明到底意味着什么）

| 模式 | 解析器集合 | 似然 | 该证据支持的声明 |
|---|---|---|---|
| `parse` | 官方解析器；`treeLikelihood` 子树用项目 shim | 无 | XML 良构，元素/id 在解析阶段被接受，且 `treeLikelihood` 子树满足 shim 镜像的规则面 |
| `java-core` | 官方解析器 + 项目自带的 `TreeLikelihood` shim | 不计算（仅构建） | 模型图可以构建，初始后验对象存在；似然没有由官方核心计算 |
| `beagle` | 完整官方解析器集合 | 真实原生 hmsbeagle 似然 | 真实似然下的完整官方验证 |

`beastxpy capabilities` 中的每一行 “supported”、每条 reason 以及 README 中的每条声明，除非明确写出 `beagle`，否则指的都是 `java-core`。论文或发布说明若声称“已用 BEAST X.Y 验证”，必须写明模式，并报告实际运行过的矩阵：平台 × BEAST 版本 × 示例配置数。下面的命令用来运行该矩阵；在记录下你所在平台的输出之前，它本身不是一个声明。

```
python tools/official_validate.py --matrix "examples/config_*.yaml" \
    --mode beagle --versions 1.10.4 10.5.0
```

`beastxpy capabilities` 会打印 `data/xml_spec.json` 中记录的每个 BEAST 版本的源 jar SHA-256，因此元素白名单过期与否可以与已安装的 BEAST 版本对照。

### 已记录的验证矩阵

以下矩阵于 2026-10-22 在本检出上实际运行（Linux x86_64、JDK 17.0.20.1、随包官方 jar；原生 BEAGLE 按各 BEAST 版本的固定源码构建）。每一行都覆盖完整的 `examples/config_*.yaml` 集合：

| BEAST 版本 | 模式 | 原生 BEAGLE | 通过示例数 |
|---|---|---|---|
| 1.10.4 | `beagle` | 3.1.2（源码构建，SHA-256 `dd872b48...`） | 16/16 |
| 10.5.0 | `beagle` | 4.0.1（源码构建，SHA-256 `9d258cd9...`） | 16/16 |

两行都是完整解析器 + 真实似然的验证。同一检出还通过了 `examples/coverage/` 中所有适用于该版本的覆盖夹具（BEAST 1.10.4 为 12/12，BEAST X 10.5.0 为 15/15；其中三个夹具仅适用于 10.5.0）。两行均可重放：`tools/run_beagle_evidence.py` 下载固定版本 BEAGLE 源码、校验 SHA-256、构建（4.0.1 用 CMake，3.1.2 用 autotools）、运行矩阵，并写出 `evidence.json`（命令、哈希、通过数）。CI 的 `beagle-evidence` 作业按 BEAST 版本各运行一次并上传产物。自动构建需要 JDK；若检出路径含非 ASCII 字符，请在 ASCII 目录构建 3.1.2（其 autotools 的 `javac` 探测在 `LC_ALL=C` 下运行）。

仅 `java-core` 的运行是原生 BEAGLE 不可用时的仅构建回退；如*证据强度* 表所示，其证据弱于 `beagle` 行。

### BEAUti 惯例参照对照

黄金文件锁定的是本生成器的行为，不是外部真值。`tests/golden/beauti_reference/` 为三个代表性分析（JC69/严格钟/Yule、HKY+Gamma+UCLN 加一个标定、两个 GTR 分区）提供了手工誊录的参照子集。数值来自写 XML 模板的 BEAUti 1.10.4 option 类，并用 BEAST 源码树中 BEAUti 生成的 `examples/Benchmarks/benchmark1.xml` 交叉核对。`tests/test_beauti_reference.py` 逐元素、逐属性比较，并显式断言每一个有意差异：钟速率先验族、gamma 形状先验族、GTR 速率先验族、碱基频率先验族，以及标定的 `meanInRealSpace` 默认值。这些参照不是实时 BEAUti 输出（BEAUti 是图形界面，本环境无法无头导出），因此这是结构等价证据，不是后验对照。

### 参考数据案例（工作流演示）

`examples/empirical/` 在随包的 `examples/primates.fasta` 参考/教程比对（12 个分类单元、898 bp）上跑完整流程。这不是新的实证研究，也不是生物学结论；它说明流水线能在参考数据上得到收敛、可复现的结果。配置为两个分区（均为 HKY+G）、严格钟、Yule 树先验和两个化石标定。四个种子，2,000,000 步，每 1,000 步记录：

```
export JAVA_HOME=/path/to/jdk-17
export BEASTX_BEAGLE_10_5_0_LIB=/path/to/beagle-4.0.1/lib
python tools/run_reference_case.py \
    --workdir validation_runs/primates_reference \
    --results examples/empirical/results \
    --beast-version 10.5.0 --seeds 1,2,3,4 --parallel 4
```

记录的运行（BEAST X 10.5.0、BEAGLE 4.0.1、Temurin JDK 17.0.20.1、Linux x86_64，2026-10-22）：

| 指标 | 结果 |
|---|---|
| `assess` 判定 | `pass`（ESS 200、PSRF 1.01 阈值） |
| 链数 / 样本数 | 4 条链 × 2,001 个样本，10% burn-in |
| 每条链最小 ESS | 698.7 / 775.4 / 748.9 / 767.0 |
| 最大经典 split R-hat | 1.00138（posterior） |
| 最大秩归一化 split R-hat | 1.00145（posterior） |
| ASDSF | 0.000115 |
| 根高 | 中位数 49.83，95% HPD [42.86, 60.19] |
| Catarrhini tmrca | 中位数 26.10，95% HPD [25.29, 27.85] |
| Homininae tmrca | 中位数 7.56，95% HPD [7.17, 8.07] |
| MCMC 墙钟时间 | 192.8 秒（四个种子并行） |
| 全流程时间 | 216.0 秒 |

后验摘要使用配置的树单位（标定偏移以 Ma 计）；它们只是演示，不是定年结论。产物（配置、生成 XML、锁、评估 JSON、HTML/JSON 报告，以及含命令/耗时的摘要）在 `examples/empirical/results/`。这里不声称与实时 BEAUti 运行的后验对照；结构对照见上面的 BEAUti 一节。

### 模拟数据的端到端恢复

`tools/simulate_end_to_end.py` 是解析器矩阵之外的科学验证：在已知根树（(A:0.03,B:0.05):0.05,C:0.05,D:0.05；A 采样于年龄 0.02，因此根高为 0.10）上模拟四分类单元 JC69 比对，严格钟速率固定为 1.0，运行两个独立种子，并检查根高的 95% HPD 是否包含 0.10。

```
python tools/simulate_end_to_end.py --out validation_runs/simulated_e2e \
    --beagle-lib /path/to/libhmsbeagle
```

本检出上记录的一次运行（5000 位点、500,000 步、BEAST X 10.5.0 + 原生 BEAGLE 4.0.1）得到：

| 种子 | 根高 95% HPD | 中位数 | 是否包含 0.10 |
|---|---|---|---|
| 1 | [0.0903, 0.1020] | 0.0961 | 是 |
| 2 | [0.0904, 0.1013] | 0.0964 | 是 |

这条刻意较短的链在 `assess` 中的 verdict 为 `fail`（部分 ESS 低于 200）；脚本的通过标准只是 HPD 覆盖已知真值，不是收敛。

### 参数恢复研究（`--study recovery`）

同一脚本还能运行八分类单元的参数恢复研究：数据在不相关对数正态钟下模拟，树固定并含六个真实 clade；分支速率取对数正态（对数标准差为给定真值），并归一化到时间加权均值为 1。根年龄用 normal(1.05, 0.15) 先验标定，真值为 1.0。每个格子使用 4 个种子、2,000 个位点与 1,000,000 步 MCMC：

```
python tools/simulate_end_to_end.py --study recovery \
    --out validation_runs/recovery --seeds 1,2,3,4 \
    --stdev-truth 0.3 --stdev-truth 0.15 \
    --beagle-lib /path/to/libhmsbeagle
```

本检出上记录的结果（BEAST X 10.5.0、原生 BEAGLE 4.0.1，每行 4 个种子）：

| ucld.stdev 真值 | 校准年龄 HPD 覆盖 1.0 | ucld.stdev HPD 覆盖真值 | 后验均值 | 相对误差中位数 | 最小 ESS | 真实 clade 支持度（最小） | ASDSF |
|---|---|---|---|---|---|---|---|
| 0.30 | 4/4 | 3/4 | 0.523 | 0.452 | 19.7 | 0.966 | 0.000 |
| 0.15 | 4/4 | 4/4 | 0.128 | 0.260 | 105.1 | 1.000 | 0.000 |

这些是 `recovery_summary.json` 的完整逐种子结果，不是挑选后的子集。两个格子的校准覆盖率合计为 8/8，真实拓扑被恢复（六个 clade 的后验支持度均 ≥ 0.966），但在真值 0.30 时每个 `ucld.stdev` 估计的 ESS 都低于 200 的筛查阈值，真值 0.15 时四个也都低于 200；因此 `assess` verdict 为 `fail`。0.30 格子还有一个种子的 ucld.stdev 后验远高于真值，所以覆盖率为 3/4、均值被抬高。论文级恢复表需要更长的链（或更有信息量的设计）；脚本如实报告 ESS，而不是隐藏这一限制。

## `--allow-unvalidated`

它用于调试。在该选项下产出的结果会被标记为 `UNVALIDATED`，绝不会被打印成 `OK`。它不适用于自定义片段：拼接后的文档必须通过 Gate 3，否则拒绝生成。
