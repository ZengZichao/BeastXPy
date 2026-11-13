"""Self-contained HTML run report for a ``beastxpy run`` workdir (F3).

The report is a **single HTML file**: all CSS is inline, every image is a
base64 ``data:`` URI and nothing is fetched from the network, so it renders
identically from ``file://`` on a machine with no internet access.

Honesty rules this module is built around
-----------------------------------------

* Every piece of user-supplied or data-derived text is passed through
  :func:`html.escape` with ``quote=True`` before it reaches the document.
  Nothing is inserted as markup, and no link is ever generated from data.
* Numbers are only rendered when they were actually computed or actually
  recorded in the workdir.  A missing value renders as the localized
  "not available" label, never as ``0`` and never as a guess.
* When no ``beastxpy.assessment/v1`` document exists for the workdir the
  assess section says **"not assessed" / "未评估"** explicitly and lists the
  paths that were searched.  The report never back-fills ESS, PSRF or ASDSF.
* A gate that did not run is shown as ``not-run`` together with the reason;
  a gate whose pass is *inferred* from artefacts (rather than recorded) is
  labelled as inferred and states the evidence.

Both languages are complete: :data:`LABELS_EN` and :data:`LABELS_ZH` carry the
same key set, and :data:`LIMITATIONS_EN` / :data:`LIMITATIONS_ZH` are
parallel lists.  Verbatim tool output (``run_meta.json`` keys, BEAST log
lines, ``tracestats`` warnings, assessment messages) is quoted unmodified and
always captioned as raw output, because translating another program's text
would misrepresent it.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import math
import re
import tempfile
import warnings as _warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import _contracts as C
from .tracestats import (
    NON_PARAMETER_COLUMNS,
    TraceError,
    compute_trace_stats,
    format_stats_table,
    parse_trace_log,
    write_trace_plot,
)

LANG_EN = "en"
LANG_ZH = "zh"
LANGUAGES: Sequence[str] = (LANG_EN, LANG_ZH)

#: Assessment documents are looked for here, in this order.
ASSESSMENT_CANDIDATES: Sequence[str] = (
    "assessment.json",
    "assess.json",
    "beastxpy.assessment.json",
)

#: Cap the manifest so a huge workdir cannot produce an unusable document.
MANIFEST_FILE_LIMIT = 2000
#: Cap on embedded configuration text (bytes).
CONFIG_TEXT_LIMIT = 200_000
#: Cap on how many trace-log bytes are parsed for the summary.
TRACE_ROW_LIMIT = 200_000
#: Cap on the number of parameters rendered per chain.
PARAMETER_ROW_LIMIT = 200

_STATUS_OK = "ok"
_STATUS_SKIPPED = "skipped"

# ---------------------------------------------------------------------------
# Labels -- complete, parallel dictionaries (no partial translations)
# ---------------------------------------------------------------------------

LABELS_EN: Dict[str, str] = {
    "title": "BeastXPy run report",
    "subtitle": "Reproducible BEAST 1 divergence-time estimation",
    "generated_at": "Generated at (UTC)",
    "language": "Report language",
    "tool_version": "BeastXPy version",
    "report_schema": "Report schema",
    "self_contained": "Self-contained file: inline CSS, base64 images, no network access.",
    "nav": "Contents",
    "sec_conclusion": "Conclusion",
    "sec_provenance": "Provenance and fingerprint",
    "sec_gates": "Validation gates (Gate 0-3)",
    "sec_auto_priors": "Automatic priors",
    "sec_diagnostics": "Diagnostics",
    "sec_run_metadata": "Run metadata",
    "sec_trace": "Trace summary",
    "sec_assess": "Assessment",
    "sec_manifest": "File manifest",
    "sec_config": "Configuration used",
    "sec_limitations": "Limitations",
    "col_field": "Field",
    "col_value": "Value",
    "col_gate": "Gate",
    "col_status": "Status",
    "col_basis": "Basis / reason",
    "col_seed": "Seed",
    "col_parameter": "Parameter",
    "col_mean": "Mean",
    "col_median": "Median",
    "col_ess": "ESS",
    "col_act": "ACT",
    "col_hpd": "95% HPD",
    "col_samples": "Samples",
    "col_path": "Path",
    "col_kind": "Kind",
    "col_bytes": "Bytes",
    "col_sha256": "SHA-256",
    "col_severity": "Severity",
    "col_message": "Message",
    "col_fix": "Next step",
    "col_distribution": "Distribution",
    "col_source": "Provenance",
    "auto_priors_caption": (
        "These priors were filled in automatically because the configuration did "
        "not specify them. Each is marked with a 'BXP auto prior' comment in the "
        "generated XML; override any of them under parameter_priors."
    ),
    "auto_priors_none": "No generated XML with automatic-prior annotations was found.",
    "col_exit_code": "Exit code",
    "col_elapsed": "Elapsed (s)",
    "conclusion_run_completed": "The recorded BEAST run(s) completed.",
    "conclusion_run_partial": "Some recorded BEAST runs failed.",
    "conclusion_run_failed": "Every recorded BEAST run failed.",
    "conclusion_no_chains": "No BEAST run directory was found in this workdir.",
    "conclusion_chain_ok": "Seed {seed}: BEAST finished with exit code {code}.",
    "conclusion_chain_failed": "Seed {seed}: BEAST reported status {status} (exit code {code}).",
    "conclusion_chain_timeout": "Seed {seed}: the run timed out after {seconds} s.",
    "conclusion_chain_skipped": "Seed {seed}: skipped by --resume (previous run reused).",
    "conclusion_fatal": "Seed {seed}: BEAST fatal error: {line}",
    "conclusion_assessed": "Convergence assessment: {verdict} (source: {source}).",
    "conclusion_not_assessed": (
        "Convergence assessment: not assessed -- no assessment JSON was found " "for this workdir."
    ),
    "conclusion_gate3_not_run": "Gate 3 (official BEAST validation) was not recorded for this workdir.",
    "assess_not_assessed": "not assessed",
    "assess_not_assessed_reason": (
        "No beastxpy.assessment/v1 document was found for this workdir, so no "
        "ESS, PSRF, ASDSF or verdict is reported here. Nothing is estimated or "
        "back-filled."
    ),
    "assess_searched": "Locations searched",
    "assess_howto": "Run `beastxpy assess <workdir> --json <workdir>/assessment.json` first.",
    "assess_source": "Assessment document",
    "assess_schema": "Assessment schema",
    "assess_verdict": "Verdict",
    "assess_strict": "Strict mode",
    "assess_thresholds": "Thresholds",
    "assess_chains": "Chains in the assessment",
    "assess_multichain": "Multi-chain diagnostics",
    "assess_psrf": "PSRF (Gelman-Rubin)",
    "assess_asdsf": "ASDSF (split frequencies)",
    "assess_reasons": "Assessment reasons",
    "assess_limitations": "Assessment limitations (verbatim)",
    "assess_parameters": "Parameters",
    "assess_operators": "Operators",
    "assess_burnin": "Burn-in",
    "assess_status": "Chain status",
    "assess_incomplete": "Incomplete reason",
    "assess_raw_note": "Values below are quoted from the assessment document.",
    "not_available": "not available",
    "not_reported": "not reported",
    "not_computable": "not computable",
    "not_run": "not-run",
    "passed": "passed",
    "failed": "failed",
    "passed_inferred": "passed (inferred)",
    "unknown": "unknown",
    "yes": "yes",
    "no": "no",
    "none": "none",
    "verdict_pass": "pass",
    "verdict_warn": "warn",
    "verdict_fail": "fail",
    "status_ok": "completed",
    "status_failed": "failed",
    "status_timeout": "timeout",
    "status_skipped": "skipped (resumed)",
    "gate_0_name": "Gate 0 - strict configuration parsing",
    "gate_1_name": "Gate 1 - pure-Python XML validation",
    "gate_2_name": "Gate 2 - calibration diagnostics",
    "gate_3_name": "Gate 3 - official BEAST jar validation",
    "gate_basis_xml": (
        "Inferred: `beastxpy run` writes the generated XML only after Gate 0 " "parsing succeeds."
    ),
    "gate_basis_gate1": (
        "Inferred: the runner aborts before writing any XML when Gate 1 reports "
        "an error on the assembled document."
    ),
    "gate_basis_gate2": (
        "Inferred: the runner aborts before writing any XML when Gate 2 reports "
        "a calibration conflict."
    ),
    "gate_basis_no_xml": "No generated XML document was found in the workdir.",
    "gate_basis_gate3_absent": (
        "No Gate-3 record in the workdir (no *.fingerprint.json with a "
        "`validation` block); `beastxpy run` executes BEAST directly instead of "
        "running the parser-validation gate."
    ),
    "gate_basis_gate3_sidecar": "Read from the fingerprint side-car written next to the XML.",
    "gate_basis_run_evidence": (
        "Note: BEAST itself ran in this workdir (see run metadata); that is "
        "execution evidence, not a recorded Gate-3 validation result."
    ),
    "prov_fingerprint": "Analysis fingerprint",
    "prov_content_fingerprint": "Content fingerprint",
    "prov_data_digest": "Alignment data digest",
    "prov_beast_version": "BEAST version",
    "prov_tool_version": "BeastXPy version",
    "prov_analysis_name": "Analysis name",
    "prov_java_path": "Java executable",
    "prov_jar_path": "BEAST jar",
    "prov_jar_sha256": "BEAST jar SHA-256",
    "prov_seeds": "Seeds",
    "prov_generated_xml": "Generated XML",
    "prov_wall_clock": "Wall clock",
    "prov_source": "Provenance source",
    "prov_none": "No run_meta.json or fingerprint comment was found in the workdir.",
    "diag_ess_threshold": "ESS threshold",
    "diag_columns": "Parameters analysed",
    "diag_burnin": "Burn-in (samples)",
    "diag_chains_in_log": "Concatenated chains in the log",
    "diag_below_threshold": "Parameters below the ESS threshold",
    "diag_nonfinite": "Non-finite samples dropped",
    "diag_min_ess": "Lowest ESS",
    "diag_no_trace": "No parsable trace log for this seed.",
    "diag_trace_error": "The trace log could not be analysed: {message}",
    "diag_warnings_raw": "Raw tracestats warnings (verbatim tool output, not translated)",
    "diag_plot": "Trace plot",
    "diag_plot_missing": "No trace plot: {reason}",
    "trace_caption": (
        "Statistics computed by beastxpy.tracestats from the trace log; a value "
        "that could not be estimated is shown as 'not available'."
    ),
    "trace_none": "No trace statistics are available for this workdir.",
    "meta_caption": "Recorded by beastxpy.runner in run_meta.json.",
    "meta_raw": "Raw run_meta.json (verbatim)",
    "meta_no_chains": "No run_meta.json was found in the workdir.",
    "meta_command": "BEAST command",
    "meta_trace_log": "Trace log",
    "meta_tree_log": "Tree log",
    "meta_run_log": "Run log",
    "meta_checkpoints": "Checkpoint files",
    "meta_archived": "Archived logs",
    "meta_timed_out": "Timed out",
    "manifest_caption": "Every file in the workdir at report time, with its SHA-256.",
    "manifest_total": "Total",
    "manifest_files": "files",
    "manifest_truncated": "The manifest lists the first {limit} files; {extra} more were not listed.",
    "manifest_excluded": "Excluded from the manifest: this report and its --json output.",
    "config_caption": "Embedded verbatim from the workdir; HTML-escaped, never executed.",
    "config_source": "Source",
    "config_kind_yaml": "YAML configuration",
    "config_kind_xml": "Generated BEAST XML",
    "config_truncated": "Truncated to the first {limit} bytes of {total}.",
    "config_absent": (
        "No configuration file was found in the workdir. `beastxpy run` does not "
        "copy the input YAML into the workdir, so nothing is embedded and nothing "
        "is reconstructed."
    ),
    "raw_note": "Raw program output, quoted verbatim and not translated.",
    "footer": "Generated by BeastXPy {version} (`beastxpy report`).",
}

LABELS_ZH: Dict[str, str] = {
    "title": "BeastXPy 运行报告",
    "subtitle": "可复现的 BEAST 1 分化时间估计",
    "generated_at": "生成时间（协调世界时）",
    "language": "报告语言",
    "tool_version": "BeastXPy 版本",
    "report_schema": "报告模式版本",
    "self_contained": "单文件自包含：内联样式、base64 图片，不访问网络。",
    "nav": "目录",
    "sec_conclusion": "结论",
    "sec_provenance": "溯源与指纹",
    "sec_gates": "验证闸门（Gate 0-3）",
    "sec_auto_priors": "自动先验",
    "sec_diagnostics": "诊断",
    "sec_run_metadata": "运行元数据",
    "sec_trace": "采样轨迹摘要",
    "sec_assess": "评估",
    "sec_manifest": "文件清单",
    "sec_config": "所用配置",
    "sec_limitations": "局限性",
    "col_field": "项目",
    "col_value": "内容",
    "col_gate": "闸门",
    "col_status": "状态",
    "col_basis": "依据 / 原因",
    "col_seed": "随机种子",
    "col_parameter": "参数",
    "col_mean": "均值",
    "col_median": "中位数",
    "col_ess": "有效样本量",
    "col_act": "自相关时间",
    "col_hpd": "95% 最高后验密度区间",
    "col_samples": "样本数",
    "col_path": "路径",
    "col_kind": "类型",
    "col_bytes": "字节数",
    "col_sha256": "SHA-256",
    "col_severity": "严重程度",
    "col_message": "说明",
    "col_fix": "下一步",
    "col_distribution": "分布",
    "col_source": "来源",
    "auto_priors_caption": (
        "以下先验因配置未显式指定而由 BeastXPy 自动补全。生成的 XML 中每个都带有 "
        "'BXP auto prior' 注释；可在 parameter_priors 中覆盖任意一项。"
    ),
    "auto_priors_none": "未找到带自动先验注释的生成 XML。",
    "col_exit_code": "退出码",
    "col_elapsed": "耗时（秒）",
    "conclusion_run_completed": "已记录的 BEAST 运行全部完成。",
    "conclusion_run_partial": "已记录的 BEAST 运行中有部分失败。",
    "conclusion_run_failed": "已记录的 BEAST 运行全部失败。",
    "conclusion_no_chains": "该工作目录中未找到 BEAST 运行子目录。",
    "conclusion_chain_ok": "种子 {seed}：BEAST 以退出码 {code} 结束。",
    "conclusion_chain_failed": "种子 {seed}：BEAST 状态为 {status}（退出码 {code}）。",
    "conclusion_chain_timeout": "种子 {seed}：运行在 {seconds} 秒后超时。",
    "conclusion_chain_skipped": "种子 {seed}：由 --resume 跳过（沿用上次运行结果）。",
    "conclusion_fatal": "种子 {seed}：BEAST 致命错误：{line}",
    "conclusion_assessed": "收敛性评估：{verdict}（来源：{source}）。",
    "conclusion_not_assessed": "收敛性评估：未评估——该工作目录中没有找到评估 JSON。",
    "conclusion_gate3_not_run": "该工作目录中没有记录 Gate 3（官方 BEAST 验证）结果。",
    "assess_not_assessed": "未评估",
    "assess_not_assessed_reason": (
        "该工作目录中没有 beastxpy.assessment/v1 文档，因此此处不报告任何有效样本量、"
        "PSRF、ASDSF 或结论；不做任何估计或补算。"
    ),
    "assess_searched": "已查找的位置",
    "assess_howto": "请先运行 `beastxpy assess <workdir> --json <workdir>/assessment.json`。",
    "assess_source": "评估文档",
    "assess_schema": "评估模式版本",
    "assess_verdict": "结论",
    "assess_strict": "严格模式",
    "assess_thresholds": "阈值",
    "assess_chains": "评估中的链",
    "assess_multichain": "多链诊断",
    "assess_psrf": "PSRF（Gelman-Rubin 统计量）",
    "assess_asdsf": "ASDSF（分裂频率标准差）",
    "assess_reasons": "评估给出的原因",
    "assess_limitations": "评估声明的局限性（原文引用）",
    "assess_parameters": "参数",
    "assess_operators": "算子",
    "assess_burnin": "预烧期",
    "assess_status": "链状态",
    "assess_incomplete": "不完整原因",
    "assess_raw_note": "以下数值均引自评估文档。",
    "not_available": "无数据",
    "not_reported": "未报告",
    "not_computable": "无法计算",
    "not_run": "未运行",
    "passed": "通过",
    "failed": "失败",
    "passed_inferred": "通过（推断）",
    "unknown": "未知",
    "yes": "是",
    "no": "否",
    "none": "无",
    "verdict_pass": "通过",
    "verdict_warn": "警告",
    "verdict_fail": "失败",
    "status_ok": "已完成",
    "status_failed": "失败",
    "status_timeout": "超时",
    "status_skipped": "已跳过（续跑）",
    "gate_0_name": "Gate 0 —— 严格配置解析",
    "gate_1_name": "Gate 1 —— 纯 Python XML 校验",
    "gate_2_name": "Gate 2 —— 定年校准诊断",
    "gate_3_name": "Gate 3 —— 官方 BEAST jar 验证",
    "gate_basis_xml": "推断：`beastxpy run` 仅在 Gate 0 解析成功后才写出生成的 XML。",
    "gate_basis_gate1": "推断：当 Gate 1 对组装文档报错时，运行器不会写出任何 XML。",
    "gate_basis_gate2": "推断：当 Gate 2 报告定年冲突时，运行器不会写出任何 XML。",
    "gate_basis_no_xml": "工作目录中未找到生成的 XML 文档。",
    "gate_basis_gate3_absent": (
        "工作目录中没有 Gate 3 记录（没有带 `validation` 字段的 *.fingerprint.json）；"
        "`beastxpy run` 直接执行 BEAST，并不运行解析器验证闸门。"
    ),
    "gate_basis_gate3_sidecar": "读取自 XML 旁写的指纹文件。",
    "gate_basis_run_evidence": "注意：BEAST 本身在该工作目录中运行过（见运行元数据）；那是执行证据，不是已记录的 Gate 3 验证结果。",
    "prov_fingerprint": "分析指纹",
    "prov_content_fingerprint": "内容指纹",
    "prov_data_digest": "比对数据摘要",
    "prov_beast_version": "BEAST 版本",
    "prov_tool_version": "BeastXPy 版本",
    "prov_analysis_name": "分析名称",
    "prov_java_path": "Java 可执行文件",
    "prov_jar_path": "BEAST jar",
    "prov_jar_sha256": "BEAST jar 的 SHA-256",
    "prov_seeds": "随机种子",
    "prov_generated_xml": "生成的 XML",
    "prov_wall_clock": "实际时间",
    "prov_source": "溯源信息来源",
    "prov_none": "工作目录中未找到 run_meta.json，也未找到指纹注释。",
    "diag_ess_threshold": "有效样本量阈值",
    "diag_columns": "已分析的参数数",
    "diag_burnin": "预烧期（样本数）",
    "diag_chains_in_log": "日志中串接的链数",
    "diag_below_threshold": "低于有效样本量阈值的参数数",
    "diag_nonfinite": "被剔除的非有限样本数",
    "diag_min_ess": "最低有效样本量",
    "diag_no_trace": "该种子没有可解析的轨迹日志。",
    "diag_trace_error": "无法分析该轨迹日志：{message}",
    "diag_warnings_raw": "tracestats 原始警告（工具输出原文，未翻译）",
    "diag_plot": "轨迹图",
    "diag_plot_missing": "没有轨迹图：{reason}",
    "trace_caption": "统计量由 beastxpy.tracestats 从轨迹日志计算；无法估计的值显示为“无数据”。",
    "trace_none": "该工作目录没有可用的轨迹统计。",
    "meta_caption": "由 beastxpy.runner 记录在 run_meta.json 中。",
    "meta_raw": "run_meta.json 原始内容（原文引用）",
    "meta_no_chains": "工作目录中未找到 run_meta.json。",
    "meta_command": "BEAST 命令",
    "meta_trace_log": "轨迹日志",
    "meta_tree_log": "树日志",
    "meta_run_log": "运行日志",
    "meta_checkpoints": "检查点文件",
    "meta_archived": "已归档日志",
    "meta_timed_out": "是否超时",
    "manifest_caption": "生成报告时工作目录中的全部文件及其 SHA-256。",
    "manifest_total": "合计",
    "manifest_files": "个文件",
    "manifest_truncated": "清单仅列出前 {limit} 个文件，另有 {extra} 个未列出。",
    "manifest_excluded": "清单不包含本报告文件及其 --json 输出。",
    "config_caption": "原文嵌入自工作目录；已作 HTML 转义，不会被执行。",
    "config_source": "来源",
    "config_kind_yaml": "YAML 配置",
    "config_kind_xml": "生成的 BEAST XML",
    "config_truncated": "已截断为 {total} 字节中的前 {limit} 字节。",
    "config_absent": (
        "工作目录中没有配置文件。`beastxpy run` 不会把输入 YAML 复制到工作目录，"
        "因此此处不嵌入任何内容，也不做任何重建。"
    ),
    "raw_note": "程序原始输出，原文引用，未翻译。",
    "footer": "由 BeastXPy {version}（`beastxpy report`）生成。",
}

#: Limitations, parallel lists (same order, same count).
LIMITATIONS_EN: List[str] = [
    "This report describes one workdir as recorded on disk. It is not evidence "
    "of convergence, of mixing, or of model adequacy.",
    "When no assessment document is present the assess section says 'not "
    "assessed'. BeastXPy never estimates, interpolates or back-fills ESS, PSRF, "
    "ASDSF or a verdict.",
    "Gate statuses are derived from the artefacts in the workdir. Gates marked "
    "'passed (inferred)' were not recorded by a gate run; the basis column "
    "states exactly which artefact implies them.",
    "Trace statistics are computed by beastxpy.tracestats with its own ACT/ESS "
    "estimator; they are diagnostics, and short chains give uninformative "
    "intervals rather than wrong ones.",
    "A completed BEAST run is execution evidence only. It does not mean the "
    "model is identifiable, that the calibrations are justified, or that the "
    "chain sampled the posterior adequately.",
    "The file manifest hashes what was on disk when the report was generated. "
    "Later runs, --resume or manual edits invalidate it.",
    "Embedded configuration text is quoted verbatim from the workdir. BeastXPy "
    "does not reconstruct a configuration that was not stored there.",
    "The HTML is self-contained on purpose: no script, no stylesheet, no font "
    "and no image is loaded from anywhere, so the document cannot call home.",
]

LIMITATIONS_ZH: List[str] = [
    "本报告只描述磁盘上所记录的那一个工作目录，不构成收敛性、混合效率或模型恰当性的证据。",
    "当没有评估文档时，评估一节明确写“未评估”。BeastXPy 绝不估算、插值或补算有效样本量、PSRF、ASDSF 或结论。",
    "闸门状态由工作目录中的产物推得。标为“通过（推断）”的闸门并非由闸门运行记录，"
    "“依据 / 原因”一列会说明究竟是哪个产物支持该推断。",
    "轨迹统计由 beastxpy.tracestats 用其自有的自相关时间/有效样本量估计器计算，属于诊断信息；"
    "短链只会给出不 informative 的区间，而不会给出“错误但好看”的区间。",
    "BEAST 运行完成仅是执行证据，并不意味模型可识别、定年校准有依据，或链已充分采样后验分布。",
    "文件清单记录的是生成报告那一刻磁盘上的内容及其哈希；此后的运行、--resume 或手工修改都会使其失效。",
    "嵌入的配置文本原文引用自工作目录；BeastXPy 不会重建没有被保存在那里的配置。",
    "该 HTML 刻意自包含：不加载任何脚本、样式表、字体或图片，因此文档不会对外发起任何请求。",
]

LABELS: Dict[str, Dict[str, str]] = {LANG_EN: LABELS_EN, LANG_ZH: LABELS_ZH}
LIMITATIONS: Dict[str, List[str]] = {LANG_EN: LIMITATIONS_EN, LANG_ZH: LIMITATIONS_ZH}

#: Inline stylesheet.  Deliberately contains no ``url(``, no ``@import`` and no
#: protocol-relative reference: the document must render offline from ``file://``.
REPORT_CSS = """
:root { color-scheme: light dark; }
body { margin: 0; padding: 0 0 2rem 0; background: #f6f7f9; color: #1c2430;
  font-family: -apple-system, "Segoe UI", "Microsoft YaHei", "Noto Sans CJK SC",
  "Helvetica Neue", Arial, sans-serif; font-size: 15px; line-height: 1.55; }
header.page { background: #16233a; color: #f2f5fa; padding: 1.4rem 1.6rem; }
header.page h1 { margin: 0 0 .25rem 0; font-size: 1.5rem; }
header.page p { margin: .15rem 0; color: #c7d2e2; font-size: .86rem; }
main { padding: 0 1.6rem; max-width: 1100px; }
nav.toc { background: #ffffff; border: 1px solid #d9dee6; border-radius: 6px;
  padding: .7rem 1rem; margin: 1rem 0; }
nav.toc ol { margin: .3rem 0 0 1.1rem; padding: 0; columns: 2; }
nav.toc a { color: #1d4f8c; text-decoration: none; }
nav.toc a:hover { text-decoration: underline; }
section { background: #ffffff; border: 1px solid #d9dee6; border-radius: 6px;
  padding: .9rem 1.1rem 1.1rem 1.1rem; margin: 1rem 0; }
section > h2 { margin: 0 0 .6rem 0; font-size: 1.15rem; border-bottom: 2px solid
  #e4e8ee; padding-bottom: .35rem; }
h3 { font-size: 1rem; margin: 1rem 0 .35rem 0; }
table { border-collapse: collapse; width: 100%; margin: .4rem 0 .8rem 0;
  font-size: .88rem; }
th, td { border: 1px solid #dde2ea; padding: .32rem .5rem; text-align: left;
  vertical-align: top; }
th { background: #eef1f6; font-weight: 600; white-space: nowrap; }
td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tr:nth-child(even) td { background: #fafbfd; }
code, pre { font-family: "Cascadia Mono", Consolas, "Courier New", monospace;
  font-size: .82rem; }
pre { background: #f2f4f8; border: 1px solid #dde2ea; border-radius: 4px;
  padding: .6rem .7rem; overflow-x: auto; white-space: pre-wrap;
  word-break: break-word; }
.badge { display: inline-block; padding: .1rem .5rem; border-radius: 10px;
  font-size: .8rem; font-weight: 600; border: 1px solid transparent; }
.badge.ok { background: #e3f5e7; color: #1d6b32; border-color: #b6e0c1; }
.badge.warn { background: #fdf3d9; color: #7d5a05; border-color: #eedaa4; }
.badge.bad { background: #fbe4e4; color: #8f1d1d; border-color: #eebcbc; }
.badge.muted { background: #eceff4; color: #4a5567; border-color: #d7dce5; }
.note { color: #4a5567; font-size: .85rem; margin: .2rem 0 .6rem 0; }
.alert { border-left: 4px solid #b26a00; background: #fff8ea; padding: .6rem .8rem;
  border-radius: 0 4px 4px 0; margin: .4rem 0 .8rem 0; }
.alert.stop { border-left-color: #a32020; background: #fdeeee; }
ul.tight { margin: .3rem 0 .6rem 1.2rem; padding: 0; }
ul.tight li { margin: .15rem 0; }
details { border: 1px solid #dde2ea; border-radius: 4px; padding: .4rem .6rem;
  margin: .4rem 0; background: #fafbfd; }
details summary { cursor: pointer; font-weight: 600; font-size: .86rem; }
img.plot { max-width: 100%; height: auto; border: 1px solid #dde2ea;
  border-radius: 4px; margin: .4rem 0; }
footer.page { padding: 1rem 1.6rem; color: #5a6577; font-size: .82rem; }
.small { font-size: .8rem; color: #5a6577; }
""".strip()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _labels(lang: str) -> Dict[str, str]:
    if lang not in LABELS:
        raise C.ContractError(
            f"unsupported report language {lang!r}",
            fix=f"Pass --lang {' or '.join(LANGUAGES)}.",
        )
    return LABELS[lang]


def _esc(value: Any) -> str:
    """Escape *any* value for HTML text or a quoted attribute."""
    return html.escape("" if value is None else str(value), quote=True)


def _fmt(template: str, **values: Any) -> str:
    """Fill a localized template with already-plain values."""
    return template.format(**values)


def _sha256_file(path: Path, limit: Optional[int] = None) -> Optional[str]:
    try:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
                if limit is not None and hasher and limit <= 0:
                    break
        return hasher.hexdigest()
    except OSError:
        return None


def _finite(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _number(value: Any, spec: str = ".5g") -> Optional[float]:
    """Return ``value`` as a finite float, else ``None`` (never a fake 0)."""
    if not _finite(value):
        return None
    return float(value)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        loaded = json.loads(text)
    except ValueError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _json_safe(value: Any) -> Any:
    """Coerce a value so ``json.dumps`` cannot fail on it."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


# ---------------------------------------------------------------------------
# Workdir discovery
# ---------------------------------------------------------------------------


def resolve_workdir(workdir: Any) -> Path:
    """Resolve the report workdir; raises ``ContractError`` when unusable."""
    return C.resolve_existing_dir(workdir, what="workdir")


def find_assessment(
    workdir: Path, explicit: Optional[Any] = None
) -> Tuple[Optional[Path], List[str]]:
    """Locate a ``beastxpy.assessment/v1`` document for ``workdir``.

    Returns ``(path_or_None, searched)``.  An explicit ``--assessment`` path is
    required to exist and to carry the assessment schema; the conventional
    locations are probed silently because their absence is the normal case.
    """
    searched: List[str] = []
    if explicit is not None:
        target = Path(explicit).expanduser()
        searched.append(C.display_path(target))
        if not target.is_file():
            raise C.ContractError(
                f"assessment file not found: {C.display_path(target)}",
                fix=(
                    "Pass the JSON written by `beastxpy assess --json PATH`, or drop "
                    "--assessment to let the report look in the workdir."
                ),
            )
        document = _read_json(target)
        if document is None:
            raise C.ContractError(
                f"assessment file is not readable JSON: {C.display_path(target)}",
                fix="Re-run `beastxpy assess <workdir> --json <path>` to rewrite it.",
            )
        if document.get("schema_version") != C.SCHEMA_ASSESSMENT:
            raise C.ContractError(
                f"{C.display_path(target)} is not a {C.SCHEMA_ASSESSMENT} document "
                f"(schema_version={document.get('schema_version')!r})",
                fix="Point --assessment at the JSON produced by `beastxpy assess`.",
            )
        return target, searched

    for name in ASSESSMENT_CANDIDATES:
        candidate = workdir / name
        searched.append(C.display_path(candidate))
        if not candidate.is_file():
            continue
        document = _read_json(candidate)
        if document and document.get("schema_version") == C.SCHEMA_ASSESSMENT:
            return candidate, searched
    return None, searched


def discover_chains(workdir: Path) -> List[Dict[str, Any]]:
    """Find the per-seed run directories written by :mod:`beastxpy.runner`.

    A directory counts as a chain when it holds ``run_meta.json`` or a
    ``seed_*_trace.log``.  Everything is read tolerantly: a missing or corrupt
    ``run_meta.json`` is recorded as such instead of aborting the report.
    """
    chains: List[Dict[str, Any]] = []
    try:
        entries = sorted(
            (item for item in workdir.iterdir() if item.is_dir()),
            key=lambda item: item.name,
        )
    except OSError:
        return chains
    for directory in entries:
        meta_path = directory / "run_meta.json"
        meta = _read_json(meta_path) if meta_path.is_file() else None
        trace_logs = sorted(directory.glob("seed_*_trace.log"))
        tree_logs = sorted(directory.glob("seed_*_tree.log"))
        run_log = directory / "run.log"
        if meta is None and not trace_logs and not meta_path.is_file():
            continue
        seed: Any = directory.name
        if meta and meta.get("seed") is not None:
            seed = meta.get("seed")
        elif directory.name.lstrip("-").isdigit():
            seed = int(directory.name)
        chains.append(
            {
                "seed": seed,
                "directory": C.display_path(directory),
                "run_meta_path": C.display_path(meta_path) if meta_path.is_file() else None,
                "run_meta": meta,
                "run_meta_readable": bool(meta_path.is_file()),
                "run_meta_parsable": meta is not None,
                "trace_log": C.display_path(trace_logs[0]) if trace_logs else None,
                "tree_log": C.display_path(tree_logs[0]) if tree_logs else None,
                "run_log": C.display_path(run_log) if run_log.is_file() else None,
            }
        )
    return chains


def _fingerprint_comment_fields(workdir: Path) -> Dict[str, str]:
    """Read the ``BXP-*`` fingerprint comment out of a generated XML."""
    found: Dict[str, str] = {}
    for xml_path in sorted(workdir.glob("*.xml")):
        try:
            with xml_path.open("r", encoding="utf-8", errors="replace") as handle:
                head = handle.read(4096)
        except OSError:
            continue
        for line in head.splitlines():
            if not line.lstrip().startswith("<!--") and "BXP-" not in line:
                continue
            for token in line.replace("<!--", " ").replace("-->", " ").splitlines():
                token = token.strip()
                if token.startswith("BXP-") and ":" in token:
                    key, _, value = token.partition(":")
                    found.setdefault(key.strip(), value.strip())
        if found:
            break
    return found


def collect_provenance(workdir: Path, chains: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Assemble provenance from run_meta.json plus the XML fingerprint comment."""
    meta: Dict[str, Any] = {}
    for chain in chains:
        if isinstance(chain.get("run_meta"), dict):
            meta = chain["run_meta"]
            break
    comments = _fingerprint_comment_fields(workdir)
    xml_documents = [
        C.display_path(path) for path in sorted(workdir.glob("*.xml")) if path.is_file()
    ]
    seeds = meta.get("seeds")
    if not isinstance(seeds, list):
        seeds = [chain["seed"] for chain in chains]
    sources: List[str] = []
    if meta:
        sources.append("run_meta.json")
    if comments:
        sources.append("XML fingerprint comment")
    return {
        "sources": sources,
        "fingerprint": meta.get("fingerprint") or comments.get("BXP-fingerprint"),
        "content_fingerprint": (
            meta.get("content_fingerprint") or comments.get("BXP-content-fingerprint")
        ),
        "data_digest": comments.get("BXP-data-digest"),
        "beast_version": meta.get("beast_version") or comments.get("BXP-beast-version"),
        "beastxpy_version": meta.get("beastxpy_version"),
        "analysis_name": (Path(xml_documents[0]).stem if xml_documents else None),
        "java_path": meta.get("java_path"),
        "jar_path": meta.get("jar_path"),
        "jar_sha256": meta.get("jar_sha256"),
        "wall_clock": meta.get("wall_clock"),
        "seeds": [_json_safe(seed) for seed in seeds],
        "generated_xml": xml_documents,
    }


def _gate3_from_sidecar(workdir: Path) -> Optional[Dict[str, Any]]:
    """Read a Gate-3 record from a ``*.fingerprint.json`` side-car, if any."""
    for path in sorted(workdir.glob("*.fingerprint.json")):
        document = _read_json(path)
        if not isinstance(document, dict):
            continue
        validation = document.get("validation")
        if isinstance(validation, dict) and validation.get("status"):
            return {
                "path": C.display_path(path),
                "status": str(validation.get("status")),
                "reason": validation.get("reason"),
                "versions": validation.get("versions") or [],
                "exit_code": validation.get("exit_code"),
                "mode": validation.get("mode"),
                "beagle": validation.get("beagle"),
                "beagle_version": validation.get("beagle_version"),
                "tree_likelihood_rule_source": validation.get("tree_likelihood_rule_source"),
            }
    return None


_AUTO_PRIOR_COMMENT = re.compile(
    r"BXP auto prior \((?P<source>.+?)\):\s*"
    r"parameter=(?P<parameter>[^;]+);\s*distribution=(?P<distribution>[^;]+);"
)


def collect_automatic_priors(workdir: Path) -> List[Dict[str, Any]]:
    """Extract ``BXP auto prior`` annotations from generated XML documents.

    Only comments written by the generator are reported; a missing annotation
    means either an explicit prior or no prior at all, never an inference.
    """
    found: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for path in sorted(workdir.rglob("*.xml")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _AUTO_PRIOR_COMMENT.finditer(text):
            parameter = match.group("parameter").strip()
            distribution = match.group("distribution").strip()
            source = match.group("source").strip()
            key = (parameter, distribution, source)
            if key in found:
                continue
            found[key] = {
                "parameter": parameter,
                "distribution": distribution,
                "source": source,
                "xml_path": C.display_path(path),
            }
    return [found[key] for key in sorted(found)]


def collect_gates(workdir: Path, chains: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Derive the Gate 0-3 status table from artefacts, never from assumption.

    ``status`` is ``passed`` / ``failed`` / ``not_run``; ``inferred`` is true
    when no gate run recorded the result and it is implied by an artefact the
    pipeline only writes after that gate succeeded.  ``basis`` always states
    the evidence.
    """
    xml_documents = [path for path in sorted(workdir.glob("*.xml")) if path.is_file()]
    has_xml = bool(xml_documents)
    has_meta = any(isinstance(chain.get("run_meta"), dict) for chain in chains)
    gates: List[Dict[str, Any]] = []

    gates.append(
        {
            "gate": "0",
            "label_key": "gate_0_name",
            "status": "passed" if (has_xml or has_meta) else "not_run",
            "inferred": bool(has_xml or has_meta),
            "basis_key": "gate_basis_xml" if (has_xml or has_meta) else "gate_basis_no_xml",
            "evidence": (C.display_path(xml_documents[0]) if xml_documents else None),
        }
    )
    gates.append(
        {
            "gate": "1",
            "label_key": "gate_1_name",
            "status": "passed" if (has_xml or has_meta) else "not_run",
            "inferred": bool(has_xml or has_meta),
            "basis_key": "gate_basis_gate1" if (has_xml or has_meta) else "gate_basis_no_xml",
            "evidence": (C.display_path(xml_documents[0]) if xml_documents else None),
        }
    )
    gates.append(
        {
            "gate": "2",
            "label_key": "gate_2_name",
            "status": "passed" if (has_xml or has_meta) else "not_run",
            "inferred": bool(has_xml or has_meta),
            "basis_key": "gate_basis_gate2" if (has_xml or has_meta) else "gate_basis_no_xml",
            "evidence": (C.display_path(xml_documents[0]) if xml_documents else None),
        }
    )

    sidecar = _gate3_from_sidecar(workdir)
    if sidecar is not None:
        status = str(sidecar["status"])
        mapped = {
            "passed": "passed",
            "failed": "failed",
            "not-run": "not_run",
            "not-requested": "not_run",
        }.get(status, "not_run")
        gates.append(
            {
                "gate": "3",
                "label_key": "gate_3_name",
                "status": mapped,
                "inferred": False,
                "basis_key": "gate_basis_gate3_sidecar",
                "evidence": sidecar["path"],
                "reason": sidecar.get("reason"),
                "recorded_status": status,
                "versions": sidecar.get("versions"),
                "exit_code": sidecar.get("exit_code"),
                "mode": sidecar.get("mode"),
                "beagle": sidecar.get("beagle"),
                "beagle_version": sidecar.get("beagle_version"),
                "tree_likelihood_rule_source": sidecar.get("tree_likelihood_rule_source"),
            }
        )
    else:
        gates.append(
            {
                "gate": "3",
                "label_key": "gate_3_name",
                "status": "not_run",
                "inferred": False,
                "basis_key": "gate_basis_gate3_absent",
                "evidence": None,
                "extra_key": "gate_basis_run_evidence" if has_meta else None,
            }
        )
    return gates


# ---------------------------------------------------------------------------
# Trace diagnostics (reuse tracestats; never a second parser)
# ---------------------------------------------------------------------------


def _fatal_line(run_log: Optional[str]) -> Optional[str]:
    """Return BEAST's fatal-error line from a run log, if there is one."""
    if not run_log:
        return None
    path = Path(run_log)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Fatal exception"):
            return stripped[:400]
    return None


def analyse_trace(
    trace_log: Optional[str],
    *,
    ess_threshold: float = 200.0,
    with_plot: bool = True,
) -> Dict[str, Any]:
    """Compute the trace summary for one chain with :mod:`beastxpy.tracestats`.

    Returns a dict with ``available`` plus, when the log parsed, the burn-in,
    per-parameter statistics, the raw warning list and an inline PNG.  A log
    that cannot be parsed reports the reason; no statistic is invented.
    """
    result: Dict[str, Any] = {
        "available": False,
        "trace_log": trace_log,
        "ess_threshold": float(ess_threshold),
        "reason": None,
        "stats": None,
        "parameters": [],
        "warnings": [],
        "plot": None,
        "text_table": None,
    }
    if not trace_log:
        result["reason"] = "no trace log path"
        return result
    path = Path(trace_log)
    if not path.is_file():
        result["reason"] = f"trace log not found: {C.display_path(path)}"
        return result
    try:
        parsed = parse_trace_log(str(path))
    except (TraceError, OSError, ValueError) as exc:
        result["reason"] = f"{type(exc).__name__}: {exc}"
        return result

    chains = [(item["start"], item["end"]) for item in parsed.get("chains", [])]
    try:
        stats = compute_trace_stats(
            parsed["values"],
            burnin=None,
            ess_threshold=float(ess_threshold),
            chains=chains or None,
        )
    except (TraceError, ValueError) as exc:
        result["reason"] = f"{type(exc).__name__}: {exc}"
        return result

    parameters: List[Dict[str, Any]] = []
    below = 0
    nonfinite = 0
    lowest: Optional[float] = None
    for column in stats.get("columns", [])[:PARAMETER_ROW_LIMIT]:
        name = str(column.get("name", ""))
        if name.strip().lower() in NON_PARAMETER_COLUMNS:
            continue
        ess = _number(column.get("ess"))
        nonfinite += int(column.get("n_nonfinite") or 0)
        if ess is not None:
            lowest = ess if lowest is None else min(lowest, ess)
            if ess < float(ess_threshold):
                below += 1
        parameters.append(
            {
                "name": name,
                "n": _json_safe(column.get("n")),
                "mean": _number(column.get("mean")),
                "median": _number(column.get("median")),
                "ess": ess,
                "act": _number(column.get("act")),
                "hpd_lower": _number(column.get("hpd_lower")),
                "hpd_upper": _number(column.get("hpd_upper")),
                "ess_below_threshold": bool(ess is not None and ess < float(ess_threshold)),
            }
        )

    result.update(
        {
            "available": True,
            "stats": {
                "burnin": _json_safe(stats.get("burnin")),
                "n_chains": _json_safe(stats.get("n_chains")),
                "n_columns": len(stats.get("columns", [])),
                "n_samples": _json_safe(parsed.get("n_samples")),
                "beast_version": parsed.get("beast_version"),
            },
            "parameters": parameters,
            "summary": {
                "parameters_analysed": len(parameters),
                "below_ess_threshold": below,
                "lowest_ess": lowest,
                "nonfinite_dropped": nonfinite,
            },
            "warnings": [str(item) for item in stats.get("warnings", [])],
            "text_table": format_stats_table(stats, ess_threshold=float(ess_threshold)),
        }
    )
    if with_plot:
        result["plot"] = _render_plot(parsed["values"], int(stats.get("burnin") or 0))
    return result


def _render_plot(values: Dict[str, Any], burnin: int) -> Dict[str, Any]:
    """Render the trace plot to a temporary PNG and inline it as base64."""
    plot: Dict[str, Any] = {"present": False, "reason": None}
    try:
        with tempfile.TemporaryDirectory(prefix="bxp_report_plot_") as scratch:
            png = Path(scratch) / "trace.png"
            with _warnings.catch_warnings():
                # Matplotlib warns about missing CJK glyphs; the plot is still
                # written, and the warning must not become a report failure.
                _warnings.simplefilter("ignore")
                write_trace_plot(values, burnin, str(png))
            payload = png.read_bytes()
    except Exception as exc:  # noqa: BLE001 - reported, never silently dropped
        plot["reason"] = f"{type(exc).__name__}: {exc}"
        return plot
    encoded = base64.b64encode(payload).decode("ascii")
    plot.update(
        {
            "present": True,
            "mime": "image/png",
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "data_uri": f"data:image/png;base64,{encoded}",
        }
    )
    return plot


# ---------------------------------------------------------------------------
# Assessment (Worker A's schema; read-only, never re-derived)
# ---------------------------------------------------------------------------


def summarise_assessment(
    document: Optional[Dict[str, Any]],
    source: Optional[Path],
    searched: Sequence[str],
) -> Dict[str, Any]:
    """Project an assessment document onto the report summary.

    With no document the result says ``not_assessed`` and lists what was
    searched.  With a document only the values it actually contains are
    carried over: a missing key becomes ``None`` and renders as the localized
    "not reported" label.
    """
    if document is None:
        return {
            "available": False,
            "status": "not_assessed",
            "source": None,
            "searched": list(searched),
            "schema_version": None,
            "verdict": None,
            "strict": None,
            "thresholds": None,
            "chains": [],
            "multichain": None,
            "reasons": [],
            "limitations": [],
        }
    chains: List[Dict[str, Any]] = []
    for raw in document.get("chains") or []:
        if not isinstance(raw, dict):
            continue
        parameters = raw.get("parameters") or {}
        ess_values = []
        if isinstance(parameters, dict):
            for stats in parameters.values():
                if isinstance(stats, dict):
                    value = _number(stats.get("ess"))
                    if value is not None:
                        ess_values.append(value)
        operators = raw.get("operators") or []
        chains.append(
            {
                "seed": _json_safe(raw.get("seed")),
                "status": raw.get("status"),
                "samples": _json_safe(raw.get("samples")),
                "burnin_fraction": _number(raw.get("burnin_fraction")),
                "burnin_samples": _json_safe(raw.get("burnin_samples")),
                "n_parameters": len(parameters) if isinstance(parameters, dict) else None,
                "lowest_ess": min(ess_values) if ess_values else None,
                "n_operators": len(operators) if isinstance(operators, list) else None,
                "tree_summary": (
                    _json_safe(raw.get("tree_summary"))
                    if raw.get("tree_summary") is not None
                    else None
                ),
                "incomplete_reason": raw.get("incomplete_reason"),
            }
        )
    reasons: List[Dict[str, Any]] = []
    for raw in document.get("reasons") or []:
        if isinstance(raw, dict):
            reasons.append(
                {
                    "id": raw.get("id"),
                    "severity": raw.get("severity"),
                    "message": raw.get("message"),
                    "fix": raw.get("fix"),
                }
            )
    return {
        "available": True,
        "status": "assessed",
        "source": C.display_path(source) if source else None,
        "searched": list(searched),
        "schema_version": document.get("schema_version"),
        "verdict": document.get("verdict"),
        "strict": document.get("strict"),
        "thresholds": _json_safe(document.get("thresholds")),
        "chains": chains,
        "multichain": _json_safe(document.get("multichain")),
        "reasons": reasons,
        "limitations": [str(item) for item in (document.get("limitations") or [])],
    }


# ---------------------------------------------------------------------------
# Manifest / configuration
# ---------------------------------------------------------------------------


def collect_manifest(workdir: Path, exclude: Sequence[Path] = ()) -> Dict[str, Any]:
    """Hash every file in the workdir (excluding the report's own outputs)."""
    excluded = {str(Path(item).resolve()) for item in exclude if item}
    files: List[Dict[str, Any]] = []
    total_bytes = 0
    count = 0
    for path in sorted(workdir.rglob("*")):
        if not path.is_file():
            continue
        count += 1
        if str(path.resolve()) in excluded:
            continue
        if len(files) >= MANIFEST_FILE_LIMIT:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        total_bytes += int(size or 0)
        files.append(
            {
                "path": C.display_path(path.relative_to(workdir)),
                "kind": path.suffix.lstrip(".").lower() or "file",
                "bytes": size,
                "sha256": _sha256_file(path),
            }
        )
    return {
        "files": files,
        "listed": len(files),
        "found": count,
        "truncated": count - len(files) > 0,
        "total_bytes": total_bytes,
        "excluded": [C.display_path(item) for item in exclude if item],
    }


def collect_config(workdir: Path, include: bool) -> Dict[str, Any]:
    """Embed the configuration found in the workdir (verbatim, escaped later).

    ``beastxpy run`` does not copy the input YAML into the workdir, so the
    generated XML is the fallback artefact.  When neither exists the section
    says so; nothing is reconstructed.
    """
    result: Dict[str, Any] = {
        "included": bool(include),
        "requested": bool(include),
        "source": None,
        "kind": None,
        "bytes": None,
        "sha256": None,
        "truncated": False,
        "text": None,
    }
    if not include:
        return result
    candidates: List[Tuple[Path, str]] = []
    for pattern in ("*.yaml", "*.yml"):
        candidates.extend((path, "yaml") for path in sorted(workdir.glob(pattern)))
    candidates.extend((path, "xml") for path in sorted(workdir.glob("*.xml")))
    for path, kind in candidates:
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        text = raw[:CONFIG_TEXT_LIMIT].decode("utf-8", errors="replace")
        result.update(
            {
                "source": C.display_path(path),
                "kind": kind,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "truncated": len(raw) > CONFIG_TEXT_LIMIT,
                "text": text,
            }
        )
        return result
    return result


# ---------------------------------------------------------------------------
# Conclusion
# ---------------------------------------------------------------------------


def build_conclusion(
    lang: str,
    chains: List[Dict[str, Any]],
    gates: List[Dict[str, Any]],
    assessment: Dict[str, Any],
) -> Dict[str, Any]:
    """Derive the conclusion from recorded facts only."""
    labels = _labels(lang)
    statuses: List[str] = []
    reasons: List[str] = []
    for chain in chains:
        raw_meta = chain.get("run_meta")
        meta: Dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
        status = str(meta.get("status") or "unknown")
        statuses.append(status)
        code = meta.get("exit_code")
        seed = chain.get("seed")
        if meta.get("timed_out"):
            reasons.append(
                _fmt(
                    labels["conclusion_chain_timeout"],
                    seed=seed,
                    seconds=meta.get("elapsed_seconds", labels["not_available"]),
                )
            )
        elif status == _STATUS_SKIPPED:
            reasons.append(_fmt(labels["conclusion_chain_skipped"], seed=seed))
        elif status == _STATUS_OK:
            reasons.append(_fmt(labels["conclusion_chain_ok"], seed=seed, code=code))
        else:
            reasons.append(
                _fmt(
                    labels["conclusion_chain_failed"],
                    seed=seed,
                    status=status,
                    code=code if code is not None else labels["not_available"],
                )
            )
        fatal = _fatal_line(chain.get("run_log"))
        if fatal:
            reasons.append(_fmt(labels["conclusion_fatal"], seed=seed, line=fatal))

    ok_count = sum(1 for item in statuses if item == _STATUS_OK)
    if not chains:
        status = "no_chains"
        headline = labels["conclusion_no_chains"]
    elif ok_count == len(chains):
        status = "completed"
        headline = labels["conclusion_run_completed"]
    elif ok_count:
        status = "partial"
        headline = labels["conclusion_run_partial"]
    else:
        status = "failed"
        headline = labels["conclusion_run_failed"]

    if assessment.get("available"):
        reasons.append(
            _fmt(
                labels["conclusion_assessed"],
                verdict=assessment.get("verdict") or labels["not_reported"],
                source=assessment.get("source") or labels["not_available"],
            )
        )
    else:
        reasons.append(labels["conclusion_not_assessed"])

    gate3 = next((item for item in gates if item["gate"] == "3"), None)
    if gate3 is not None and gate3["status"] == "not_run":
        reasons.append(labels["conclusion_gate3_not_run"])

    return {
        "status": status,
        "headline": headline,
        "reasons": reasons,
        "chains_total": len(chains),
        "chains_ok": ok_count,
        "assessed": bool(assessment.get("available")),
        "gate3_status": gate3["status"] if gate3 else None,
    }


# ---------------------------------------------------------------------------
# Collection entry point
# ---------------------------------------------------------------------------


def collect_report_data(
    workdir: Any,
    *,
    lang: str = LANG_EN,
    include_config: bool = False,
    assessment_path: Optional[Any] = None,
    ess_threshold: float = 200.0,
    with_plot: bool = True,
    output_path: Optional[Any] = None,
    json_path: Optional[Any] = None,
) -> Dict[str, Any]:
    """Collect every value the HTML report renders (and the JSON carries).

    All text is plain (unescaped) here; escaping happens once, in
    :func:`render_html`.  All numbers are either computed from a file in the
    workdir or ``None``.
    """
    _labels(lang)  # validates the language early
    root = resolve_workdir(workdir)
    chains = discover_chains(root)
    for chain in chains:
        chain["diagnostics"] = analyse_trace(
            chain.get("trace_log"),
            ess_threshold=ess_threshold,
            with_plot=with_plot,
        )
    gates = collect_gates(root, chains)
    assessment_file, searched = find_assessment(root, assessment_path)
    assessment_doc = _read_json(assessment_file) if assessment_file else None
    assessment = summarise_assessment(assessment_doc, assessment_file, searched)
    provenance = collect_provenance(root, chains)
    manifest = collect_manifest(
        root, exclude=[Path(item) for item in (output_path, json_path) if item]
    )
    config = collect_config(root, include_config)
    auto_priors = collect_automatic_priors(root)
    conclusion = build_conclusion(lang, chains, gates, assessment)
    return {
        "lang": lang,
        "workdir": C.display_path(root),
        "output_html": C.display_path(output_path) if output_path else None,
        "ess_threshold": float(ess_threshold),
        "conclusion": conclusion,
        "provenance": provenance,
        "gates": gates,
        "auto_priors": auto_priors,
        "run_metadata": {
            "chains": [
                {
                    "seed": chain["seed"],
                    "directory": chain["directory"],
                    "run_meta_path": chain["run_meta_path"],
                    "run_meta_readable": chain["run_meta_readable"],
                    "run_meta_parsable": chain["run_meta_parsable"],
                    "run_meta": _json_safe(chain.get("run_meta")),
                    "trace_log": chain.get("trace_log"),
                    "tree_log": chain.get("tree_log"),
                    "run_log": chain.get("run_log"),
                    "fatal_line": _fatal_line(chain.get("run_log")),
                }
                for chain in chains
            ]
        },
        "diagnostics": {
            "chains": [
                {
                    "seed": chain["seed"],
                    "trace_log": chain.get("trace_log"),
                    "available": chain["diagnostics"]["available"],
                    "reason": chain["diagnostics"]["reason"],
                    "stats": chain["diagnostics"]["stats"],
                    "summary": chain["diagnostics"].get("summary"),
                    "warnings": chain["diagnostics"]["warnings"],
                    "plot": _public_plot(chain["diagnostics"].get("plot")),
                }
                for chain in chains
            ]
        },
        "trace_summary": {
            "chains": [
                {
                    "seed": chain["seed"],
                    "available": chain["diagnostics"]["available"],
                    "parameters": chain["diagnostics"]["parameters"],
                }
                for chain in chains
            ]
        },
        "assessment": assessment,
        "manifest": manifest,
        "config": config,
        "limitations": list(LIMITATIONS[lang]),
        "_internal": {"chains": chains},
    }


def _public_plot(plot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The JSON view of a plot: metadata only, never the base64 payload."""
    if not plot:
        return {"present": False, "reason": None}
    return {
        "present": bool(plot.get("present")),
        "reason": plot.get("reason"),
        "mime": plot.get("mime"),
        "bytes": plot.get("bytes"),
        "sha256": plot.get("sha256"),
    }


def to_envelope(data: Dict[str, Any]) -> Dict[str, Any]:
    """Build the ``beastxpy.report/v1`` document from collected data."""
    payload = {key: value for key, value in data.items() if not key.startswith("_")}
    payload["diagnostics"] = {
        "chains": [
            dict(chain, plot=_public_plot(chain.get("plot")))
            for chain in data["diagnostics"]["chains"]
        ]
    }
    return C.envelope(C.SCHEMA_REPORT, **_json_safe(payload))


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


def _badge(text: str, kind: str) -> str:
    return f'<span class="badge {_esc(kind)}">{_esc(text)}</span>'


def _status_badge(status: Any, labels: Dict[str, str]) -> str:
    text = str(status) if status is not None else labels["not_available"]
    kind = {
        "passed": "ok",
        "ok": "ok",
        "completed": "ok",
        "pass": "ok",
        "supported": "ok",
        "not_run": "muted",
        "not_assessed": "warn",
        "unknown": "muted",
        "failed": "bad",
        "fail": "bad",
        "timeout": "bad",
        "warn": "warn",
        "partial": "warn",
        "skipped": "muted",
    }.get(text, "muted")
    return _badge(text, kind)


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    head = "".join(f"<th>{item}</th>" for item in headers)
    body: List[str] = []
    for row in rows:
        cells = "".join(f"<td>{cell}</td>" for cell in row)
        body.append(f"<tr>{cells}</tr>")
    if not body:
        body.append(f'<tr><td colspan="{len(headers)}"><em>-</em></td></tr>')
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _value_or(value: Any, labels: Dict[str, str], missing_key: str = "not_available") -> str:
    if value is None or value == "" or value == []:
        return _esc(labels[missing_key])
    if isinstance(value, bool):
        return _esc(labels["yes"] if value else labels["no"])
    if isinstance(value, (list, tuple)):
        return _esc(", ".join(str(item) for item in value))
    return _esc(value)


def _number_or(value: Any, labels: Dict[str, str], spec: str = ".5g") -> str:
    number = _number(value)
    if number is None:
        return _esc(labels["not_available"])
    return _esc(format(number, spec))


def _kv_table(pairs: Sequence[Tuple[str, Any]], labels: Dict[str, str]) -> str:
    rows = [
        (f"<th>{_esc(name)}</th>", f"<td>{_value_or(value, labels)}</td>") for name, value in pairs
    ]
    body = "".join(f"<tr>{left}{right}</tr>" for left, right in rows)
    return (
        "<table><thead><tr>"
        f"<th>{_esc(labels['col_field'])}</th><th>{_esc(labels['col_value'])}</th>"
        f"</tr></thead><tbody>{body}</tbody></table>"
    )


def _section(anchor: str, title: str, body: str) -> str:
    return f'<section id="{_esc(anchor)}"><h2>{_esc(title)}</h2>{body}</section>'


def _chain_status_label(status: Any, labels: Dict[str, str]) -> str:
    mapping = {
        "ok": labels["status_ok"],
        "failed": labels["status_failed"],
        "timeout": labels["status_timeout"],
        "skipped": labels["status_skipped"],
    }
    return mapping.get(str(status), labels["unknown"])


def _render_conclusion(data: Dict[str, Any], labels: Dict[str, str]) -> str:
    conclusion = data["conclusion"]
    kind = {
        "completed": "ok",
        "partial": "warn",
        "failed": "bad",
        "no_chains": "muted",
    }.get(conclusion["status"], "muted")
    parts = [
        f'<p class="alert{" stop" if kind == "bad" else ""}">'
        f"{_badge(_esc(conclusion['headline']), kind)} "
        f"{_esc(conclusion['headline'])}</p>"
    ]
    items = "".join(f"<li>{_esc(reason)}</li>" for reason in conclusion["reasons"])
    parts.append('<ul class="tight">{}</ul>'.format(items))
    assessment = data["assessment"]
    gate_rows = [
        (
            _esc(labels["sec_gates"].split(" (")[0] + f" {gate['gate']}"),
            _status_badge(gate["status"], labels),
        )
        for gate in data["gates"]
    ]
    parts.append(
        _table(
            [
                _esc(labels["col_gate"]),
                _esc(labels["col_status"]),
            ],
            [
                (
                    _esc(f"{labels['sec_gates']} {gate['gate']}"),
                    _status_badge(gate["status"], labels),
                )
                for gate in data["gates"]
            ]
            + [
                (
                    _esc(labels["sec_assess"]),
                    _status_badge(
                        assessment["verdict"] if assessment["available"] else "not_assessed",
                        labels,
                    ),
                )
            ],
        )
    )
    del gate_rows
    return "".join(parts)


def _render_provenance(data: Dict[str, Any], labels: Dict[str, str]) -> str:
    prov = data["provenance"]
    if not prov["sources"] and not prov["generated_xml"]:
        return f'<p class="note">{_esc(labels["prov_none"])}</p>'
    pairs = [
        (labels["prov_fingerprint"], prov.get("fingerprint")),
        (labels["prov_content_fingerprint"], prov.get("content_fingerprint")),
        (labels["prov_data_digest"], prov.get("data_digest")),
        (labels["prov_analysis_name"], prov.get("analysis_name")),
        (labels["prov_beast_version"], prov.get("beast_version")),
        (labels["prov_tool_version"], prov.get("beastxpy_version")),
        (labels["prov_seeds"], prov.get("seeds")),
        (labels["prov_java_path"], prov.get("java_path")),
        (labels["prov_jar_path"], prov.get("jar_path")),
        (labels["prov_jar_sha256"], prov.get("jar_sha256")),
        (labels["prov_wall_clock"], prov.get("wall_clock")),
        (labels["prov_generated_xml"], prov.get("generated_xml")),
        (labels["prov_source"], prov.get("sources")),
    ]
    return _kv_table(pairs, labels)


def _render_gates(data: Dict[str, Any], labels: Dict[str, str]) -> str:
    rows: List[Tuple[str, str, str, str]] = []
    for gate in data["gates"]:
        status = gate["status"]
        badge_text = labels["not_run"] if status == "not_run" else labels.get(status, status)
        if status == "passed" and gate.get("inferred"):
            badge_text = labels["passed_inferred"]
        basis = labels.get(str(gate.get("basis_key")), "")
        extra_key = gate.get("extra_key")
        if extra_key:
            basis = f"{basis} {labels.get(str(extra_key), '')}".strip()
        reason = gate.get("reason")
        if reason:
            basis = f"{basis} {reason}".strip()
        mode = gate.get("mode")
        if mode:
            basis = f"{basis} mode={mode}".strip()
        rule_source = gate.get("tree_likelihood_rule_source")
        if rule_source:
            basis = f"{basis} tree_likelihood_rule_source={rule_source}".strip()
        evidence = gate.get("evidence")
        if evidence:
            basis = f"{basis} [{evidence}]".strip()
        rows.append(
            (
                f"<td>{_esc(gate['gate'])}</td>",
                f"<td>{_esc(labels.get(str(gate['label_key']), gate['gate']))}</td>",
                f"<td>{_badge(badge_text, 'ok' if status == 'passed' else ('bad' if status == 'failed' else 'muted'))}</td>",
                f"<td>{_esc(basis)}</td>",
            )
        )
    body = "".join(f"<tr>{''.join(row)}</tr>" for row in rows)
    return (
        "<table><thead><tr>"
        f"<th>{_esc(labels['col_gate'])}</th><th>{_esc(labels['col_field'])}</th>"
        f"<th>{_esc(labels['col_status'])}</th><th>{_esc(labels['col_basis'])}</th>"
        "</tr></thead><tbody>"
        f"{body}</tbody></table>"
    )


def _render_automatic_priors(data: Dict[str, Any], labels: Dict[str, str]) -> str:
    priors = data.get("auto_priors") or []
    if not priors:
        return f'<p class="note">{_esc(labels["auto_priors_none"])}</p>'
    rows = [
        (
            f"<td><code>{_esc(item['parameter'])}</code></td>",
            f"<td><code>{_esc(item['distribution'])}</code></td>",
            f"<td>{_esc(item['source'])}</td>",
            f"<td><code>{_esc(item['xml_path'])}</code></td>",
        )
        for item in priors
    ]
    body = "".join(f"<tr>{''.join(row)}</tr>" for row in rows)
    return (
        f'<p class="note">{_esc(labels["auto_priors_caption"])}</p>'
        "<table><thead><tr>"
        f"<th>{_esc(labels['col_parameter'])}</th>"
        f"<th>{_esc(labels['col_distribution'])}</th>"
        f"<th>{_esc(labels['col_source'])}</th>"
        f"<th>{_esc(labels['col_path'])}</th>"
        "</tr></thead><tbody>"
        f"{body}</tbody></table>"
    )


def _render_diagnostics(data: Dict[str, Any], labels: Dict[str, str]) -> str:
    blocks: List[str] = []
    for chain in data["diagnostics"]["chains"]:
        header = f"<h3>{_esc(labels['col_seed'])} {_esc(chain['seed'])}</h3>"
        if not chain["available"]:
            reason = chain.get("reason") or labels["diag_no_trace"]
            blocks.append(
                header + f'<p class="note">{_esc(labels["diag_no_trace"])} ' f"{_esc(reason)}</p>"
            )
            continue
        stats = chain.get("stats") or {}
        summary = chain.get("summary") or {}
        pairs = [
            (labels["col_samples"], stats.get("n_samples")),
            (labels["diag_burnin"], stats.get("burnin")),
            (labels["diag_columns"], summary.get("parameters_analysed")),
            (labels["diag_chains_in_log"], stats.get("n_chains")),
            (labels["diag_min_ess"], summary.get("lowest_ess")),
            (labels["diag_below_threshold"], summary.get("below_ess_threshold")),
            (labels["diag_nonfinite"], summary.get("nonfinite_dropped")),
            (labels["diag_ess_threshold"], data["ess_threshold"]),
        ]
        blocks.append(header + _kv_table(pairs, labels))
        warnings_list = chain.get("warnings") or []
        if warnings_list:
            items = "".join(f"<li>{_esc(item)}</li>" for item in warnings_list)
            blocks.append(
                "<details><summary>"
                f"{_esc(labels['diag_warnings_raw'])}"
                f"</summary><p class=\"small\">{_esc(labels['raw_note'])}</p>"
                f'<ul class="tight">{items}</ul></details>'
            )
        plot = chain.get("plot") or {}
        internal = _internal_plot(data, chain["seed"])
        if plot.get("present") and internal:
            blocks.append(
                f"<h4>{_esc(labels['diag_plot'])}</h4>"
                f'<img class="plot" alt="{_esc(labels["diag_plot"])}" '
                f'src="{_esc(internal)}" />'
            )
        elif not plot.get("present"):
            blocks.append(
                f'<p class="note">{_esc(labels["diag_plot_missing"])}</p>'.replace(
                    "{reason}", _esc(str(plot.get("reason") or labels["not_available"]))
                )
            )
    if not blocks:
        blocks.append(f'<p class="note">{_esc(labels["diag_no_trace"])}</p>')
    return "".join(blocks)


def _internal_plot(data: Dict[str, Any], seed: Any) -> Optional[str]:
    for chain in data.get("_internal", {}).get("chains", []):
        if chain["seed"] == seed:
            plot = chain.get("diagnostics", {}).get("plot") or {}
            if plot.get("present"):
                return str(plot.get("data_uri"))
    return None


def _render_run_metadata(data: Dict[str, Any], labels: Dict[str, str]) -> str:
    chains = data["run_metadata"]["chains"]
    if not chains:
        return f'<p class="note">{_esc(labels["meta_no_chains"])}</p>'
    blocks: List[str] = [f'<p class="note">{_esc(labels["meta_caption"])}</p>']
    rows: List[Tuple[str, ...]] = []
    for chain in chains:
        meta = chain.get("run_meta") or {}
        rows.append(
            (
                f"<td>{_esc(chain['seed'])}</td>",
                f"<td>{_esc(_chain_status_label(meta.get('status'), labels))}</td>",
                f"<td class=\"num\">{_value_or(meta.get('exit_code'), labels)}</td>",
                f"<td class=\"num\">{_value_or(meta.get('elapsed_seconds'), labels)}</td>",
                f"<td>{_esc(_value_or(meta.get('beast_version'), labels))}</td>",
                f"<td>{_esc(labels['yes'] if meta.get('timed_out') else labels['no'])}</td>",
                f"<td>{_esc(_value_or(chain.get('trace_log'), labels))}</td>",
            )
        )
    body = "".join(f"<tr>{''.join(row)}</tr>" for row in rows)
    blocks.append(
        "<table><thead><tr>"
        f"<th>{_esc(labels['col_seed'])}</th>"
        f"<th>{_esc(labels['col_status'])}</th>"
        f"<th>{_esc(labels['col_exit_code'])}</th>"
        f"<th>{_esc(labels['col_elapsed'])}</th>"
        f"<th>{_esc(labels['prov_beast_version'])}</th>"
        f"<th>{_esc(labels['meta_timed_out'])}</th>"
        f"<th>{_esc(labels['meta_trace_log'])}</th>"
        "</tr></thead><tbody>"
        f"{body}</tbody></table>"
    )
    for chain in chains:
        meta = chain.get("run_meta")
        header = f"<h3>{_esc(labels['col_seed'])} {_esc(chain['seed'])}</h3>"
        if not isinstance(meta, dict):
            note = (
                labels["meta_no_chains"]
                if not chain.get("run_meta_readable")
                else labels["not_available"]
            )
            blocks.append(header + f'<p class="note">{_esc(note)}</p>')
            continue
        pairs = [
            (labels["col_status"], _chain_status_label(meta.get("status"), labels)),
            (labels["col_exit_code"], meta.get("exit_code")),
            (labels["col_elapsed"], meta.get("elapsed_seconds")),
            (labels["meta_timed_out"], bool(meta.get("timed_out"))),
            (labels["prov_wall_clock"], meta.get("wall_clock")),
            (labels["prov_java_path"], meta.get("java_path")),
            (labels["prov_jar_path"], meta.get("jar_path")),
            (labels["prov_jar_sha256"], meta.get("jar_sha256")),
            (labels["meta_command"], " ".join(str(p) for p in (meta.get("command") or []))),
            (labels["meta_trace_log"], chain.get("trace_log")),
            (labels["meta_tree_log"], chain.get("tree_log")),
            (labels["meta_run_log"], chain.get("run_log")),
            (labels["meta_checkpoints"], meta.get("checkpoint_files")),
            (labels["meta_archived"], meta.get("archived_logs")),
        ]
        blocks.append(header + _kv_table(pairs, labels))
        raw = json.dumps(meta, indent=2, ensure_ascii=False, sort_keys=True)
        blocks.append(
            "<details><summary>"
            f"{_esc(labels['meta_raw'])}"
            f"</summary><p class=\"small\">{_esc(labels['raw_note'])}</p>"
            f"<pre>{_esc(raw)}</pre></details>"
        )
        if chain.get("fatal_line"):
            blocks.append(
                '<div class="alert stop"><strong>BEAST</strong> '
                f"<pre>{_esc(chain['fatal_line'])}</pre></div>"
            )
    return "".join(blocks)


def _render_trace(data: Dict[str, Any], labels: Dict[str, str]) -> str:
    chains = [chain for chain in data["trace_summary"]["chains"] if chain["available"]]
    if not chains:
        return f'<p class="note">{_esc(labels["trace_none"])}</p>'
    parts = [f'<p class="note">{_esc(labels["trace_caption"])}</p>']
    for chain in chains:
        rows: List[Tuple[str, ...]] = []
        for parameter in chain["parameters"]:
            lower = parameter.get("hpd_lower")
            upper = parameter.get("hpd_upper")
            if lower is None or upper is None:
                hpd = labels["not_available"]
            else:
                hpd = f"[{format(lower, '.5g')}, {format(upper, '.5g')}]"
            ess = parameter.get("ess")
            ess_text = labels["not_available"] if ess is None else format(ess, ".1f")
            if parameter.get("ess_below_threshold"):
                ess_text = f"{ess_text} *"
            rows.append(
                (
                    f"<td>{_esc(parameter['name'])}</td>",
                    f"<td class=\"num\">{_number_or(parameter.get('mean'), labels)}</td>",
                    f"<td class=\"num\">{_number_or(parameter.get('median'), labels)}</td>",
                    f'<td class="num">{_esc(ess_text)}</td>',
                    f"<td class=\"num\">{_number_or(parameter.get('act'), labels, '.3g')}</td>",
                    f'<td class="num">{_esc(hpd)}</td>',
                )
            )
        body = "".join(f"<tr>{''.join(row)}</tr>" for row in rows)
        parts.append(
            f"<h3>{_esc(labels['col_seed'])} {_esc(chain['seed'])}</h3>"
            "<table><thead><tr>"
            f"<th>{_esc(labels['col_parameter'])}</th>"
            f"<th>{_esc(labels['col_mean'])}</th>"
            f"<th>{_esc(labels['col_median'])}</th>"
            f"<th>{_esc(labels['col_ess'])}</th>"
            f"<th>{_esc(labels['col_act'])}</th>"
            f"<th>{_esc(labels['col_hpd'])}</th>"
            "</tr></thead><tbody>"
            f"{body}</tbody></table>"
        )
    parts.append(
        f'<p class="small">* {_esc(labels["diag_ess_threshold"])} = '
        f"{_esc(data['ess_threshold'])}</p>"
    )
    return "".join(parts)


def _render_assessment(data: Dict[str, Any], labels: Dict[str, str]) -> str:
    assessment = data["assessment"]
    if not assessment["available"]:
        searched = "".join(f"<li><code>{_esc(item)}</code></li>" for item in assessment["searched"])
        return (
            '<div class="alert">'
            f"<p>{_badge(labels['assess_not_assessed'], 'warn')} "
            f"<strong>{_esc(labels['assess_not_assessed'])}</strong></p>"
            f"<p>{_esc(labels['assess_not_assessed_reason'])}</p>"
            f"<p>{_esc(labels['assess_howto'])}</p>"
            "</div>"
            f"<p class=\"note\">{_esc(labels['assess_searched'])}:</p>"
            f'<ul class="tight">{searched}</ul>'
        )
    parts = [f'<p class="note">{_esc(labels["assess_raw_note"])}</p>']
    verdict = assessment.get("verdict")
    verdict_text = {
        "pass": labels["verdict_pass"],
        "warn": labels["verdict_warn"],
        "fail": labels["verdict_fail"],
    }.get(str(verdict), labels["not_reported"])
    pairs = [
        (labels["assess_verdict"], f"{verdict_text} ({verdict})" if verdict else None),
        (labels["assess_source"], assessment.get("source")),
        (labels["assess_schema"], assessment.get("schema_version")),
        (labels["assess_strict"], assessment.get("strict")),
    ]
    parts.append(_kv_table(pairs, labels))

    thresholds = assessment.get("thresholds")
    if isinstance(thresholds, dict) and thresholds:
        parts.append(
            f"<h3>{_esc(labels['assess_thresholds'])}</h3>"
            + _kv_table([(str(key), thresholds[key]) for key in sorted(thresholds)], labels)
        )
    else:
        parts.append(
            f"<h3>{_esc(labels['assess_thresholds'])}</h3>"
            f'<p class="note">{_esc(labels["not_reported"])}</p>'
        )

    chains = assessment.get("chains") or []
    if chains:
        chain_rows: List[Tuple[str, str, str, str, str, str, str, str]] = []
        for chain in chains:
            chain_rows.append(
                (
                    f"<td>{_esc(_value_or(chain.get('seed'), labels))}</td>",
                    f"<td>{_esc(_value_or(chain.get('status'), labels))}</td>",
                    f"<td class=\"num\">{_value_or(chain.get('samples'), labels)}</td>",
                    f"<td class=\"num\">{_value_or(chain.get('burnin_samples'), labels)}</td>",
                    f"<td class=\"num\">{_value_or(chain.get('n_parameters'), labels)}</td>",
                    f"<td class=\"num\">{_number_or(chain.get('lowest_ess'), labels, '.1f')}</td>",
                    f"<td class=\"num\">{_value_or(chain.get('n_operators'), labels)}</td>",
                    f"<td>{_esc(_value_or(chain.get('incomplete_reason'), labels))}</td>",
                )
            )
        body = "".join(f"<tr>{''.join(row)}</tr>" for row in chain_rows)
        parts.append(
            f"<h3>{_esc(labels['assess_chains'])}</h3>"
            "<table><thead><tr>"
            f"<th>{_esc(labels['col_seed'])}</th>"
            f"<th>{_esc(labels['assess_status'])}</th>"
            f"<th>{_esc(labels['col_samples'])}</th>"
            f"<th>{_esc(labels['assess_burnin'])}</th>"
            f"<th>{_esc(labels['assess_parameters'])}</th>"
            f"<th>{_esc(labels['col_ess'])}</th>"
            f"<th>{_esc(labels['assess_operators'])}</th>"
            f"<th>{_esc(labels['assess_incomplete'])}</th>"
            "</tr></thead><tbody>"
            f"{body}</tbody></table>"
        )

    multichain = assessment.get("multichain")
    parts.append(f"<h3>{_esc(labels['assess_multichain'])}</h3>")
    if isinstance(multichain, dict):
        psrf = multichain.get("psrf") if isinstance(multichain.get("psrf"), dict) else {}
        asdsf = multichain.get("asdsf") if isinstance(multichain.get("asdsf"), dict) else {}
        psrf_status = (psrf or {}).get("status") or labels["not_reported"]
        asdsf_status = (asdsf or {}).get("status") or labels["not_reported"]
        asdsf_value = (asdsf or {}).get("value")
        mc_rows = [
            (
                f"<td>{_esc(labels['assess_psrf'])}</td>",
                f"<td>{_esc(_value_or(psrf_status, labels))}</td>",
                f"<td>{_esc(_value_or((psrf or {}).get('reason'), labels))}</td>",
            ),
            (
                f"<td>{_esc(labels['assess_asdsf'])}</td>",
                f"<td>{_esc(_value_or(asdsf_status, labels))}</td>",
                f"<td>{_esc(_value_or((asdsf or {}).get('reason'), labels))}"
                + (
                    ""
                    if _number(asdsf_value) is None
                    else f" [{_esc(format(_number(asdsf_value), '.5g'))}]"
                )
                + "</td>",
            ),
            (
                f"<td>{_esc(labels['col_samples'])}</td>",
                f"<td>{_esc(_value_or(multichain.get('n_chains'), labels))}</td>",
                f"<td>{_esc(_value_or(multichain.get('available'), labels))}</td>",
            ),
        ]
        body = "".join(f"<tr>{''.join(row)}</tr>" for row in mc_rows)
        parts.append(
            "<table><thead><tr>"
            f"<th>{_esc(labels['col_field'])}</th>"
            f"<th>{_esc(labels['col_status'])}</th>"
            f"<th>{_esc(labels['col_basis'])}</th>"
            "</tr></thead><tbody>"
            f"{body}</tbody></table>"
        )
    else:
        parts.append(f'<p class="note">{_esc(labels["not_reported"])}</p>')

    reasons = assessment.get("reasons") or []
    parts.append(f"<h3>{_esc(labels['assess_reasons'])}</h3>")
    if reasons:
        reason_rows: List[Tuple[str, str, str, str]] = [
            (
                f"<td>{_esc(_value_or(reason.get('id'), labels))}</td>",
                f"<td>{_esc(_value_or(reason.get('severity'), labels))}</td>",
                f"<td>{_esc(_value_or(reason.get('message'), labels))}</td>",
                f"<td>{_esc(_value_or(reason.get('fix'), labels))}</td>",
            )
            for reason in reasons
        ]
        body = "".join(f"<tr>{''.join(row)}</tr>" for row in reason_rows)
        parts.append(
            "<table><thead><tr>"
            f"<th>id</th><th>{_esc(labels['col_severity'])}</th>"
            f"<th>{_esc(labels['col_message'])}</th>"
            f"<th>{_esc(labels['col_fix'])}</th>"
            "</tr></thead><tbody>"
            f"{body}</tbody></table>"
        )
        parts.append(f'<p class="small">{_esc(labels["raw_note"])}</p>')
    else:
        parts.append(f'<p class="note">{_esc(labels["none"])}</p>')

    limitations = assessment.get("limitations") or []
    parts.append(f"<h3>{_esc(labels['assess_limitations'])}</h3>")
    if limitations:
        items = "".join(f"<li>{_esc(item)}</li>" for item in limitations)
        parts.append(f'<ul class="tight">{items}</ul>')
        parts.append(f'<p class="small">{_esc(labels["raw_note"])}</p>')
    else:
        parts.append(f'<p class="note">{_esc(labels["none"])}</p>')
    return "".join(parts)


def _render_manifest(data: Dict[str, Any], labels: Dict[str, str]) -> str:
    manifest = data["manifest"]
    parts = [f'<p class="note">{_esc(labels["manifest_caption"])}</p>']
    rows = [
        (
            f"<td><code>{_esc(item['path'])}</code></td>",
            f"<td>{_esc(item['kind'])}</td>",
            f"<td class=\"num\">{_value_or(item['bytes'], labels)}</td>",
            f"<td><code>{_esc(_value_or(item['sha256'], labels))}</code></td>",
        )
        for item in manifest["files"]
    ]
    body = "".join(f"<tr>{''.join(row)}</tr>" for row in rows)
    parts.append(
        "<table><thead><tr>"
        f"<th>{_esc(labels['col_path'])}</th>"
        f"<th>{_esc(labels['col_kind'])}</th>"
        f"<th>{_esc(labels['col_bytes'])}</th>"
        f"<th>{_esc(labels['col_sha256'])}</th>"
        "</tr></thead><tbody>"
        f"{body}</tbody></table>"
    )
    parts.append(
        f'<p class="small">{_esc(labels["manifest_total"])}: '
        f"{_esc(manifest['listed'])} {_esc(labels['manifest_files'])}, "
        f"{_esc(manifest['total_bytes'])} bytes</p>"
    )
    if manifest.get("truncated"):
        parts.append(
            f'<p class="small">{_esc(labels["manifest_truncated"])}</p>'.replace(
                "{limit}", _esc(str(MANIFEST_FILE_LIMIT))
            ).replace("{extra}", _esc(str(manifest["found"] - manifest["listed"])))
        )
    if manifest.get("excluded"):
        parts.append(f'<p class="small">{_esc(labels["manifest_excluded"])}</p>')
    return "".join(parts)


def _render_config(data: Dict[str, Any], labels: Dict[str, str]) -> str:
    config = data["config"]
    if not config.get("requested"):
        return ""
    if not config.get("source"):
        return f'<p class="note">{_esc(labels["config_absent"])}</p>'
    kind_label = (
        labels["config_kind_yaml"] if config.get("kind") == "yaml" else labels["config_kind_xml"]
    )
    pairs = [
        (labels["config_source"], config.get("source")),
        (labels["col_kind"], kind_label),
        (labels["col_bytes"], config.get("bytes")),
        (labels["col_sha256"], config.get("sha256")),
    ]
    parts = [
        f'<p class="note">{_esc(labels["config_caption"])}</p>',
        _kv_table(pairs, labels),
    ]
    if config.get("truncated"):
        text = labels["config_truncated"].format(limit=CONFIG_TEXT_LIMIT, total=config.get("bytes"))
        parts.append(f'<p class="small">{_esc(text)}</p>')
    parts.append(f"<pre>{_esc(config.get('text'))}</pre>")
    return "".join(parts)


def _render_limitations(labels: Dict[str, str], lang: str) -> str:
    items = "".join(f"<li>{_esc(item)}</li>" for item in LIMITATIONS[lang])
    return f'<ul class="tight">{items}</ul>'


def render_html(data: Dict[str, Any], lang: Optional[str] = None) -> str:
    """Render the collected data as one self-contained HTML document."""
    language = lang or data.get("lang") or LANG_EN
    labels = _labels(language)
    html_lang = "zh" if language == LANG_ZH else "en"
    anchors = [
        ("conclusion", labels["sec_conclusion"]),
        ("provenance", labels["sec_provenance"]),
        ("gates", labels["sec_gates"]),
        ("auto-priors", labels["sec_auto_priors"]),
    ]
    anchors += [
        ("diagnostics", labels["sec_diagnostics"]),
        ("run-metadata", labels["sec_run_metadata"]),
        ("trace", labels["sec_trace"]),
        ("assess", labels["sec_assess"]),
        ("manifest", labels["sec_manifest"]),
    ]
    if data["config"].get("requested"):
        anchors.append(("config", labels["sec_config"]))
    anchors.append(("limitations", labels["sec_limitations"]))

    nav_items = "".join(
        f'<li><a href="#{_esc(anchor)}">{_esc(title)}</a></li>' for anchor, title in anchors
    )
    sections = [
        _section("conclusion", labels["sec_conclusion"], _render_conclusion(data, labels)),
        _section("provenance", labels["sec_provenance"], _render_provenance(data, labels)),
        _section("gates", labels["sec_gates"], _render_gates(data, labels)),
        _section("diagnostics", labels["sec_diagnostics"], _render_diagnostics(data, labels)),
        _section("run-metadata", labels["sec_run_metadata"], _render_run_metadata(data, labels)),
        _section("trace", labels["sec_trace"], _render_trace(data, labels)),
        _section("assess", labels["sec_assess"], _render_assessment(data, labels)),
        _section("manifest", labels["sec_manifest"], _render_manifest(data, labels)),
    ]
    if data.get("auto_priors") is not None:
        sections.insert(
            3,
            _section(
                "auto-priors",
                labels["sec_auto_priors"],
                _render_automatic_priors(data, labels),
            ),
        )
    if data["config"].get("requested"):
        sections.append(_section("config", labels["sec_config"], _render_config(data, labels)))
    sections.append(
        _section("limitations", labels["sec_limitations"], _render_limitations(labels, language))
    )

    header = (
        '<header class="page">'
        f"<h1>{_esc(labels['title'])}</h1>"
        f"<p>{_esc(labels['subtitle'])}</p>"
        f"<p>{_esc(labels['generated_at'])}: {_esc(C.now_iso())}</p>"
        f"<p>{_esc(labels['language'])}: {_esc(language)} &middot; "
        f"{_esc(labels['tool_version'])}: {_esc(C.tool_version())} &middot; "
        f"{_esc(labels['report_schema'])}: {_esc(C.SCHEMA_REPORT)}</p>"
        f"<p>{_esc(labels['col_path'])}: <code>{_esc(data['workdir'])}</code></p>"
        f"<p>{_esc(labels['self_contained'])}</p>"
        "</header>"
    )
    footer = (
        '<footer class="page">'
        f"{_esc(labels['footer'].format(version=C.tool_version()))}"
        "</footer>"
    )
    return (
        "<!DOCTYPE html>\n"
        f'<html lang="{_esc(html_lang)}">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="generator" content="BeastXPy {_esc(C.tool_version())} report/v1">\n'
        f"<title>{_esc(labels['title'])}</title>\n"
        f"<style>\n{REPORT_CSS}\n</style>\n"
        "</head>\n"
        "<body>\n"
        f"{header}\n"
        "<main>\n"
        f'<nav class="toc"><strong>{_esc(labels["nav"])}</strong>'
        f"<ol>{nav_items}</ol></nav>\n" + "\n".join(sections) + "\n</main>\n"
        f"{footer}\n"
        "</body>\n"
        "</html>\n"
    )


def build_report(
    workdir: Any,
    output: Any,
    *,
    lang: str = LANG_EN,
    include_config: bool = False,
    assessment_path: Optional[Any] = None,
    ess_threshold: float = 200.0,
    with_plot: bool = True,
    json_path: Optional[Any] = None,
) -> Tuple[Path, Dict[str, Any]]:
    """Collect, render and write the report; return ``(html_path, data)``.

    Raises:
        C.ContractError: When the workdir is missing/unreadable (the CLI maps
            that to exit 2) or when an explicit ``--assessment`` path is
            unusable (exit 1).
        OSError: Propagated for a failed write; the CLI maps it to exit 1.
    """
    target = Path(output).expanduser()
    data = collect_report_data(
        workdir,
        lang=lang,
        include_config=include_config,
        assessment_path=assessment_path,
        ess_threshold=ess_threshold,
        with_plot=with_plot,
        output_path=target,
        json_path=json_path,
    )
    document = render_html(data, lang)
    data["output_html"] = C.display_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(target.name + ".part")
    # Write via pathlib with an explicit newline so it is pinned across platforms.
    tmp_path.write_text(document, encoding="utf-8", newline="\n")
    tmp_path.replace(target)
    if json_path:
        C.write_json(json_path, to_envelope(data))
    return target, data


__all__ = [
    "ASSESSMENT_CANDIDATES",
    "CONFIG_TEXT_LIMIT",
    "LABELS_EN",
    "LABELS_ZH",
    "LANG_EN",
    "LANG_ZH",
    "LANGUAGES",
    "LIMITATIONS_EN",
    "LIMITATIONS_ZH",
    "MANIFEST_FILE_LIMIT",
    "analyse_trace",
    "build_conclusion",
    "build_report",
    "collect_config",
    "collect_gates",
    "collect_manifest",
    "collect_provenance",
    "collect_report_data",
    "discover_chains",
    "find_assessment",
    "render_html",
    "resolve_workdir",
    "summarise_assessment",
    "to_envelope",
]
