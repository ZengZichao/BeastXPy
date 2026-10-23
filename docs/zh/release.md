# 发布

[English](../en/release.md) | [中文](release.md)

本文说明如何切版并发布。整条发布流水线都跑在 GitHub Actions 上：触发发布的机器只需要 `git` 和推送权限，完全不需要 JDK、BEAST 或 BEAGLE。因此你可以在任何一台电脑上切版发布，包括只存放了一份仓库下载副本的机器。

## 一次性配置

1. **GitHub 仓库。** 把本仓库推送到 GitHub，并确认该仓库启用了 Actions。
2. **PyPI 可信发布。** 发布到 PyPI 不需要保存任何令牌。在 pypi.org 打开 *Account → Publishing*，添加一个待发布者（pending publisher），各项取值必须与下面完全一致：
- PyPI 项目名：`beastxpy`
- Owner：你的 GitHub 用户名或组织名
- Repository：仓库名
- Workflow filename：`release.yml`
- Environment name：`pypi`
3. **Zenodo。** 用 GitHub 账号登录 zenodo.org，授权 Zenodo–GitHub 集成，并打开该仓库的开关。此后每个 GitHub release 都会被自动归档，获得一个版本 DOI 以及覆盖所有版本的概念 DOI。Zenodo 会读取发布记录与 `CITATION.cff`。

## 切版发布

一条命令即可更新所有声明版本号的位置，测试套件会验证没有遗漏：

```
python tools/bump_version.py 0.2.0
python -m pytest -q
git add pyproject.toml beastxpy/__init__.py CITATION.cff README.md README.zh-CN.md docs tools
git commit -m "Release 0.2.0"
git tag v0.2.0
git push origin main --tags
```

推送标签会启动发布流水线（`.github/workflows/release.yml`），它按顺序执行：

1. 校验标签与 `pyproject.toml`、`beastxpy/__init__.py`、`CITATION.cff` 中声明的版本一致（`tools/check_release_tag.py`）；
2. 构建 sdist 与 wheel，并用 `twine check --strict` 检查；
3. 构建免 jar 的 `beastxpy-core` wheel；
4. 安装该 wheel，运行 `beastxpy --version` 与 `beastxpy capabilities` 作为冒烟检查；
5. 通过可信发布（无需令牌）把包发布到 PyPI；
6. 创建 GitHub release，附上 sdist、wheel 与 core wheel——这就是 Zenodo 归档的记录。

## 验证发布结果

```
python -m pip install beastxpy==0.2.0
beastxpy --version
beastxpy check-environment
```

* 几分钟内 PyPI 项目页会出现新版本。
* GitHub 的 Releases 页面会列出该标签与三个发行文件。
* release 创建几分钟后，Zenodo 会保留版本 DOI；DOI 显示在你的 Zenodo 控制台与 release 页面上。

## 归档之后

Zenodo 发出首个 DOI 后，把它写进 `CITATION.cff`（添加 `doi:` 字段并更新其中的说明文字），并同步放宽 `tests/test_release_contract.py` 里的相应守卫，让测试断言真实 DOI 而不是禁止 DOI。提交该修改，它会随下一次发布一起生效。

## 版本编号

版本遵循语义化版本：`MAJOR.MINOR.PATCH`。新增模型或命令时提升 minor，修复问题时提升 patch。不要手工编辑版本字符串；`tools/bump_version.py` 的存在就是为了让 `pyproject.toml`、包本身、`CITATION.cff`、README 与文档保持一致，一旦不一致，`tests/test_release_contract.py` 会失败。
