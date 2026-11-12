# 参与 BeastXPy 的开发

[English](CONTRIBUTING.md) | [中文](CONTRIBUTING.zh-CN.md)

感谢你改进 BeastXPy。本项目是一个科学工具：任何让 XML、某个统计量或某项有记录的主张变得不那么可信的改动，都是回归，即使测试通过也一样。

## 环境搭建

```
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m pip install -r requirements-test.txt
```

运行 Gate 3 与完整测试套件需要 JDK。运行或比较链需要原生 BEAGLE（生成 XML 不需要）。

## 测试

```
python -m pytest -q                 # full suite, needs a JDK
python -m pytest -q -m "not java"   # Java-free subset
```

找不到 JDK 时，Gate-3 测试会失败而非跳过；这是有意为之（一项校验主张绝不能悄无声息地消失）。`java` 标记是本地工作的逃生通道，会被 `-m "not java"` 排除。

## 质量关卡

CI 质量作业运行以下命令：

```
python -m flake8 beastxpy tools
python -m black --check --line-length 100 beastxpy tests tools
python -m mypy beastxpy
```

CI 覆盖率作业运行完整套件（包括带 Java 标记的测试），命令为：

```
python -m pytest -q --cov=beastxpy --cov-fail-under=70
```

要在本地不依赖 Java 运行，请使用 `python -m pytest -q -m "not java"`。

在发起 pull request 之前，用 `python -m black --line-length 100 beastxpy tests tools` 格式化。不要添加笼统的 `# type: ignore` 或 `# noqa` 注释；要么修正类型，要么写明该抑制为何不可避免。

## 科学不变量

- 绝不编造统计量。当某个值无法计算时，报告 `not_computable`/`None` 并给出原因。特别地，不要把一个不可计算的诊断变成零，或变成一个猜测的数字。
- 保持校验证据的分层。`java-core` 只做构建，并使用项目自带的 `TreeLikelihood` shim；只有 `beagle` 才是完整解析器、真实似然的校验。若证据发生变化，要一并更新 `capabilities.yaml` 的原因说明与 README。
- 为每一个新的数值估计器添加参考值测试或解析测试。"能跑起来"不是数值测试。
- `tests/golden/` 下的金标准 XML 文件是回归产物。只能通过有记录的刷新机制来刷新它们，并说明 XML 为何改变。

## 随包的 BEAST jar

`beastxpy/tools/jars/` 必须包含未经修改的官方发布产物。SHA-256 值记录在 `NOTICE`、`beastxpy/tools/jars/MANIFEST.txt` 与 `beastxpy/data/xml_spec.json` 中；`beastxpy/tools/build_jars.py` 在写入清单时会核验这些值。绝不要把本地重新构建的 jar 作为发布产物提交，也绝不要在构建清单中记录绝对路径、用户名或主机名。

## 文档

英文页与中文页保持一一对应，且英文版是权威版本：两者不一致时以英文为准，修正中文页。所有面向读者的文档都有中英两份——`docs/en/` 与 `docs/zh/`、`README.md` 与 `README.zh-CN.md`，其余辅助文档则以 `<name>.zh-CN.md` 作为其英文文件的同目录兄弟文件（`CONTRACT.md`、`CONTRIBUTING.md`、`deploy/README.md`、`deploy/beagle/README.md`、`examples/empirical/README.md`、`tests/golden/beauti_reference/README.md`）。语言切换链接把英文列在前面。改动默认值、flag、阈值或任何校验主张时，两种语言都要更新，命令参考也要同步；`tests/test_docs.py` 会强制检查配对关系、切换链接、代码块与表格内联 token 的一致性，以及 `docs/<lang>/cli.md` 与参数解析器在每一个已注册 flag、取值集合、必选项和 synopsis 上都保持一致。参考文献的 DOI 在发布前应对照出版社核验。

## 发布

版本号用 `tools/bump_version.py` 切版，并通过推送一个 `vX.Y.Z` 标签来发布；发布工作流会构建、检查并发布该包与发布记录。完整流程（包括一次性的 PyPI 与 Zenodo 配置）见 [docs/zh/release.md](docs/zh/release.md)。

## 仓库 URL 与存档 DOI

`CITATION.cff` 不声明仓库 URL；待仓库公开且首个发布被存档后，再加上真实的 URL 与 Zenodo DOI（同时放宽 `tests/test_release_contract.py` 中相应的守护检查）。这是一项发布任务，不是贡献者能从代码中推断出来的东西。
