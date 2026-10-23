# 自定义 XML 片段

[English](../en/extensions.md) | [中文](extensions.md)

`custom_fragments` 把手写 XML 拼接进生成的文档。它**默认关闭**，也是这里最危险的功能，因此限制很紧。

## 第 1 步 —— 编写片段块

在配置中加入：

```yaml
custom_fragments:
  enabled: true
  fragments:
    - name: root-height-stat
      xml: '<treeHeightStatistic id="rootHeight"> <treeModel idref="treeModel"/> </treeHeightStatistic>'
      anchor_id: speciation
      position: before
```

一个条目就是一个片段。`xml` 接内联字符串；`file` 接片段文件路径（二者二选一）。锚点用 `anchor_id`（已有元素 id，如 `speciation`）或 `anchor_tag`（元素名）指定，`position` 取 `before`、`after` 或 `inside`（`inside` 只能用于 `<beast>`）。

可以在配置里设 `enabled: true`，也可以保持 false 并在命令行传 `--allow-custom-fragments`。两者都没有时，该块被忽略。

## 第 2 步 —— 允许片段并生成

```
beastxpy generate --config demo.yaml --output demo.xml --allow-custom-fragments
```

拼接后的文档与其他文档一样过全部门。Gate 0 会拒绝坏片段，给出具体原因和 `next step:` 提示：

```
error: refusing to generate: fragment 'root-height-stat': anchor ambiguous -- 3 elements match tag=<speciationLikelihood>
  next step: An anchor must match exactly one element so the splice is deterministic. Use anchor_id instead of anchor_tag, or pick a unique tag from: 'HKYModel', 'alignment', 'beast', ...
```

`speciationLikelihood` 这类标签会连同 `<speciationLikelihood idref=...>` 引用一起计数，因此常常不唯一——优先用 `anchor_id`。

## 白名单允许什么

片段可以声明分类单元与分类单元集合、数值 `parameter` 元素、只读统计量（`tmrcaStatistic`、`treeHeightStatistic`、`monophylyStatistic`、`branchLengthStatistic`、仅 `idref` 的 `statistic`）、现有树的视图（仅 `idref` 的 `treeModel`），以及独立分布先验（`logNormalPrior`、`normalPrior`、`uniformPrior` 等）。`mrca` 与 `taxon` 构建 clade；这里的任何东西都不能替换 BeastXPy 自己的机制。

## 会被拒绝的情况

* 不是良构 XML
* 元素或属性不在白名单内
* id 重复，或片段重定义已存在的 id
* `idref` 无法解析
* `<!DOCTYPE>` 与实体声明（外部实体永远不会被展开）
* 逃出配置目录的 `file:` 路径，以及绝对路径
* 任何触及 `mcmc`、日志器、operator 或其他核心块的改动

## Gate 3 是强制的

拼接后的文档在溯源记录中标记为 `custom`，并且**必须**通过官方 BEAST jar 的 Gate 3。校验失败则拒绝生成——`--allow-unvalidated` 不能覆盖这一点。

## 一句提醒

白名单让片段保持"惰性"：可以声明分类单元集合、参数、统计量与独立分布，但不能悄悄替换 BeastXPy 自己的机制。如果你需要的东西不在名单上，诚实的答案是另写一份 BEAST XML，而不是用片段。
