# 原生 BEAGLE

[English](../en/native-beagle.md) | [中文](native-beagle.md)

真实 MCMC 似然需要原生 hmsbeagle JNI 库。缺少时 BEAST 会退回纯 Java 实现，而它从未实现位点对数似然入口，因此运行会在第一次评估时就停下，而不是产出 "看起来是真的"链。

## 第 1 步 —— 获取正确的版本

| BEAST | 原生 BEAGLE |
|---|---|
| 1.10.4 | 3.1.x |
| 10.5.0 | 4.0.x |

（BEAGLE 本身：Ayres 等 2012——见 [references](references.md)。）

两者 ABI 互不兼容：3.1.x 不能服务 BEAST X 10.5.0，反之亦然。安装或编译你的 BEAST 所需的版本，然后找到**包含库文件本身**的目录：Windows 上的 `hmsbeagle64.dll`，Linux 上的 `libhmsbeagle-jni.so` / `libhmsbeagle.so`。BEAST 自带的 `lib/beagle` 目录只有 Java 封装，永远不算数。

## 第 2 步 —— 告诉 BeastXPy 位置

```
export BEASTX_BEAGLE_1_10_4_LIB="/opt/beagle/3.1.0"
export BEASTX_BEAGLE_10_5_0_LIB="/opt/beagle/4.0.1"
```

两个变量相互独立、均可省略：用哪个版本就设哪个。目录发现顺序：显式参数（`compare` 的 `--beagle-dir`；`lock`、`reproduce` 的 `--beagle-lib`）→ `BEASTX_BEAGLE_<tag>_LIB` → `BEASTX_BEAGLE_LIB` → `BEAGLE_HOME` → `BEAGLE_LIB`（每个还会追加 `/lib`、`/bin` 再试）→ 所选 jar 旁的 `beagle` 目录。

## 第 3 步 —— 验证可发现

```
beastxpy check-environment
```

`check-environment` 报告 Java 与两个 jar。要确认 BEAST 本身能用这个库，做一次 Gate 3 验证的生成——设置了变量后，Gate 3 会以 `beagle` 模式（最强验证）运行：

```
beastxpy generate --config examples/config_subst_jc69.yaml \
    --output out.xml --beast-validate
```

每个版本的核心选择是自动的：能发现原生库就用 `beagle`，否则用 `java-core`（附说明）。库存在但加载失败会被报告并改用较弱核心——不可用的 BEAGLE 是环境故障，不是 XML 缺陷。

## `run` 如何使用它

`beastxpy run` 会像 Gate 3 一样把探测到的目录暴露给 BEAST 进程：`-Djava.library.path=<目录>` **加上**动态链接器路径（Linux 的 `LD_LIBRARY_PATH`、macOS 的 `DYLD_LIBRARY_PATH`、Windows 的 `PATH`）。后一半很关键：BEAGLE 通过 `dlopen`/`LoadLibrary` 按**裸库文件名**加载资源插件（`libhmsbeagle-cpu.so` 等），走的是链接器路径而不是 `java.library.path`。只设 `-Djava.library.path` 会得到：

```
Unable to load CPU plugin!
...
BEAGLE function, create, returned error code -6 (No resource matches requirements)
```

BeastXPy 会替你把两者都设好；如果手动运行 BEAST，请自己导出 `LD_LIBRARY_PATH`。

## 位数陷阱

**JVM 与库的位数必须一致。** 在 Windows 上很容易出现 32 位 JDK（`os.arch=x86`）配 64 位 `hmsbeagle64.dll` 的情况。症状是：

```
native hmsbeagle library not found in java.library.path
```

即使文件就在那儿。先检查再怀疑路径：

```
java -XshowSettings:properties -version 2>&1 | grep 'os.arch'
```

`os.arch = amd64` 是 64 位；`x86` 是 32 位。

## Windows 上的线程数

Windows 上请传 `--beagle-threads 1`。多线程 BEAGLE 在那里不够稳定，单线程对校验和短链已经足够快。

## 哪些命令需要它

| 命令 | 需要原生 BEAGLE |
|---|---|
| `validate --beast` | 否（回退到 java-core 验证机制） |
| `generate --beast-validate` | 否（同上） |
| `run` | 是——BeastXPy 的文档全部含 `<treeLikelihood>` |
| `compare` | 是，缺失时直接失败关闭 |
| `assess`、`report`、`lock`、`check-data` | 否 |
