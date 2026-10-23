# 可复现性

[English](../en/reproducibility.md) | [中文](reproducibility.md)

有两类产物让一次运行日后仍可核查：嵌在 XML 里的指纹，以及记录环境的锁文件。

## 指纹

```
beastxpy fingerprint --config demo.yaml
```

打印只含三个键的 JSON：`fingerprint`（`BXP-<hash12>-<工具版本>`）、`data_digest` 与 `beastxpy_version`。同样的指纹会作为注释嵌入每份生成的 XML，因此文档可以追溯到产生它的配置。`generate` 的输出旁还会写伴生文件（`demo.fingerprint.json`）。

指纹负载刻意排除非科学元数据、日志文件名与溯源备注；包含 `control.seed` 与指纹嵌入开关，因为它们会改变分析本身。

`methods` 写出的上文 LaTeX 段落会为分析用到的每个模型（替换模型、分子钟、树先验、标定密度、MCMC 方案）嵌入 `\citep` 引用命令，并在末尾以 LaTeX 注释列出每个引用键的完整文献——见 [references](references.md)。

## 锁

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

锁记录：

* BeastXPy、Python 与平台
* Java 版本与架构
* BEAST jar 的 **SHA-256** 及其来源
* 原生 BEAGLE 的版本与 **SHA-256**
* 关键依赖的版本
* 配置的**内容哈希**
* 种子

只记录文件名与摘要，绝不记录绝对路径；写入前还会扫描形似凭据的值。文档用逐字段摘要封存，任何改动都可被发现。

## 校验（`--check`）

```
beastxpy reproduce --lock beastxpy.lock --check --config demo.yaml
```

```
reproduce: mode=check verdict=match
  25 field(s) match
```

`--check` 逐字段比对当前机器，任何不匹配都以非零码退出（2 = 字段不同，3 = 锁文件损坏或被编辑过）。如果环境真的变了，重新生成锁；不要手改。

不带 `--config` 时无法核验配置内容字段，verdict 为 `match_partial`（退出码 0），原因列表会逐项说明哪些字段未核验。只有所有记录字段都比较过才会打印 `match`；不要把 `match_partial` 引用为“环境完全一致”。

## 重放（`--run`）

```
beastxpy reproduce --lock beastxpy.lock --run --config demo.yaml --workdir runs/replay
```

`--run` 走正常 runner 重新执行，因此重放与普通运行是同一条代码路径。它要求同时给出 `--config` 与 `--workdir`。

## 边界

锁能固定版本与文件身份，但固定不了操作系统的数学库、CPU 的浮点行为或 BEAST 自己的随机数实现。不承诺跨机器逐位一致；承诺的是输入可复现。

两个已知边界被显式记录：

* **Java**：只锚定启动器可执行文件的 SHA-256，以及报告的版本、供应商与架构；JRE 的其余部分（模块、共享库、信任库）不参与哈希。一个保留启动器与版本字符串的重新打包 JRE 不会被发现。
* **BEAGLE**：同时记录期望版本（配置）与观测版本（运行时）以及 JNI 库的 SHA-256。`reproduce --check` 会比较观测版本，因此未声明的 BEAGLE 升级即使库哈希未变也会报告为字段不匹配。
