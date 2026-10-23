# 运行后评估

[English](../en/assessment.md) | [中文](assessment.md)

`beastxpy assess` 读取 `beastxpy run` 的输出，报告样本呈现出的样子。

## 第 1 步 —— 至少跑两个种子

```
beastxpy run --config demo.yaml --workdir runs/demo --seeds 1,2
```

两条链是 PSRF 与 ASDSF 的最低要求；正式研究通常跑四条或更多。

## 第 2 步 —— 评估

```
beastxpy assess runs/demo --json runs/demo/assessment.json
```

在刻意调短的链上：

```
assess: runs/demo -> verdict fail (2 chain(s), strict=False)
thresholds: burnin=0.1 ess=200.0 psrf=1.01 operator=[0.01, 0.95]
  seed 1: status=ok samples=201 burnin=20 parameters=12 operators=10
  seed 2: status=ok samples=201 burnin=20 parameters=12 operators=10
  multichain: 2 usable chain(s)
    PSRF (split-chain Gelman-Rubin ...): max R-hat 1.0306
    ASDSF: 0.0000
  reasons: 24 error(s), 11 warning(s), 4 info
    [error] ess-below-threshold: seed 1: ESS 81.8 for 'posterior' is below the requested threshold 200
    ...
```

退出码 3 = 失败，0 = 通过/警告（`--strict` 下警告为 1，输入不可读为 2）。短链上得到 `fail` 是正确答案，不是工具坏了。

## 第 3 步 —— 自上而下读错误

`ess-below-threshold` 指出哪些参数没有充分移动。解决办法是加长链（必要时调整算子），而不是调高 `--ess` 阈值。`psrf-above-threshold` 说明各链对参数位置意见不一。`bulk-ess-below-threshold` 与 `tail-ess-below-threshold` 说明位置或尾部采样不足。`asdsf-high`（警告阈值 0.05）说明各链对拓扑意见不一。

## 报告内容

* **逐参数** —— ESS、ACT、均值、中位数与 95% HPD（ESS 采用 Geyer 初始单调序列估计量，Geyer 1992）。
* **多链 PSRF** —— 经典分半链 R-hat（Gelman & Rubin 1992；分半链变体见 Gelman 等 2013）**以及** 秩归一化分半链 R-hat（Vehtari 等 2021），并为每个参数给出 bulk ESS 与 tail ESS。阈值检查优先使用秩归一化值，无法计算时退回经典 R-hat；两列都会出现在 HTML 报告和 JSON（`multichain.rank_diagnostics`）中。
* **拓扑 ASDSF** —— 分裂频率的平均标准差（MrBayes 惯例，Ronquist & Huelsenbeck 2003）。
* **Operator** —— 每个移动的接受率，标记低于 1%、高于 95% 与从未触发的移动。
* **树摘要** —— 树高、钟速率、`ucld.mean`、`ucld.stdev` 及其 HPD（当相应列存在）。

## 最重要的一条规则

可用链少于两条时，PSRF 与 ASDSF 报告为 `not_computable` 并给出原因。既然不存在这个数值，就不编造。某列在日志中缺失时同理。

## 判定

| 判定 | 退出码（默认） | 退出码（`--strict`） |
|---|---|---|
| `pass` | 0 | 0 |
| `warn` | 0 | 1 |
| `fail` | 3 | 3 |

`fail` 表示越过了你配置的阈值；`warn` 表示值得看一眼，但没到错误阈值。

## 判定不是什么

`pass` 只意味着没有越过你设置的阈值，**不**意味着链收敛了。ESS 与 R-hat 是有限样本估计，在短链上噪声很大；一条尚未探索后验的链也可能看起来正常。

`assess` 是诊断工具。它不做模型选择（`compare` 负责边际似然），也不判断模型是否适合你的数据。

每个参数列还带 `per_chain`：每条链各自的 `act`、`ess`、`act_max_lag` 与 `act_truncated`，因此即使合并的 `act_truncated` 为真，也能定位到具体链。

## 阈值与输出

默认值——ESS 200、PSRF 1.01、ASDSF 0.05、算子接受率 0.01-0.95——是惯例而非定律：ESS 与 PSRF 建议沿用 BEAST 书（Drummond & Bouckaert 2015），ASDSF 阈值沿用 MrBayes 惯例。每一个都可以在命令行上调整；一手文献汇总在 [references](references.md)。

```
beastxpy assess runs/demo --burnin 0.1 --ess 200 --psrf 1.01 --html report.html
```

`--burnin` 是从每条链开头丢弃的比例（0..1）；`--burnin auto` 使用与 `beastxpy trace --burnin auto` 相同的 Geweke 自动 burn-in，并按链记录有效比例。若没有可计算的 Geweke 分数（样本少于 100，或所有参数恒定），该链标记为 `burnin_status: not_computable`，产生 warning finding，并把 verdict 降级为 `warn`；此时 burn-in 0 只是因为无法丢弃样本，明确不是收敛估计。请改传显式比例。`--html` 在 JSON 旁生成一个自包含摘要页。JSON 信封（`beastxpy.assessment/v1`）携带 `verdict`、`counts`、`chains`、`multichain`、`reasons` 与 `limitations`。

## 与参考实现的一致性

`tests/golden/reference_diagnostics/` 中两组固定模拟数据把数值行为钉在社区工具上（由 `tests/test_reference_diagnostics.py` 验证）：

* **ArviZ 0.23.4**（`rank` R-hat、`bulk`/`tail` ESS）：在四条固定 AR(1) 链上，rank-normalized split R-hat、bulk ESS 与 split-chain tail ESS 与 ArviZ 在打印精度内一致（相对差 < 1e-9）。ESS **不封顶于 `m*n`**：存储的反相关夹具只有 1,000 个抽样，但 BeastXPy 与 ArviZ 都给出 ESS 3,000，因为反相关抽样可以比独立抽样携带更多信息。这是参考实现的行为，不是上界声明。
* **Tracer 1.7.2**（固定 BEAST 风格日志）：均值与 95% HPD 端点相对差 < 1e-9，ESS 与 ACT 相对差 < 0.1%，中位数差 < 0.002（Tracer 的中位数插值不同）。ACT 以抽样数计；Tracer 以记录状态数计，比较前需除以记录间隔。

95% HPD 是包含 `ceil((1-alpha) * n)` 个观测的最短窗口。小样本时 Tracer 可能选择相差一个顺序统计量的窗口；该差异被记录而不是隐藏。单链 ACT 保留无偏 `n/(n-lag)` 自协方差修正；在固定夹具上与 Tracer 相差 0.06%。多链 ESS 采用 Vehtari et al. (2021) 边际方差分解所需的有偏自协方差。
