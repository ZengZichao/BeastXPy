# 模型比较

[English](../en/compare.md) | [中文](compare.md)

`beastxpy compare` 用 BEAST 自带的路径采样或步进 stone 估计对数边际似然，再给出两两对数贝叶斯因子。

## 第 1 步 —— 每个模型准备一份配置

每份配置必须描述**同一份数据**，只有模型不同。文件名按所载模型命名：

```
demo.yaml       JC69 + strict clock + Yule
demo_hky.yaml   HKY  + strict clock + Yule
```

复制配置后只改一处（这里只改 `substitution_model`）。当比对内容、分类单元集合、分区布局或目标版本不一致时，`compare` 会拒绝（退出码 3，一个 BEAST 都不跑）。

## 第 2 步 —— 运行比较

```
beastxpy compare --configs demo.yaml demo_hky.yaml --method stepping-stone \
    --workdir compare_runs --seeds 1 --chain-length 10000 --path-steps 4
```

`--chain-length` 是每个路径步的链长，`--path-steps` 默认 8；上面的数值用于几秒钟内冒烟测试机制本身。要得到可发表的估计，用 `--path-steps 50` 到 `100`，链长按数据量决定。输出的样子：

```
probing environment for BEAST 1.10.4 ...
checking comparability of 2 configurations ...
  alignment.files              match
  ...
environment: java 17.0.20.1 (64-bit), jar beast.jar, BEAGLE libhmsbeagle-jni.so
running demo (1 seeds, chainLength=10000, pathSteps=4) ...
  seed 1: BEAST exit=0 (3.4 s)
  seed 1: ok
running demo_hky (1 seeds, chainLength=10000, pathSteps=4) ...
  seed 1: BEAST exit=0 (3.3 s)
  seed 1: ok
BeastXPy model comparison -- method: stepping_stone
...
models:
  demo                     ok       logML = -6393.838363  SE = 1.268922
  demo_hky                 ok       logML = -6074.787979  SE = 0.819432

pairwise log Bayes factors (a - b):
  demo vs demo_hky: logBF = -319.050384 (SE 1.510508)
      2 ln BF = 638.1008: very strong evidence for the second model (b) (Kass & Raftery band; a reporting convention, not a decision rule)
```

每个种子都是一次独立估计。文本摘要、CSV 与 HTML 会列出逐 seed 的 log 边际似然以及模型内极差（`max - min`），因此即使合并均值看起来很精确，种子敏感性也仍然可见。可用 `--parallel N` 并发运行各种子（默认 1）；模型之间仍顺序执行，因为它们共享同一个 BEAGLE 环境。

## 内部流程

1. 检查配置是否可比：比对内容、分类单元集合、分区布局是否一致。不一致就以退出码 3 停下，一个 BEAST 都不跑。
2. 为每个模型派生一份带边际似然分析块的配置并生成 XML。
3. 运行真实 BEAST，再从结果文件读取边际似然与标准误——每个数字都与 BEAST 实际写出的日志交叉核对。
4. 写出两两对数贝叶斯因子。

## 为什么失败关闭

路径采样需要原生 BEAGLE。缺失时 `compare` 以退出码 2 结束且不给出任何数字，从不用占位日志或估计值冒充测量值。

## 如何读贝叶斯因子

输出是 `2 ln BF`，沿用常见约定（Kass & Raftery 1995）；边际似然来自 BEAST 自带的路径采样与 stepping-stone 估计量（Xie 等 2011；Baele 等 2013——见 [references](references.md)）。正值越大越支持第一个模型，负值越大越支持第二个；Kass-Raftery 分档是**报告约定，不是决策规则**。

三点提醒：

* 贝叶斯因子只比较你给它的模型，对没试过的模型一无所知。
* 链太短、路径步数太少或混合不佳都会让估计不稳。标准误一并给出就是为了让你看到这一点：解释串会说明 `|log BF|` 是否超过 `2 * SE`。若 log BF 为 2 而标准误为 3，输出会写明“与零无法区分”，那等于没有证据。
* 报告的标准误是 delta-method 下界：路径步被当作彼此独立，步间残余相关没有建模；真实 Monte-Carlo 误差可能更大。

## 输出

`--json` 写 `beastxpy.compare/v1`（模型、两两比较、BEAGLE 探测、设置）；`--csv` 与 `--html` 写表格。
