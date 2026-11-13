# BEAUti 约定参照夹具

[English](README.md) | [中文](README.zh-CN.md)

这三个 XML 子集是 `tests/test_beauti_reference.py` 的外部参照。它们是**手工从 BEAUti 1.10.4 源码默认值誊录而来**，而非由一次实时的 BEAUti 运行产出。也曾针对 1.10.4 与 10.5.0 构建中的 BEAUti 生成器 API 尝试过一个无头驱动；它的默认输出被 BEAST 自身拒绝（`coalescentTree` 引用了一个作为 `coalescentSimulator` 发出的对象），所以下面的参照改为从源码默认值誊录。它们的取值来自：

- `src/dr/app/beauti/options/PartitionTreePrior.java`（Yule 出生率、出生死亡参数）；
- `src/dr/app/beauti/options/PartitionSubstitutionModel.java`（kappa、gamma 形状、GTR 速率、碱基频率）；
- `src/dr/app/beauti/options/PartitionClockModel.java`（钟速率、UCLN 均值与标准差）；
- `src/dr/app/beauti/generator/ParameterPriorGenerator.java`（每个先验的 XML 形状）；
- `examples/Benchmarks/benchmark1.xml`，一份随 BEAST 1.10.4 源码树一同分发的 BEAUti v1.6.0pre 输出，用于确认 XML 形状以及 kappa/stdev 约定。

每个文件都含有一段注释，列出它与本生成器之间已有记录的差异。测试会显式断言每一处差异，因此将来若有一个改动意外地“修复”或恶化了某个约定，测试就会立刻失败并明确报错。参照文件并不是可运行的 BEAST 文档（它们省略了分类单元与某些 id）；只比较模型、先验与似然片段。

## 比较的约定

| 约定 | BEAUti 1.10.4 | BeastXPy | 状态 |
|---|---|---|---|
| `yule.birthRate` 先验 | lognormal（对数均值 2、对数标准差 1.5） | 相同 | 一致 |
| `kappa` 先验 | lognormal（对数均值 1、对数标准差 1.25） | 相同 | 一致 |
| `ucld.stdev` 先验 | exponential（均值 1/3） | 相同 | 一致 |
| `relativeDeathRate` 先验 | uniform（0, 1） | 相同 | 一致 |
| 钟速率先验 | CTMC 速率参考 | `oneOnXPrior` | 有记录的偏离 |
| gamma 形状先验 | exponential（均值 0.5） | lognormal（对数均值 1、对数标准差 1.25） | 有记录的偏离 |
| GTR 速率先验 | gamma（shape 0.05、scale 20） | uniform（0, 100） | 有记录的偏离 |
| 碱基频率 | Dirichlet（1）或固定值 | 在单纯形上的 uniform | 有记录的偏离 |
| 标定 `meanInRealSpace` | 默认为 `true` | 依 YAML 而定（省略即 `false`） | 有记录，Gate 2 会告警 |
| 标定 XML | 对标定数据使用 `distributionLikelihood` | 对 `tmrcaStatistic` 的直接先验 | 有记录，两者均被 BEAST 接受 |

这些偏离是 BeastXPy 刻意的选择，而非隐藏的 BEAUti 默认值；每一处都会在生成的 XML 注释、Gate 2 以及 `docs/zh/configuration.md` 中被点名。
