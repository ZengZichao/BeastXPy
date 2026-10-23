# 常见问题

[English](../en/faq.md) | [中文](faq.md)

**需要安装 BEAST 吗？** 不需要。两个 jar 都在 `beastxpy/tools/jars/`。Gate 3 与运行 BEAST 需要 JDK，真实似然需要原生 BEAGLE。

**需要原生 BEAGLE 吗？** `run` 与 `compare` 需要；`generate`、`validate`、`check-data`、`assess`、`report`、`lock` 不需要。

**为什么 `run --java-only` 直接拒绝启动？** BEAST 自带的纯 Java BEAGLE 从未实现 `getSiteLogLikelihoods`，因此 `-java` 在任何含 `<treeLikelihood>` 的文档上都会中止——而 BeastXPy 生成的每份文档都含这个元素。请改用原生 BEAGLE。见[原生 BEAGLE](native-beagle.md)。

**为什么 `assess` 显示 `not_computable`？** PSRF 与 ASDSF 至少需要两条链，多跑几个种子。某个统计量缺失意味着日志里没有这一列，而不是数值为零。

**为什么短测试链上 `assess` 判定失败？** 因为链短。这正是正确的报告；短链 ESS 很低，不应通过。

**`pass` 是收敛结论吗？** 不是。参见[能力边界](limitations.md)。

**能用我自己的 XML 吗？** 可以——那正是 BEAST 的用途。`custom_fragments` 只支持一个很小的惰性元素白名单，并且总是强制 Gate 3。

**为什么拒绝 `codon`？** 两个版本的官方 BEAST 解析器都无法应用 `<convert dataType="codon">`。请用 `srd06`，或换一个解析器能接受的 BEAST 构建。

**没有 BEAGLE 时 `compare` 能用吗？** 不能。它以退出码 2 结束且不报告任何数字。

**如何在另一台机器上复现？** 在那台机器上 `beastxpy lock`，在这里 `beastxpy reproduce --check`。

**支持哪些 Python 版本？** 3.10 与 3.11。

**能在容器里运行吗？** 可以：`docker build -f deploy/Dockerfile .`。参见[容器](container.md)。
