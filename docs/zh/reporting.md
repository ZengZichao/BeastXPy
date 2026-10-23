# 报告

[English](../en/reporting.md) | [中文](reporting.md)

`beastxpy report` 把一个运行目录变成单个 HTML 文件。

## 第 1 步 —— 先做（或有意跳过）评估

报告在能找到评估结果时会将其纳入。正常顺序：

```
beastxpy assess runs/demo --json runs/demo/assessment.json
```

如果没有评估就调用 `report`，报告会写 **not assessed / 未评估**，并且不显示任何诊断数字——绝不估算或回填。

## 第 2 步 —— 渲染

```
beastxpy report runs/demo --output runs/demo/report.html --lang zh --include-config
```

预期输出：

```
report: runs/demo/report.html
conclusion: completed
chains: 2
assessment: present (verdict fail)
```

（完整输出还会回显 `language: zh`；用 `--lang en` 时回显 `language: en`。）

文件不引用任何外部 CSS、JavaScript、字体或图片：在无网络的机器上直接以 `file://` 打开即可。图形以 `data:` URI 内联。树日志本身仍是标准 BEAST NEXUS 文件——需要交互式查看（旋转、定年、着色）时请用 FigTree 或 iTOL 打开；报告是静态、可分享的摘要，不是树查看器。

## 章节

结论、溯源与指纹、Gate 0-3 状态、自动先验（当生成 XML 含有 `BXP auto prior` 注释时）、诊断、运行元数据、trace 与评估摘要、带哈希的文件清单，以及能力边界。

自动先验一节列出参数、发出的分布与 XML 来源。内容从生成文档中提取，不是从 YAML 推断；工作目录没有生成 XML 时会明确说明。

## 诚实规则

* 没有运行 `assess` 时，报告明确写 **not assessed / 未评估**，并且不显示任何诊断数字，绝不估算或回填。
* 未运行的闸门显示为 `not-run` 并附原因，绝不会显示成对勾。
* 所有用户提供的字符串在写入前都做 HTML 转义。

## 语言

`--lang en` 与 `--lang zh` 产出完整报告；不存在混排输出，中文报告中不会回退成英文。

## 机器可读输出

```
beastxpy report runs/demo --output report.html --json report.json
```

JSON 携带与 HTML 相同的数值，模式为 `beastxpy.report/v1`。

## 选项

`--include-config` 嵌入运行目录中找到的配置；`--assessment PATH` 指定默认查找之外的评估文件；`--no-plot` 跳过轨迹图以减小文件；`--ess-threshold N` 改变报告绘制的 ESS 参考线，不会重新运行评估。
