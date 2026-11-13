# 离线 BEAGLE 源码包

[English](README.md) | [中文](README.zh-CN.md)

`deploy/Dockerfile` 与 `deploy/apptainer.def` 从源码构建原生 BEAGLE。

默认情况下定义文件会下载官方 release 归档。
如果本目录中含有一个名字恰好为 `beagle-lib.tar.gz` 的文件，
构建就改用那个文件且不执行任何下载，
这正是完全隔离的机器上、或在 GitHub 不可达的网络里想要的做法。

```
# optional: enable an offline build
cp /path/to/beagle-lib-4.0.1.tar.gz deploy/beagle/beagle-lib.tar.gz
docker build -f deploy/Dockerfile -t beastxpy:0.1.0 .
rm deploy/beagle/beagle-lib.tar.gz   # do not commit the tarball
```

该 tar 包必须解压出单个顶层目录（即上游的
`beagle-lib-<version>/` 布局）。它绝不提交：`.gitignore` 会排除它，
而且把第三方源码归档放进本仓库一起分发会让许可立场变得含混。

版本配对很重要：BEAST X 10.5.0 使用 BEAGLE 4.0.x，而 BEAST 1.10.4
使用较旧的 3.1.0 ABI。默认构建产出 4.0.1，
也就是 `BEASTX_BEAGLE_10_5_0_LIB` 指向的那个。
