# 流水线

[English](../en/pipelines.md) | [中文](pipelines.md)

`beastxpy pipeline` 会写出一个 Snakemake 或 Nextflow 文件，用于跑种子矩阵与后续处理步骤。

## 第 1 步 —— 生成流水线

```
beastxpy pipeline --config examples/config_basic.yaml --type snakemake --output-dir pipeline
```

预期输出：

```
wrote pipeline/Snakefile
wrote pipeline/config_basic.yaml
```

命令生成文本时会自行做语法检查，且会把配置一并拷入，使该目录自包含。`--type nextflow` 则写 `pipeline/main.nf`。

## 第 2 步 —— 运行

Snakemake 在 Linux、macOS 与 Windows 上都可原生运行：

```
cd pipeline
snakemake -s Snakefile --cores 4
```

Nextflow 在 Windows 上**不**被原生支持：它的 SIGHUP 进程注册在那里会失败。请在 WSL 或 Linux 下运行生成的流水线。

两条流水线都假设 `beastxpy` 在 `PATH` 中，并继承[原生 BEAGLE](native-beagle.md) 与[安装](installation.md)里描述的环境变量。流水线只是把你能手敲的命令包了一层——若你需要检查中间状态，或只跑一个分析，直接用 [快速上手](quickstart.md)里的命令更容易推理。
