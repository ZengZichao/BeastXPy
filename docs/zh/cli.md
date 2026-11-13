# 命令行参考

[English](../en/cli.md) | [中文](cli.md)

每个命令都由 `beastxpy.main` 注册。下文的 flag、metavar 名称、默认值与必选性都来自响应 `--help` 的同一个参数解析器：必选参数不带方括号，可重复的参数写成 `PATH ...`，`--print` 保留解析器自己的 `{docker,apptainer}` 顺序。synopsis 是 `--help` 的阅读形式而非逐字拷贝，因此省略 `[-h]`，并按稳定的阅读顺序排列单个取值不带重复的 flag。`tests/test_docs.py` 会针对真实解析器检查这一对应关系。

全局选项：

```
beastxpy --help
beastxpy --version
```

默认退出码：0 成功，1 配置或用法错误，2 环境不可用，3 校验或判定失败。

本页目录：[assess](#beastxpy-assess) · [capabilities](#beastxpy-capabilities) · [check-data](#beastxpy-check-data) · [check-environment](#beastxpy-check-environment) · [compare](#beastxpy-compare) · [container](#beastxpy-container) · [diagnose](#beastxpy-diagnose) · [fingerprint](#beastxpy-fingerprint) · [generate](#beastxpy-generate) · [list-models](#beastxpy-list-models) · [lock](#beastxpy-lock) · [logcombine](#beastxpy-logcombine) · [methods](#beastxpy-methods) · [pipeline](#beastxpy-pipeline) · [quick](#beastxpy-quick) · [report](#beastxpy-report) · [reproduce](#beastxpy-reproduce) · [run](#beastxpy-run) · [trace](#beastxpy-trace) · [tree-annotate](#beastxpy-tree-annotate) · [validate](#beastxpy-validate) · [JSON 模式](#json-模式)

## 命令

### `beastxpy assess`

诊断 MCMC 输出：ESS/ACT/HPD、PSRF 与 ASDSF、算子、树摘要。仅作诊断。

**用法**

```
beastxpy assess [WORKDIR] [--trace PATH ...] [--trees PATH ...] [--burnin FRACTION|auto] [--ess MIN] [--psrf MAX] [--html PATH] [--strict] [--json PATH] [-q]
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `WORKDIR` | `--` | 否（未给 `--trace` 时必填） |
| `--trace` | `PATH` | 否（评估多条链时重复使用） |
| `--trees` | `PATH` | 否（与 `--trace` 同序、一一对应） |
| `--burnin` | `FRACTION` 或 `auto` | 否 |
| `--ess` | `MIN` | 否 |
| `--psrf` | `MAX` | 否 |
| `--html` | `PATH` | 否 |
| `--strict` | `flag` | 否 |
| `--json` | `PATH` | 否 |
| `-q, --quiet` | `flag` | 否 |

**示例**

请先运行 `beastxpy run --config examples/config_basic.yaml --seeds 1,2 --workdir runs/demo`；PSRF 至少需要两个种子。

```
beastxpy assess runs/demo --json runs/demo/assessment.json
```

也可直接评估 BeastXPy 工作目录之外的 BEAST 日志：每个 `--trace` 是一条链（按 1..N 标号），`--trees` 提供对应的树日志用于 ASDSF：

```
beastxpy assess --trace seed_1.log --trace seed_2.log \
    --trees seed_1.trees --trees seed_2.trees --burnin auto
```

**输出**

摘要输出到 stdout；--json 与 --html 写文件。仅作诊断。

**退出码**

0 通过或警告，1 --strict 下的警告，2 输入不可读，3 判定失败。

### `beastxpy capabilities`

打印功能 × BEAST 版本的支持矩阵。

**用法**

```
beastxpy capabilities [--beast-version {1.10.4,10.5.0,both}] [--json PATH] [-q]
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `--beast-version` | `{1.10.4,10.5.0,both}` | 否 |
| `--json` | `PATH` | 否 |
| `-q, --quiet` | `flag` | 否 |

**示例**

```
beastxpy capabilities
```

**输出**

对齐表格输出到 stdout；--json 写出信封。

**退出码**

0 正常，2 清单缺失或无法解析。

### `beastxpy check-data`

对配置指向的数据与标定做预检。

**用法**

```
beastxpy check-data --config PATH [--ambiguity-threshold FRACTION] [--json PATH] [-q]
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `--config` | `PATH` | 是 |
| `--ambiguity-threshold` | `FRACTION` | 否 |
| `--json` | `PATH` | 否 |
| `-q, --quiet` | `flag` | 否 |

**示例**

```
beastxpy check-data --config examples/config_basic.yaml
```

**输出**

带位置与修复建议的发现项输出到 stdout；--json 写出信封。

**退出码**

0 通过，1 仅警告，2 发现错误。错误绝不降级。

### `beastxpy check-environment`

探测 Python、Java 与 BEAST jar。

**用法**

```
beastxpy check-environment
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|

**示例**

```
beastxpy check-environment
```

**输出**

解析到的 Java、jar 与 Python 输出到 stdout。

**退出码**

0 成功，1 配置或用法错误，2 环境不可用，3 校验或判定失败。

### `beastxpy compare`

用真实 PS/SS 运行按边际似然比较模型；缺少 BEAGLE 时失败关闭。

`--path-steps` 默认 8——这是快速估计并会打印一次性警告；发表级结果请至少用 50。每个模型的 `--timeout` 默认 86400 秒（24 小时）。

**用法**

```
beastxpy compare --configs PATH [PATH ...] --method {stepping-stone,path-sampling} --workdir DIR [--seeds 1,2] [--parallel N] [--chain-length N] [--path-steps N] [--path-scheme {linear,oneSidedBeta,beta,betaQuantile,sigmoid}] [--log-every N] [--beast-version {1.10.4,10.5.0}] [--beagle-dir DIR] [--beagle-threads N] [--timeout SECONDS] [--csv PATH] [--html PATH] [--json PATH] [-q]
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `--configs` | `PATH` | 是 |
| `--method` | `{stepping-stone,path-sampling}` | 是 |
| `--workdir` | `DIR` | 是 |
| `--seeds` | `1,2` | 否 |
| `--parallel` | `N` | 否（每个模型的种子并发执行） |
| `--chain-length` | `N` | 否 |
| `--path-steps` | `N` | 否 |
| `--path-scheme` | `{linear,oneSidedBeta,beta,betaQuantile,sigmoid}` | 否 |
| `--log-every` | `N` | 否 |
| `--beast-version` | `{1.10.4,10.5.0}` | 否 |
| `--beagle-dir` | `DIR` | 否 |
| `--beagle-threads` | `N` | 否 |
| `--timeout` | `SECONDS` | 否 |
| `--csv` | `PATH` | 否 |
| `--html` | `PATH` | 否 |
| `--json` | `PATH` | 否 |
| `-q, --quiet` | `flag` | 否 |

**示例**

需要原生 BEAGLE；运行本身需要数分钟。

```
beastxpy compare --configs examples/config_subst_jc69.yaml examples/config_subst_gtr.yaml --method stepping-stone --workdir compare_runs
```

**输出**

表格输出到 stdout；--json/--csv/--html 写文件。

**退出码**

0 已比较，1 参数错误，2 环境不可用，3 不可比或 BEAST 失败。

### `beastxpy container`

逐字节原样打印随附的 Docker 或 Apptainer 定义。

**用法**

```
beastxpy container --print {docker,apptainer}
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `--print` | `{docker,apptainer}` | 是 |

**示例**

```
beastxpy container --print docker
```

**输出**

定义文件的原始字节输出到 stdout；不执行构建。

**退出码**

0 已打印，1 缺少 --print，2 定义不可用。

### `beastxpy diagnose`

标定诊断（Gate 2 细节），可选敏感性扫描。

**用法**

```
beastxpy diagnose --config CONFIG [--output-dir OUTPUT_DIR] [--report REPORT] [--sensitivity] [--no-visualize]
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `--config` | `CONFIG` | 是 |
| `--output-dir` | `OUTPUT_DIR` | 否 |
| `--report` | `REPORT` | 否 |
| `--sensitivity` | `flag` | 否 |
| `--no-visualize` | `flag` | 否 |

**示例**

```
beastxpy diagnose --config examples/config_advanced.yaml --output-dir diagnostics
```

**输出**

标定发现项输出到 stdout，可选生成图文件。

**退出码**

0 成功，1 配置或用法错误，2 环境不可用，3 校验或判定失败。

### `beastxpy fingerprint`

打印分析指纹、内容指纹与数据摘要。

**用法**

```
beastxpy fingerprint --config CONFIG [--output OUTPUT]
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `--config` | `CONFIG` | 是 |
| `--output` | `OUTPUT` | 否 |

**示例**

```
beastxpy fingerprint --config examples/config_basic.yaml
```

**输出**

指纹输出到 stdout，可选 JSON 文件。

**退出码**

0 成功，1 配置或用法错误，2 环境不可用，3 校验或判定失败。

### `beastxpy generate`

从 YAML 配置生成 BEAST 1 XML；加 `--beast-validate` 才运行 Gate 3。

**用法**

```
beastxpy generate --config CONFIG [--output OUTPUT] [--beast-validate] [--allow-unvalidated] [--no-data-check] [--allow-custom-fragments] [--beast-version {1.10.4,10.5.0,both}] [--force]
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `--config` | `CONFIG` | 是 |
| `--output` | `OUTPUT` | 否 |
| `--beast-validate` | `flag` | 否 |
| `--allow-unvalidated` | `flag` | 否 |
| `--no-data-check` | `flag` | 否 |
| `--allow-custom-fragments` | `flag` | 否 |
| `--beast-version` | `{1.10.4,10.5.0,both}` | 否 |
| `--force` | `flag` | 否 |

**示例**

```
beastxpy generate --config examples/config_basic.yaml --output out.xml --beast-validate
```

**输出**

未给 --output 时 XML 写到 stdout；Gate 进度写到 stderr。

**退出码**

0 成功，1 配置或用法错误，2 环境不可用，3 校验或判定失败。

### `beastxpy list-models`

列出已注册的模型、分子钟、树先验与分布。

**用法**

```
beastxpy list-models
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|

**示例**

```
beastxpy list-models
```

**输出**

表格输出到 stdout。

**退出码**

0 成功，1 配置或用法错误，2 环境不可用，3 校验或判定失败。

### `beastxpy lock`

写出环境锁：摘要、配置哈希与种子。

**用法**

```
beastxpy lock --config PATH --output PATH [--beast-version {1.10.4,10.5.0}] [--seeds 1,2,3] [--beagle-lib DIR] [--beagle-threads N] [--json PATH] [-q]
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `--config` | `PATH` | 是 |
| `--output` | `PATH` | 是 |
| `--beast-version` | `{1.10.4,10.5.0}` | 否 |
| `--seeds` | `1,2,3` | 否 |
| `--beagle-lib` | `DIR` | 否 |
| `--beagle-threads` | `N` | 否 |
| `--json` | `PATH` | 否 |
| `-q, --quiet` | `flag` | 否 |

**示例**

```
beastxpy lock --config examples/config_basic.yaml --output beastxpy.lock
```

**输出**

在 --output 写出封存的 JSON 锁；不含路径或凭据。

**退出码**

0 锁已写出，1 参数错误或配置不可读，2 写入失败。

### `beastxpy logcombine`

合并多个种子的 trace 日志或树日志。注意：`--burnin` 在纯 Python 路径按**日志行数**计，在 Java LogCombiner 路径按**状态数**计；所选 jar 可用时走 Java 路径。

**用法**

```
beastxpy logcombine --logs LOGS [LOGS ...] --output OUTPUT [--burnin BURNIN] [--resample RESAMPLE] [--strip] [--beast-version {1.10.4,10.5.0}]
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `--logs` | `LOGS` | 是 |
| `--output` | `OUTPUT` | 是 |
| `--burnin` | `BURNIN` | 否 |
| `--resample` | `RESAMPLE` | 否 |
| `--strip` | `flag` | 否 |
| `--beast-version` | `{1.10.4,10.5.0}` | 否 |

**示例**

请先运行 `beastxpy run --config examples/config_basic.yaml --seeds 1,2 --workdir runs/demo`。

```
beastxpy logcombine --logs runs/demo/1/seed_1_trace.log runs/demo/2/seed_2_trace.log --output combined.log
```

**输出**

在 --output 写出合并日志。

**退出码**

0 成功，1 配置或用法错误，2 环境不可用，3 校验或判定失败。

### `beastxpy methods`

生成 LaTeX 方法段落。

**用法**

```
beastxpy methods --config CONFIG [--output OUTPUT]
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `--config` | `CONFIG` | 是 |
| `--output` | `OUTPUT` | 否 |

**示例**

```
beastxpy methods --config examples/config_basic.yaml
```

**输出**

LaTeX 段落输出到 stdout。

**退出码**

0 成功，1 配置或用法错误，2 环境不可用，3 校验或判定失败。

### `beastxpy pipeline`

写出 Snakemake 或 Nextflow 流水线。

**用法**

```
beastxpy pipeline --config CONFIG [--type {snakemake,nextflow}] [--output-dir OUTPUT_DIR]
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `--config` | `CONFIG` | 是 |
| `--type` | `{snakemake,nextflow}` | 否 |
| `--output-dir` | `OUTPUT_DIR` | 否 |

**示例**

```
beastxpy pipeline --config examples/config_basic.yaml --type snakemake --output-dir pipeline
```

**输出**

在 `--output-dir`（不存在时自动创建）下写出流水线文件：`--type snakemake` 写出 `Snakefile`，`--type nextflow` 写出 `main.nf`；YAML 配置同时复制到该目录，使其自包含。CLI 只为流水线文件打印 `wrote <path>`。

**退出码**

0 成功，1 配置或用法错误，2 环境不可用，3 校验或判定失败。

### `beastxpy quick`

无需先写 YAML 的单分区生成。链长默认 1,000,000，并不是一个生产级长链默认值。

**用法**

```
beastxpy quick --alignment ALIGNMENT [--tree-prior TREE_PRIOR] [--subst-model SUBST_MODEL] [--clock-model CLOCK_MODEL] [--calibration-yaml CALIBRATION_YAML] [--chain-length CHAIN_LENGTH] [--name NAME] [--output OUTPUT] [--beast-version {1.10.4,10.5.0}] [--beast-validate] [--allow-unvalidated] [--force]
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `--alignment` | `ALIGNMENT` | 是 |
| `--tree-prior` | `TREE_PRIOR` | 否 |
| `--subst-model` | `SUBST_MODEL` | 否 |
| `--clock-model` | `CLOCK_MODEL` | 否 |
| `--calibration-yaml` | `CALIBRATION_YAML` | 否 |
| `--chain-length` | `CHAIN_LENGTH` | 否 |
| `--name` | `NAME` | 否 |
| `--output` | `OUTPUT` | 否 |
| `--beast-version` | `{1.10.4,10.5.0}` | 否 |
| `--beast-validate` | `flag` | 否 |
| `--allow-unvalidated` | `flag` | 否 |
| `--force` | `flag` | 否 |

**示例**

```
beastxpy quick --alignment examples/primates.fasta --subst-model hky --output quick.xml
```

**输出**

与 generate 相同，配置由命令行参数构成。

**退出码**

0 成功，1 配置或用法错误，2 环境不可用，3 校验或判定失败。

### `beastxpy report`

为运行目录渲染单个离线 HTML 报告。

**用法**

```
beastxpy report WORKDIR --output PATH [--lang {en,zh}] [--include-config] [--assessment PATH] [--ess-threshold N] [--no-plot] [--json PATH] [-q]
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `WORKDIR` | `--` | 是 |
| `--output` | `PATH` | 是 |
| `--lang` | `{en,zh}` | 否 |
| `--include-config` | `flag` | 否 |
| `--assessment` | `PATH` | 否 |
| `--ess-threshold` | `N` | 否 |
| `--no-plot` | `flag` | 否 |
| `--json` | `PATH` | 否 |
| `-q, --quiet` | `flag` | 否 |

**示例**

请先运行 `beastxpy run --config examples/config_basic.yaml --workdir runs/demo`。

```
beastxpy report runs/demo --output runs/demo/report.html --lang zh
```

**输出**

单个自包含 HTML 文件；--json 写出同样的数据。

**退出码**

0 已写出，1 参数或写入失败，2 工作目录不可用。

### `beastxpy reproduce`

逐字段验证锁（`--check`）或重放（`--run`）。`--run` 时每个种子的 `--timeout` 默认 86400 秒（24 小时）。

**用法**

```
beastxpy reproduce --lock PATH (--check | --run) [--config PATH] [--workdir DIR] [--beagle-lib DIR] [--beagle-threads N] [--timeout SECONDS] [--json PATH] [-q]
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `--lock` | `PATH` | 是 |
| `--check` | `flag` | 是，`--check`/`--run` 二选一 |
| `--run` | `flag` | 是，`--check`/`--run` 二选一 |
| `--config` | `PATH` | 否 |
| `--workdir` | `DIR` | 否 |
| `--beagle-lib` | `DIR` | 否 |
| `--beagle-threads` | `N` | 否 |
| `--timeout` | `SECONDS` | 否 |
| `--json` | `PATH` | 否 |
| `-q, --quiet` | `flag` | 否 |

**示例**

请先运行 `beastxpy lock --config examples/config_basic.yaml --output beastxpy.lock`。

```
beastxpy reproduce --lock beastxpy.lock --check --config examples/config_basic.yaml
```

**输出**

逐字段比对输出到 stdout；--run 重新执行 BEAST。

**退出码**

0 匹配或已运行，1 参数错误，2 不一致，3 锁损坏或被篡改。

### `beastxpy run`

在种子矩阵上运行 BEAST；需要原生 BEAGLE。每个种子的 `--timeout` 默认 86400 秒（24 小时）；长时间分歧时间分析可调高或放到后台运行。探测到的 BEAGLE 目录会自动暴露给 BEAST 进程（`-Djava.library.path` 加动态链接器路径）。`--java-only` 对应 BEAST 的 `-java` 开关；因为 BEAST 自带的纯 Java BEAGLE 从未实现位点对数似然入口，所以对任何含 `<treeLikelihood>` 的文档（也就是 BeastXPy 产出的全部文档），`--java-only` 都会在启动前被拒绝，退出码 1。

**用法**

```
beastxpy run --config CONFIG [--workdir WORKDIR] [--seeds SEEDS] [--parallel N] [--beast-version {1.10.4,10.5.0}] [--timeout TIMEOUT] [--resume] [--rerun-successful] [--checkpoint-every CHECKPOINT_EVERY] [--java-only] [--no-data-check] [--force]
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `--config` | `CONFIG` | 是 |
| `--workdir` | `WORKDIR` | 否 |
| `--seeds` | `SEEDS` | 否 |
| `--parallel` | `N` | 否（默认 1；按种子并发执行） |
| `--beast-version` | `{1.10.4,10.5.0}` | 否 |
| `--timeout` | `TIMEOUT` | 否 |
| `--resume` | `flag` | 否 |
| `--rerun-successful` | `flag` | 否 |
| `--checkpoint-every` | `CHECKPOINT_EVERY` | 否 |
| `--java-only` | `flag` | 否 |
| `--no-data-check` | `flag` | 否 |
| `--force` | `flag` | 否 |

**示例**

```
beastxpy run --config examples/config_basic.yaml --workdir runs/demo --seeds 1,2
```

**输出**

每个种子在工作目录下生成一个子目录。

**退出码**

0 全部种子成功；1 预检失败（含 `--java-only` 拒绝）或任一种子失败。

### `beastxpy trace`

纯 Python 的 trace 文件统计：ESS、ACT、HPD。

**用法**

```
beastxpy trace --log LOG [--burnin BURNIN] [--ess-threshold ESS_THRESHOLD] [--plot PLOT] [--json]
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `--log` | `LOG` | 是 |
| `--burnin` | `BURNIN` | 否 |
| `--ess-threshold` | `ESS_THRESHOLD` | 否 |
| `--plot` | `PLOT` | 否 |
| `--json` | `flag` | 否 |

**示例**

请先运行 `beastxpy run --config examples/config_basic.yaml --workdir runs/demo`。

```
beastxpy trace --log runs/demo/1/seed_1_trace.log
```

**输出**

统计表输出到 stdout，可选图与 JSON。

**退出码**

0 成功，1 配置或用法错误，2 环境不可用，3 校验或判定失败。

### `beastxpy tree-annotate`

为树日志标注 clade 支持度。注意：`--heights ca`（共同祖先高度）需要 Java TreeAnnotator；在 `--python-only` 或找不到可用 jar 时会被拒绝。

**用法**

```
beastxpy tree-annotate --trees TREES --output OUTPUT [--heights {keep,median,mean,ca}] [--burnin BURNIN] [--limit LIMIT] [--python-only] [--beast-version {1.10.4,10.5.0}]
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `--trees` | `TREES` | 是 |
| `--output` | `OUTPUT` | 是 |
| `--heights` | `{keep,median,mean,ca}` | 否 |
| `--burnin` | `BURNIN` | 否 |
| `--limit` | `LIMIT` | 否 |
| `--python-only` | `flag` | 否 |
| `--beast-version` | `{1.10.4,10.5.0}` | 否 |

**示例**

请先运行 `beastxpy run --config examples/config_basic.yaml --workdir runs/demo`。

```
beastxpy tree-annotate --trees runs/demo/1/seed_1_tree.log --output tree.tre
```

**输出**

在 --output 写出标注后的树文件。

**退出码**

0 成功，1 配置或用法错误，2 环境不可用，3 校验或判定失败。

### `beastxpy validate`

校验已有 XML；`--beast` 追加 Gate 3。

**用法**

```
beastxpy validate --xml XML [--beast] [--beast-version {1.10.4,10.5.0,both}] [--allow-unvalidated]
```

**参数**

| 参数 | 取值 | 必填 |
|---|---|---|
| `--xml` | `XML` | 是 |
| `--beast` | `flag` | 否 |
| `--beast-version` | `{1.10.4,10.5.0,both}` | 否 |
| `--allow-unvalidated` | `flag` | 否 |

**示例**

请先运行 `beastxpy generate --config examples/config_basic.yaml --output out.xml`。

```
beastxpy validate --xml out.xml --beast
```

**输出**

发现项输出到 stdout；不修改 XML。

**退出码**

0 成功，1 配置或用法错误，2 环境不可用，3 校验或判定失败。

## JSON 模式

| 命令 | `schema_version` |
|---|---|
| `assess` | `beastxpy.assessment/v1` |
| `check-data` | `beastxpy.datacheck/v1` |
| `report` | `beastxpy.report/v1` |
| `compare` | `beastxpy.compare/v1` |
| `lock` | `beastxpy.lock/v1` |
| `reproduce` | `beastxpy.reproduce/v1` |
| `capabilities` | `beastxpy.capabilities/v1` |

权威映射位于 `beastxpy/_contracts.py`。
