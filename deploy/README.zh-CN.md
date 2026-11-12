# BeastXPy 容器

[English](README.md) | [中文](README.zh-CN.md)

两份定义，一个镜像：

| 文件 | 构建工具 |
|---|---|
| `Dockerfile` | Docker / Podman |
| `apptainer.def` | Apptainer / Singularity |

`beastxpy container --print docker` 与 `--print apptainer` 会原样打印这两个文件，
所以你在本文读到的就是你实际构建的。

## 镜像内容

- Python 3.10（Ubuntu 22.04，来自 Temurin 基础镜像）
- JDK 17（`eclipse-temurin:17-jdk-jammy`）
- BEAST 1.10.4 与 BEAST X 10.5.0 —— 仓库内提交在 `beastxpy/tools/jars/`
  下的 jar 作为包数据安装，
  因此 Gate 3 在运行时无需网络访问
- 原生 BEAGLE 4.0.1，在构建期间从源码编译，安装到
  `/usr/local/lib` 并作为 `BEASTX_BEAGLE_10_5_0_LIB` 导出
- BeastXPy 0.1.0，入口点 `beastxpy`
- 一个非特权用户 `beastxpy`（uid/gid 10001）；`/work`
  是它的可写工作目录

## 构建

在仓库根目录执行（构建上下文必须包含 `beastxpy/`）：

```
docker build -f deploy/Dockerfile -t beastxpy:0.1.0 .
```

构建会编译 BEAGLE，所以它需要 C/C++ 工具链；镜像在一个早期层里就
安装了该工具链。基础镜像按 digest 固定
（`eclipse-temurin:17-jdk-jammy@sha256:73ed4422...`），BEAGLE 源码
归档会对照其 SHA-256 校验；digest 为空或不正确时构建会中止，
而不是给出警告。

### 无网络访问时构建

默认构建会下载 BEAGLE 源码归档。要完全避免这一点，
先把该归档放进 `deploy/beagle/`：

```
cp /path/to/beagle-lib-4.0.1.tar.gz deploy/beagle/beagle-lib.tar.gz
docker build -f deploy/Dockerfile -t beastxpy:0.1.0 .
rm deploy/beagle/beagle-lib.tar.gz    # never commit the tarball
```

该 tar 包必须是官方 v4.0.1 源码归档，并且必须匹配固定的 SHA-256
`9d258cd9bedd86d7c28b91587acd1132f4e01d4f095c657ad4dc93bd83d4f120`；
Dockerfile 与 `apptainer.def` 会对下载得到的和本地提供的两种归档做校验。
它必须解压出单个顶层目录，
也就是上游的布局。

### 镜像源

若 `registry-1.docker.io` 不可达，就从镜像源拉取基础镜像并重新打标，
让定义文件仍可原样使用：

```
docker pull docker.m.daocloud.io/eclipse-temurin:17-jdk-jammy
docker tag  docker.m.daocloud.io/eclipse-temurin:17-jdk-jammy eclipse-temurin:17-jdk-jammy
```

随后运行正常的 `docker build` 命令。定义文件保留规范镜像名；
镜像源是机器的属性，不是交付物的属性。

## 运行

```
docker run --rm beastxpy:0.1.0 --help
docker run --rm beastxpy:0.1.0 --version

# work on files in the current directory
docker run --rm -v "$PWD:/work" beastxpy:0.1.0 \
    generate --config /work/config.yaml --output /work/out.xml --beast-validate

docker run --rm -v "$PWD:/work" beastxpy:0.1.0 \
    check-data --config /work/config.yaml
```

Apptainer：

```
apptainer build beastxpy_0.1.0.sif deploy/apptainer.def
apptainer run beastxpy_0.1.0.sif --help
apptainer exec --bind "$PWD:/work" beastxpy_0.1.0.sif \
    beastxpy generate --config /work/config.yaml --help
```

## BEAGLE 版本配对

BEAST 的 JNI ABI 随版本而变：

| BEAST | BEAGLE | 此处打包 |
|---|---|---|
| BEAST X 10.5.0 | 4.0.x | 是，4.0.1 |
| BEAST 1.10.4 | 3.1.0 | **否** —— 若你需要在 1.10.4 上使用原生似然，请自行提供 |

镜像始终设置 `BEASTX_BEAGLE_10_5_0_LIB=/usr/local/lib`。要进行原生
1.10.4 运行，请绑定挂载一个包含 3.1.0 `libhmsbeagle*.so` 的目录，并把
`BEASTX_BEAGLE_1_10_4_LIB` 指向它。Gate 3 校验完全不需要 BEAGLE；
只有真实的 MCMC 似然才需要。

`compare` 需要原生 BEAGLE，缺少时失败关闭（退出码 2，不给出任何数字）。
这是有意为之。

## 许可

BeastXPy 采用 MIT AND LGPL-2.1-only。BEAST 是 LGPL-2.1-only；jar 带有
自己的 `META-INF/LICENSE.txt` 与 `META-INF/NOTICE.txt`，它们在打包后的 jar 内
被保留下来。BEAGLE 是 LGPL-3.0。本仓库的 `LICENSE` 与 `NOTICE` 适用于
BeastXPy 自己的代码。若你重新分发该镜像，请保留这些声明，
并按这些许可证的要求提供相应源码。

## 验证状态

**已于 2026-10-21 构建并运行**，环境是 Windows 上的 Docker Desktop 29.8.0
（linux/amd64），并使用本地的 BEAGLE 4.0.1 源码归档，
因此构建没有执行任何下载。

```
docker build -f deploy/Dockerfile -t beastxpy:0.1.0 .        -> exit 0
docker run --rm beastxpy:0.1.0 --version                     -> beastxpy 0.1.0
docker run --rm --entrypoint id beastxpy:0.1.0               -> uid=10001(beastxpy)
docker run --rm beastxpy:0.1.0 check-environment             -> JDK 17.0.20.1,
                                                                both jars resolved
docker run --rm beastxpy:0.1.0 capabilities                  -> 35 rows, exit 0
docker run --rm -v ...:/data:ro beastxpy:0.1.0 generate \
    --config /data/config_subst_jc69.yaml --beast-validate    -> Gate 3 OK, exit 0
```

原生 BEAGLE 已在镜像内确认存在：

```
/usr/local/lib/libhmsbeagle-cpu.so.40.0.0
lookup_beagle_details("10.5.0") -> {'path': '/usr/local/lib',
                                    'source': 'env:BEASTX_BEAGLE_10_5_0_LIB'}
```

**未验证：** Apptainer 构建（构建机上没有安装
Apptainer），以及镜像内的原生 1.10.4 MCMC（未打包 3.1.0 库）。
两者都不声称可用。
