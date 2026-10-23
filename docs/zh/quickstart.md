# 快速上手

[English](../en/quickstart.md) | [中文](quickstart.md)

本指南在自带的 primates 比对（898 bp、12 个分类单元）上完整跑通一个分析：JC69 + 严格钟 + Yule + 一个根标定。每一步都给出命令、应当看到的输出，以及进入下一步之前需要确认的事项。

**开始之前**：先完成[安装](installation.md)。要求 `beastxpy --version` 可用，且 `beastxpy check-environment` 能找到 JDK 和两个 BEAST jar。第 4 步的运行还需要原生 BEAGLE 库（见 [native-beagle](native-beagle.md)）；在编写本指南的参考机器上，用下面几个环境变量指向它：

```
export JAVA_HOME=/path/to/jdk-17
export BEASTX_BEAGLE_1_10_4_LIB=/path/to/beagle-3.1.x/lib
export BEASTX_BEAGLE_10_5_0_LIB=/path/to/beagle-4.0.x/lib
```

建立工作目录并拷入示例数据：

```
mkdir -p ~/beastxpy-demo && cd ~/beastxpy-demo
cp <repo>/examples/primates.fasta .
```

## 第 1 步 —— 编写分析配置

创建名为 `demo.yaml` 的文件：

```yaml
metadata:
  analysis_name: demo
alignments:
  - id: a1
    file: primates.fasta
    data_type: nucleotide
partitions:
  - id: p1
    alignment_id: a1
    from: 1
    to: 898
    site_model:
      substitution_model: {type: jc69}
    clock_model: {type: strict}
tree_prior: {type: yule}
calibrations:
  - name: root
    taxa: null
    distribution: {type: lognormal, parameters: {mean: 3.4, stdev: 0.5}}
mcmc: {chain_length: 100000, pre_burnin: 0}
loggers:
  trace_log: {file_name: demo.log, log_every: 500}
  tree_log: {file_name: demo.trees, log_every: 500}
output: {file: demo.xml}
control: {beast_version: "1.10.4", seed: 1}
```

逐字段说明：

| 配置块 | 含义 |
|---|---|
| `metadata.analysis_name` | 成为所有产物的文件名主干；必须匹配 `[A-Za-z0-9][A-Za-z0-9._-]*` |
| `alignments` | 输入 FASTA；路径**相对本 YAML 文件解析**；`data_type: nucleotide` 固定字符集 |
| `partitions` | 比对的哪一段用什么模型；`from`/`to` 为 1 基闭区间 |
| `tree_prior` | 树模型；`yule` 只有一个参数（`birth_rate`） |
| `calibrations` | `taxa: null` 表示标定根节点。这里的 `mean` 在**对数尺度**上（加 `meanInRealSpace: true` 则按真实尺度解释），且必须为正 |
| `mcmc.chain_length` | MCMC 步数。100000 只是冒烟测试值；正式分析需要数百万步 |
| `loggers` | 输出日志文件名；`log_every: 500` 得到 `chain_length / 500 = 200` 个样本 |
| `control` | 目标 BEAST 版本和默认随机种子 |

实际项目通常从示例配置（`examples/config_*.yaml`）复制一份再修改。

## 第 2 步 —— 数据预检（开销极小，能抓住大多数错误）

```
beastxpy check-data --config demo.yaml
```

预期输出：

```
check-data: demo.yaml
verdict: pass (0 error(s), 0 warning(s))
no data or calibration problems found
```

退出码 `0` 表示干净，`1` 表示仅有警告，`2` 表示有错误。如果犯了经典错误（对数尺度均值写成 `mean: -3.5`），Gate 0 会先拒绝整个配置：

```
verdict: fail (1 error(s), 0 warning(s))
[error] config-parse-error: Gate 0 rejected this config: calibrations[0].distribution.parameters.mean: must be > 0 for lognormal
```

按提示修复，重跑直到 verdict 为 `pass`。

## 第 3 步 —— 生成 BEAST XML 并验证

```
beastxpy generate --config demo.yaml --output demo.xml --beast-validate
```

预期输出（进度在 stderr，"wrote" 行在 stdout）：

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
wrote demo.xml
```

每一行证明了什么：

* **Gate 0** —— YAML 通过严格解析且自洽。
* **Gate 1** —— 生成的 XML 结构上是合法的 BEAST 1 文档。
* **Gate 2** —— 标定可用且互不矛盾。`Gate 2 [info]` 是提示而不是错误：它如实说明对数尺度标定均值的实际含义，并逐一列出 BeastXPy 自动添加的先验（可在 `parameter_priors` 中覆盖）。
* **Gate 3** —— **官方** BEAST 1.10.4 jar 解析了文档、构建了模型图、并评估出有限的初始后验。方括号里是本机支持的最强模式：有原生 BEAGLE 时为 `beagle`（且没有 WARNING 行），否则为 `java-core`（见 [validation](validation.md)）。

退出码 0 表示**每个被请求的门都真实运行且通过**。该命令还会在 XML 旁边写两个伴生文件：`demo.fingerprint.json` 和 `demo_methods.tex`。

## 第 4 步 —— 运行 BEAST（两个种子）

```
beastxpy run --config demo.yaml --workdir runs/demo --seeds 1,2
```

预期输出：

```
  seed 1: OK in 3.9s [log: runs/demo/1/run.log]
    trace: runs/demo/1/seed_1_trace.log
    trees: runs/demo/1/seed_1_tree.log
  seed 2: OK in 4.6s [log: runs/demo/2/run.log]
    trace: runs/demo/2/seed_2_trace.log
    trees: runs/demo/2/seed_2_tree.log
fingerprint: BXP-a836c87ba7de-0.1.0 (content: BXP-a836c87ba7de6055)
```

两个种子是第 5 步链间统计量的最低要求；正式分析建议四个或更多。工作目录结构：

```
runs/demo/
├── demo.xml                 master XML
├── assessment.json          (step 5)
├── report.html              (step 6)
├── 1/
│   ├── demo.xml             per-seed copy with logger names rewritten
│   ├── run.log              BEAST stdout+stderr
│   ├── run_meta.json        machine-readable run record
│   ├── seed_1_trace.log     parameter log
│   └── seed_1_tree.log      tree log
└── 2/
    └── ...                  same layout for seed 2
```

即：`demo.xml` 为主 XML；`assessment.json`、`report.html` 分别由第 5、6 步生成；每个种子子目录里有改写过日志名的 `demo.xml` 副本、BEAST 的 `run.log`、机器可读的 `run_meta.json`、参数日志 `seed_N_trace.log` 与树日志 `seed_N_tree.log`。

**如果运行几乎立刻停止**，且各种子的 `run.log` 里出现 `No acceptable BEAGLE library plugins found`：说明 JVM 没有拿到原生 BEAGLE。检查 `beastxpy check-environment`，设置 `BEASTX_BEAGLE_1_10_4_LIB` 后重跑。BeastXPy 拒绝产出"看起来像真的"链：BEAST 1 的 XML 计算似然必须有原生 BEAGLE。（对含 `<treeLikelihood>` 的文档，`--java-only` 会被直接拒绝——见 [cli](cli.md)。）

BeastXPy 会把探测到的原生 BEAGLE 自动暴露给 BEAST 进程（`-Djava.library.path` 加动态链接器路径），所以安装阶段设置的环境变量就足够了。

## 第 5 步 —— 评估 MCMC 输出

```
beastxpy assess runs/demo --json runs/demo/assessment.json
```

在这条刻意调短的链上，结论是 `fail`，而这就是正确答案（10 万步的链 ESS 只有几十）：

```
assess: runs/demo -> verdict fail (2 chain(s), strict=False)
thresholds: burnin=0.1 ess=200.0 psrf=1.01 operator=[0.01, 0.95]
  seed 1: status=ok samples=201 burnin=20 parameters=12 operators=10
  seed 2: status=ok samples=201 burnin=20 parameters=12 operators=10
  multichain: 2 usable chain(s)
    PSRF (...): max R-hat 1.0306
    ASDSF: 0.0000
  reasons: 24 error(s), 11 warning(s), 4 info
    [error] ess-below-threshold: seed 1: ESS 81.8 for 'posterior' is below the requested threshold 200
    ...
```

解读要点：

* **逐参数 ESS/ACT/HPD** —— `ess-below-threshold` 错误指出哪些参数采样不足。解决办法是加长链，而不是调高阈值。
* **PSRF** —— 至少需要两条可用链；只有一个种子时报告 `not_computable` 并附原因，绝不编造数值。
* **ASDSF** —— 链间拓扑一致性；这里 `0.0000` 表示两条链对每个分裂都一致。
* **算子** —— 接受率；低于 1% 或高于 95% 的移动会被标记。

退出码：`0` 通过/警告，`1` 为 `--strict` 下的警告，`2` 输入不可读，`3` 失败。这里的 `fail` 描述的是样本状态，不是"模型错了"的判断（见 [assessment](assessment.md) 与 [limitations](limitations.md)）。

## 第 6 步 —— 生成离线 HTML 报告

```
beastxpy report runs/demo --output runs/demo/report.html --lang zh
```

预期输出：

```
report: runs/demo/report.html
conclusion: completed
chains: 2
assessment: present (verdict fail)
```

（完整输出还会回显 `language: zh`；用 `--lang en` 时回显 `language: en`。）

单个自包含 HTML 文件：不依赖外部 CSS/JS/字体，图片内嵌，无网络也能从 `file://` 打开。`--lang en` 生成完整英文报告。如果颠倒顺序、在 `assess` 之前调用 `report`，报告会如实标注 **not assessed/未评估**，绝不补造数字。

## 第 7 步 —— 固定环境并校验

```
beastxpy lock --config demo.yaml --output beastxpy.lock --seeds 1,2
```

```
lock: beastxpy.lock
  BEAST 1.10.4 jar 0f6eb4fdf7832c51 (reference-install)
  Java 17.0.20.1 64-bit (JAVA_HOME)
  native BEAGLE 3.1.0 7fcbc7d572bdcde0 threads=1
  dependencies: 6 recorded
  config demo.yaml sha256 8e831900a5682404
  seeds: [1, 2]
  the lock records basenames and digests only; no paths or credentials
```

然后校验当前机器是否仍与锁定文件一致：

```
beastxpy reproduce --lock beastxpy.lock --check --config demo.yaml
```

```
reproduce: mode=check verdict=match
  24 field(s) match
```

退出码 0 = 当前环境就是锁定文件描述的环境。任何漂移（jar 不同、Java 不同、锁定文件被改动）都以非零码退出；被编辑过的锁定文件直接以退出码 3 拒绝。见 [reproducibility](reproducibility.md)。

## 下一步

* [configuration](configuration.md) —— 每个 YAML 键、模型名、先验。
* [cli](cli.md) —— 全部 21 个命令的精确参数与退出码。
* [validation](validation.md) —— 四道门各自证明与不证明什么。
* [assessment](assessment.md) —— 诚实解读 ESS/PSRF/ASDSF。
* [compare](compare.md) —— 边缘似然与贝叶斯因子。
* [references](references.md) —— 本指南用到的每个统计量、阈值与模型的一手文献。
* [troubleshooting](troubleshooting.md) —— 症状、原因、修法。
