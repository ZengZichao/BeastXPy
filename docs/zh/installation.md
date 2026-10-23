# 安装

[English](../en/installation.md) | [中文](installation.md)

按顺序执行以下步骤；每一步都以一条可运行的检查收尾。

## 第 1 步 —— 确认依赖

| 组件 | 用途 | 说明 |
|---|---|---|
| Python 3.10+ | 全部功能 | 已在 3.10、3.11 上测试 |
| JDK 8+（推荐 17） | Gate 3、运行 BEAST | 使用原生 BEAGLE 时必须是 64 位 |
| BEAST jar | Gate 3、运行 BEAST | 仓库内自带 1.10.4 与 10.5.0 |
| 原生 BEAGLE | 真实 MCMC 似然 | 1.10.4 配 3.1.x，10.5.0 配 4.0.x |

`generate`、`validate`、`check-data`、`assess`、`report`、`lock` 等大多数命令无需原生 BEAGLE；`run` 和 `compare` 必须有（见 [native-beagle](native-beagle.md)）。

## 第 2 步 —— 安装软件包

在本仓库的检出版本中执行：

```
python -m pip install .
beastxpy --version
```

预期输出：

```
beastxpy 0.1.0
```

如果 `beastxpy` 可执行文件不在 `PATH` 上（`pip install --user` 常见），检查 `~/.local/bin`，或用 `python3 -m beastxpy.main` 运行同样的代码——不安装也能直接从源码树运行。

## 第 3 步 —— 准备 JDK

BeastXPy 依次通过 `BEASTX_JAVA`、`JAVA_HOME`、`PATH` 查找 JVM。任选其一，并对每个要运行 BeastXPy 的 shell 都设置好：

```
export JAVA_HOME="/path/to/jdk-17"
export PATH="$JAVA_HOME/bin:$PATH"
```

验证：

```
java -version
```

任何 `-version` 能正常执行的 JDK 都可接受；容器镜像内置的是 17。

## 第 4 步 —— 决定 BEAST jar 的来源

默认使用 `beastxpy/tools/jars/` 内自带的两份 jar，因此 Gate 3 无需任何外部安装。若要改用自己的 BEAST：

```
export BEAST_1_10_4_HOME="/path/to/BEAST v1.10.4"
export BEAST_10_5_0_HOME="/path/to/BEAST X v10.5.0"
```

`BEASTX_JAR` 可显式指定单个 jar。查找顺序：显式环境变量（`BEASTX_JAR_<tag>`、`BEAST_<tag>_JAR`、`BEASTX_JAR`）→ 安装目录（`BEAST_<tag>_HOME`）→ `BEASTX_REF_DIR` 下的 `beast-mcmc-<version>/build/dist/beast.jar` → 检出目录旁边的官方安装 → 最后才是自带 jar。

**wheel 体积与归档。** 随包的两个 jar 是未经修改的官方 BEAST v1.10.4 与 BEAST X v10.5.0 release 制品（SHA-256 见 `NOTICE` 与 `tools/jars/MANIFEST.txt`），会让 wheel 与 sdist 增加约 22 MB。如果本机已装 BEAST，可用 `BEASTX_JAR`（或 `BEAST_<tag>_HOME`）指向它；自带副本虽然仍在包里，但不会再被使用。准备 Zenodo 或期刊存档时请把这份体积计入，并遵循仓库的单文件大小建议；不含 jar 的源码包要小得多。

**无 jar 变体。** 如果已经安装 BEAST，可以构建不含 jar 的 `beastxpy-core` wheel；Python 代码相同，只是缺少两个 BEAST jar：

```
python tools/build_core_wheel.py
python -m pip install dist-core/beastxpy_core-0.1.0-py3-none-any.whl
```

该安装下的 Gate 3 与 `run` 需要 `BEASTX_JAR`（或 `BEAST_<tag>_HOME`）；`beastxpy check-environment` 会报告缺失的 jar 及修复方法。LGPL-2.1 义务仅适用于随附的 BEAST 二进制，因此 core wheel 只适用 MIT。

## 第 5 步 —— 指向原生 BEAGLE（`run` 必需）

BEAST 的 JNI 库与版本绑定。把包含 `hmsbeagle64.dll`（Windows）或 `libhmsbeagle-jni.so` / `libhmsbeagle.so`（Linux）的目录指出来：

```
export BEASTX_BEAGLE_1_10_4_LIB="/path/to/beagle-3.1.x"
export BEASTX_BEAGLE_10_5_0_LIB="/path/to/beagle-4.0.x"
```

该目录必须包含原生库本身，而不是 BEAST 的 `lib/beagle`（那里只有 Java 封装）。`run` 会把探测到的目录自动暴露给 JVM（`-Djava.library.path` 加动态链接器路径），无需再做别的。细节与版本配对见 [原生 BEAGLE](native-beagle.md)。

## 第 6 步 —— 校验环境

```
beastxpy check-environment
```

正常机器上的预期输出：

```
python: 3.11.0
beastxpy: 0.1.0
java: /path/to/jdk-17/bin/java (version 17.0.2, source JAVA_HOME)
jar beast-1.10.4: /path/to/beast.jar sha256:0f6eb4fd...
jar beast-10.5.0: /path/to/beast.jar sha256:df11dea1...
beagle 1.10.4: /path/to/beagle-3.1.x/lib (source env:BEASTX_BEAGLE_1_10_4_LIB)
beagle 10.5.0: /path/to/beagle-4.0.x/lib (source env:BEASTX_BEAGLE_10_5_0_LIB)
```

* `source` 说明每一项是通过哪种机制解析到的（`JAVA_HOME`、`PATH`、`env:BEAST_1_10_4_HOME`、`reference-install`、`packaged-fallback`）。
* 出现 `missing:` 行且退出码为 2 表示缺少必需项——先修复再进入 [快速上手](quickstart.md)。
* 缺少 BEAGLE 时会打印 `beagle <version>: NOT FOUND`，并为每个版本打印一行 `guidance:`，指出需要设置的 `BEASTX_BEAGLE_<tag>_LIB` 变量。缺少 BEAGLE 不会让 `check-environment` 失败：`generate` 与 `check-data` 仍可用，Gate 3 回退到 `java-core`，而 `run`/`compare` 会拒绝启动。

继续阅读[快速上手](quickstart.md)。
