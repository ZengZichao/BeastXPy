# 故障排查

[English](../en/troubleshooting.md) | [中文](troubleshooting.md)

症状、原因、修法。条目按你碰到它们的先后排序：先是环境，再是配置，然后是运行。

## `no usable Java runtime found` / `NOT RUN`

Gate 3 或 runner 起不来 JVM。`beastxpy check-environment` 显示实际解析到什么、来自哪个来源。修法：把 `JAVA_HOME`（或 `BEASTX_JAVA`）指向一个 JDK，并把它的 `bin` 加入 `PATH`。

## `jar ... NOT FOUND`

找不到该版本的 BEAST jar。要么用自带 jar（什么都不用做），要么设置 `BEAST_1_10_4_HOME` / `BEAST_10_5_0_HOME` / `BEASTX_JAR`。`check-environment` 的 `source:` 字段说明最终是哪种机制生效。

## `native hmsbeagle library not found in java.library.path`

库存在但 JVM 加载不了。几乎总是**位数**不匹配：32 位 JVM 配 64 位 `hmsbeagle64.dll`。

```
java -XshowSettings:properties -version 2>&1 | grep os.arch
```

`amd64` 是 64 位，`x86` 是 32 位。把 `JAVA_HOME` 指向 64 位 JDK，再把 `BEASTX_BEAGLE_1_10_4_LIB` 或 `BEASTX_BEAGLE_10_5_0_LIB` 指向**包含库本身**的目录（不是 BEAST 的 `lib/beagle` 封装目录）。

## `Unable to load CPU plugin!` 随后 `error code -6 (No resource matches requirements)`

JNI 库加载成功，但 BEAGLE 的资源插件（`libhmsbeagle-cpu.so` 等）没有。插件按裸文件名 `dlopen`，跟随的是**链接器路径**而不是 `java.library.path`。通过 `beastxpy run` 运行时会替你设好；手动运行 BEAST 时，请自己导出 `LD_LIBRARY_PATH=<beagle 库目录>`（Linux）、`DYLD_LIBRARY_PATH`（macOS）或前置 `PATH`（Windows）。

## 运行几乎立刻停止，日志含 `No acceptable BEAGLE library plugins found`

JVM 完全没有可用的原生 BEAGLE。检查 `beastxpy check-environment`，设置 `BEASTX_BEAGLE_<version>_LIB` 后重跑。失败的种子会留下 `run.log`、`run_meta.json`（status `failed`）和部分日志；修好环境后用 `--force` 重跑（或 `--resume --rerun-successful`）。

## `run: --java-only cannot run this analysis`

`--java-only` 对应 BEAST 的 `-java` 开关，而 BEAST 自带的纯 Java BEAGLE 没有实现 `getSiteLogLikelihoods`——所以任何含 `<treeLikelihood>` 的文档（即 BeastXPy 产出的所有文档）在它下面都会中止。安装原生 BEAGLE 并去掉该参数。

## `Gate 0: unknown key`

配置解析刻意严格。消息会指出键名与所在区段；改正拼写或删掉该键。带引号的布尔值（`auto_optimize: "true"`）同样被拒绝。

## `calibrations[...].distribution.parameters.mean: must be > 0 for lognormal`

lognormal 的 `mean` 在对数尺度上，但仍必须为正。根高约 30 个时间单位用 `mean: 3.4`（e^3.4 ≈ 30），或用 `meanInRealSpace: true` 给真实尺度均值。

## 校准导致 `Gate 2 failed`

标定之间或与树先验矛盾。运行 `beastxpy diagnose --config config.yaml --output-dir diag` 查看细节，包括先验密度图与重叠热图。

## `Gate 3/3: ... FAILED` 且 BEAST 报解析错误

BEAST 本身拒绝了文档。消息会给出 XML 路径和解析器报错；这是 BeastXPy 的缺陷或自定义片段的问题——请附带配置上报。调试期间确需产出文档时，`--allow-unvalidated` 会以 `UNVALIDATED` 标签写出（自定义片段不适用，必须通过 Gate 3）。

## Windows 上 Nextflow 失败

Nextflow 的 SIGHUP 进程注册在 Windows 上无法原生工作。请用 WSL 或 Linux。Snakemake 可原生运行。

## `assess` 报告 `not_computable`

它在说实话。PSRF 与 ASDSF 至少需要两条链；多跑几个种子。某个统计量缺失意味着日志里没有这一列，而不是数值为零。

## 报告显示 "not assessed"

要么没跑 `assess`，要么它的 JSON 不在报告查找的位置。运行 `beastxpy assess <workdir> --json <workdir>/assessment.json`，或传 `--assessment PATH`。

## `reproduce --check` 失败

某个字段与锁不一致，输出会指出是哪个。如果环境确实变了，重新生成锁；不要去编辑它——被编辑过的锁直接以退出码 3 拒绝。
