# 容器

[English](../en/container.md) | [中文](container.md)

源码里附带两份定义：`deploy/Dockerfile` 与 `deploy/apptainer.def`。无需打开仓库即可打印：

```
beastxpy container --print docker
beastxpy container --print apptainer
```

该命令原样打印文件，不构建任何东西。定义文件位于**源码检出**中 `beastxpy` 包的旁边；普通 wheel 安装不带 `deploy/` 目录，此时 `container --print` 以退出码 2 结束并提示改用源码树（或从源码树复制这两个文件）。

## 镜像内容

Python 3.10、JDK 17、来自 `beastxpy/tools/jars/` 的两个 BEAST jar、从源码编译的原生 BEAGLE 4.0.1，以及 BeastXPy 0.1.0。以非特权用户 `beastxpy`（uid 10001）运行，入口为 `beastxpy`。基础镜像按 digest 固定；无论在线下载还是离线提供的 BEAGLE 源码包都必须匹配固定的 SHA-256，digest 为空或不匹配时构建失败。

## 构建与运行

```
docker build -f deploy/Dockerfile -t beastxpy:0.1.0 .
docker run --rm beastxpy:0.1.0 --version
docker run --rm -v "$PWD:/work" beastxpy:0.1.0     generate --config /work/config.yaml --beast-validate
```

离线构建时，先把 `beagle-lib.tar.gz` 放进 `deploy/beagle/`。镜像源、BEAGLE 版本配对与许可说明见 `deploy/README.md`。

## 已验证内容

Docker 镜像已实际构建并运行：`beastxpy --version`、非 root 的 `uid=10001`、`check-environment` 解析出 JDK 17 与两个 jar、`capabilities`，以及一次 Gate 3 通过的 `generate --beast-validate`。原生 BEAGLE 确认位于 `/usr/local/lib`。

**未验证：** Apptainer 构建（构建机上未安装 Apptainer），以及镜像内的原生 BEAST 1.10.4 MCMC（未打包 BEAGLE 3.1.0，只带 BEAST X 10.5.0 所需的 4.0.1）。
