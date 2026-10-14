"""The complete template runs in JW's tool, rather than a later manual report."""

import base64
import csv
import html
import json
import shutil
from datetime import datetime

import numpy as np

from .assets import child, sha
from .engine import inventory
from .native_data import write
from .report import history_summary, local_time
from .report_figures import render_figures
from .report_reference import csv_rows, load_reference
from .workflow import verify_selected_asset


def validate_metrics(summary, reference):
    frozen = reference["summary"]
    if (
        frozen["final_fit_rows"] != summary["final_fit"]
        or sum(frozen["primary_split_counts"].values()) != summary["total_eligible"]
    ):
        raise ValueError("Frozen report stage counts differ")
    for target, calculated in summary["targets"].items():
        metric = next(
            r
            for r in reference["metrics"]
            if r["target"] == target and r["model_id"] == "selected_calibrated"
        )
        for name, key in (
            ("Accuracy", "accuracy"),
            ("Brier", "brier"),
            ("TSS", "tss"),
            ("tp", "tp"),
            ("fp", "fp"),
            ("tn", "tn"),
            ("fn", "fn"),
        ):
            if not np.isclose(float(metric[name]), calculated[key], rtol=0, atol=1e-12):
                raise ValueError("Frozen report metric differs: " + name)
        calculated["auc"] = float(metric["ROC_AUC"])
        calculated["precision"] = float(metric["precision"])
        calculated["average_precision"] = float(metric["average_precision"])


def dataset_counts(assets):
    def count(path):
        with path.open(encoding="utf-8-sig", newline="") as f:
            return sum(1 for _ in csv.DictReader(f))

    return {
        "calendar_candidates": (datetime(2026, 1, 1) - datetime(2011, 1, 1)).days,
        "GOES_catalog_records": sum(
            count(verify_selected_asset(assets, r["path"]))
            for r in inventory()["files"]
            if r["role"] == "science_event_annual"
        ),
    }


def build_full_report(forecast, assets, output, live_dir=None):
    from markdown_it import MarkdownIt

    summary, rows, predictions = history_summary(assets)
    reference = load_reference(assets, rows, predictions)
    validate_metrics(summary, reference)
    if (
        forecast["model_origin_checkpoint_sha256"]
        != reference["inventory"]["origin_checkpoint_sha256"]
    ):
        raise ValueError("Live forecast and history use different model identities")
    p = forecast["probabilities"]
    if set(p) != {"C1.0+", "M1.0+"} or not 0 <= p["M1.0+"] <= p["C1.0+"] <= 1:
        raise ValueError("Invalid nested forecast probabilities")
    summary["dataset_counts"] = dataset_counts(assets)
    context = {}
    if live_dir is not None:
        if sha(live_dir / "source_manifest.json") != forecast["live_manifest_sha256"]:
            raise ValueError("Live source manifest changed before reporting")
        qualification = json.loads(
            (live_dir / "live_qualification.json").read_text("utf-8")
        )
        if (
            sha(live_dir / "live_physical.csv")
            != qualification["live_physical_csv_sha256"]
        ):
            raise ValueError("Live report feature table changed")
        live_rows = csv_rows(live_dir / "live_physical.csv")
        if (
            len(live_rows) != 1
            or int(live_rows[0]["patch_count"]) != qualification["central_patch_count"]
        ):
            raise ValueError("Live report patch count differs")
        context = {
            "central_patch_count": qualification["central_patch_count"],
            "processing_utc": live_rows[0]["hmi_latest_processing_utc"],
            "support_end_utc": live_rows[0]["hmi_latest_support_end_utc"],
            "retrieved_utc": live_rows[0]["source_retrieved_utc"],
            "version_literal_values": qualification["version_literal_values"],
        }
    output.mkdir(parents=True, exist_ok=False)
    evidence = output / "historical_evidence"
    evidence.mkdir()
    for row in reference["inventory"]["files"]:
        shutil.copyfile(
            child(assets, "report_reference/" + row["path"]), evidence / row["path"]
        )
        if sha(evidence / row["path"]) != row["sha256"]:
            raise ValueError("Historical evidence copy differs")
    write(output / "historical_metrics.json", summary)
    figures = render_figures(forecast, rows, predictions, reference, output)
    start, end = (
        forecast["prediction_window"]["start"],
        forecast["prediction_window"]["end"],
    )
    c, m = summary["targets"]["C1.0+"], summary["targets"]["M1.0+"]
    sections = []

    def section(identity, title, text):
        sections.append({"id": identity, "title": title, "text": text})

    def image(stem, caption):
        return f"\n\n![{caption}]({stem}.png)\n\n{caption}"

    input_note = ""
    if context:
        input_note = f"\n最新合格中央patch：{context['central_patch_count']}个；处理UTC：{context['processing_utc']}；输入支持结束UTC：{context['support_end_utc']}；检索UTC：{context['retrieved_utc']}。"

    section(
        "current",
        "本次未来24小时预报",
        "\n".join(
            [
                f"北京时间半开窗口：**{local_time(start)} 至 {local_time(end)}**，起点包含、终点排除。",
                f"\nUTC：{start} 至 {end}。起点为真实签发时刻，保留微秒，不回填为发送消息时刻。",
                "\n| 目标 | 整个24小时至少一次新起始事件的概率 | 固定0.5阈值 |\n| --- | ---: | --- |",
                *[
                    f"| {t} | {p[t]:.2%} | {'达到阳性阈值' if p[t] >= 0.5 else '未达阳性阈值'} |"
                    for t in ("C1.0+", "M1.0+")
                ],
                "\nM1.0+包含X级；C1.0+包含M/X级。概率不是正确率，未达阈值不表示不会发生。目标为全太阳新起始事件，不给地点、精确时刻、逐时强度或事件次数。",
                f"\n输入观测UTC：{forecast['input_observation_utc']}；最旧输入年龄 **{forecast['input_age_seconds'] / 60:.1f}分钟**（门槛180分钟），23项中央SHARP标量。",
                input_note,
                f"\n本次原生Data裁决：{forecast.get('native_Data_decision', '见当前任务原生回执')}。审阅绑定本次新输入，不复用旧接受。",
                image(
                    "current", "图1：本次真实签发窗口的实验概率；未来观测真值仍未知。"
                ),
            ]
        ),
    )

    table = [
        "| 目标 | 同日期模型/基线 | Accuracy | Brier（低更好） | TSS（高更好） |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    names = {
        "selected_calibrated": "磁场RF",
        "train_climatology": "训练气候",
        "always_yes": "恒报有",
        "always_no": "恒报无",
    }
    for target in ("C1.0+", "M1.0+"):
        for model, name in names.items():
            r = next(
                r
                for r in reference["metrics"]
                if r["target"] == target and r["model_id"] == model
            )
            table.append(
                f"| {target} | {name} | {float(r['Accuracy']):.2%} | {float(r['Brier']):.6f} | {float(r['TSS']):.4f} |"
            )

    def interval(target):
        r = next(
            r
            for r in reference["intervals"]
            if r["target"] == target
            and r["model_id"] == "selected_calibrated"
            and r["metric"] == "TSS"
            and r["cohort"] == "primary"
        )
        return f"[{float(r['ci_lower']):.4f}, {float(r['ci_upper']):.4f}]"

    climate = next(
        r
        for r in reference["metrics"]
        if r["target"] == "M1.0+" and r["model_id"] == "train_climatology"
    )
    gain = 1 - m["brier"] / float(climate["Brier"])
    section(
        "performance",
        "性能结论与同日期基线",
        f"全部 **{len(rows)}个** 时间留出测试窗口；同一固定0.5阈值，没有按测试成绩重选模型。\n\n**C：Accuracy {c['accuracy']:.2%}，低于恒报有 {c['always_yes_accuracy']:.2%}；Brier {c['brier']:.6f}，差于恒报有 {c['always_yes_brier']:.6f}，历史技能未获支持。**\n\n**M：Accuracy {m['accuracy']:.2%}、Brier {m['brier']:.6f}、TSS {m['tss']:.4f}、AUC {m['auc']:.4f}；仅有受限历史技能支持。**\n\n"
        + "\n".join(table)
        + f"\n\nM相对训练气候Brier降低{gain:.2%}，仅针对这条基线。M TSS的95%月份块区间为{interval('M1.0+')}；C为{interval('C1.0+')}、包含0。这是冻结历史抽样区间，不是本次预报的置信区间。"
        + image(
            "baselines",
            "图2：同一841窗口的RF、训练气候、恒报有/无比较。尚无成熟同类模型同队列比较，也无有效历史Logistic比较。",
        ),
    )

    year_counts = {
        year: sum(r["target_start_utc"].startswith(str(year)) for r in rows)
        for year in (2023, 2024, 2025)
    }
    section(
        "samples",
        "全部测试样本与历史曲线",
        f"每个散点是一个已结束的历史24小时窗口。C阳性/阴性 **{c['positive']}/{c['negative']}**，M **{m['positive']}/{m['negative']}**；2023/2024/2025分别 **{year_counts[2023]}/{year_counts[2024]}/{year_counts[2025]}** 窗，合计{len(rows)}。不挑有利日期，缺失不补零，窗口可能相关，不能称独立耀斑事件数。"
        + image(
            "all_samples",
            "图3：全部841窗口，每个目标各841个散点，颜色与标记区分阳性/阴性。",
        )
        + image(
            "history",
            "图4：全部历史概率曲线与二元观测标签；超过48小时的缺口断线。这两张历史图均不是未来24小时逐时走势。",
        ),
    )

    section(
        "errors",
        "检出、漏报与误报",
        f"固定0.5阈值：M检出 **{m['tp']}/{m['positive']}**，漏报 **{m['fn']}/{m['positive']}**，召回率 **{m['recall']:.2%}**；{m['tp'] + m['fp']}个阳性判决中{m['tp']}个正确，精确率{m['precision']:.2%}。阴性判决不能排除事件。\n\nC只有{c['negative']}个阴性窗口，其中{c['fp']}个误报；高Accuracy不足以证明稳定预测优势。"
        + image("confusion", "图5：同一841窗口混淆矩阵；格内为窗口计数与本类比例。"),
    )
    section(
        "calibration",
        "概率校准与分布",
        "固定10个概率箱，每箱标明n，空箱不画；全部841样本各归入一个箱。可靠性点是分箱统计，不是一点一个样本；C仅10个负例，小分箱不稳定。分布图按已知标签分别显示概率。"
        + image(
            "reliability",
            "图6：全队列可靠性与概率分布。没有重新校准，不给单次预报置信区间或业务校准保证。",
        ),
    )

    table = [
        "| 年度 | n | 目标 | RF Accuracy | 恒报有Accuracy | RF Brier |",
        "| --- | ---: | --- | ---: | ---: | ---: |",
    ]
    for year in (2023, 2024, 2025):
        for target in ("C1.0+", "M1.0+"):
            group = [
                r
                for r in reference["years"]
                if r["cohort"] == "primary"
                and r["target"] == target
                and int(r["year"]) == year
            ]
            rf = next(r for r in group if r["model_id"] == "selected_calibrated")
            yes = next(r for r in group if r["model_id"] == "always_yes")
            table.append(
                f"| {year} | {year_counts[year]} | {target} | {float(rf['Accuracy']):.2%} | {float(yes['Accuracy']):.2%} | {float(rf['Brier']):.6f} |"
            )
    climate_classes = reference["calibration"][
        "baseline_climatology_class_probabilities"
    ]
    section(
        "years",
        "跨年表现与分布差异",
        "\n".join(table)
        + f"\n\n三个测试年度全部列出。最终拟合队列C/M阳性比例{1 - climate_classes[0]:.1%}/{climate_classes[2]:.1%}，测试为{c['positive'] / len(rows):.1%}/{m['positive'] / len(rows):.1%}；队列/周期分布差异明显，训练气候是较弱的跨时期参考。"
        + image("years", "图7：逐年RF与恒报有；M在2024年Accuracy仍低于恒报有。"),
    )
    section(
        "importance",
        "模型依赖的磁场参数",
        "23项SHARP标量：11种物理量各取中央patch的max/median，另加patch_count。C/M分别显示冻结排序前8项，完整23项在historical_evidence/importance.csv。"
        + image(
            "importance",
            "图8：冻结置换重要性，误差条为10次置换SD，不是置信区间。未重新置换、筛选特征或调模型；重要性不是因果或新物理机制。",
        ),
    )

    counts, common = (
        reference["summary"]["primary_split_counts"],
        reference["summary"]["common_split_counts"],
    )
    days = (datetime(2026, 1, 1) - datetime(2023, 1, 1)).days
    dataset = summary["dataset_counts"]
    section(
        "data",
        "数据规模、标签与资格范围",
        "\n".join(
            [
                "| 阶段/来源 | 数量 | 用途 |",
                "| --- | ---: | --- |",
                f"| 官方GOES事件目录 | {dataset['GOES_catalog_records']:,}条记录 | 构造标签；不是训练样本数 |",
                f"| 历史日历候选 | {dataset['calendar_candidates']:,}日 | {summary['total_eligible']:,}个合格窗口；缺失不补零 |",
                f"| 2011—2020拟合（实际始于{reference['summary']['first_qualified_training_reference'][:10]}） | {counts['train']}窗 | 候选模型拟合 |",
                f"| 2021选型 | {counts['selection']}窗 | 与训练合并后最终拟合{summary['final_fit']}窗 |",
                f"| 2022校准 | {counts['calibration']}窗 | 温度校准，不用于估计器拟合 |",
                f"| 2023—2025测试 | {counts['test']}窗/{days}日 | 合格日期覆盖{counts['test'] / days:.2%}；其余不算预测成功 |",
                f"| 历史基线共同子集 | {sum(common.values())}总窗/{common['test']}测试窗 | 训练缺M类、校准不足；Logistic未有效拟合 |",
                "\n每组历史输入预测自己的未来24小时：从max(中央patch的T_OBS+675秒、DATE)+1秒模拟可用时刻起算，DATE不证明历史首次发布；实时从真实签发起算。按事件起始落在[start,end)，以最终峰值≥10⁻⁶/10⁻⁵ W/m²标C+/M+。",
                "\n已知起始与峰值但缺结束可用于未来标签；历史完成时间不明仍unknown。负标签需至少99%分钟覆盖、最大连续缺口≤300秒。输入、标签或缺失未合格不能补零；中央SHARP输入与全太阳标签有空间差异。",
            ]
        ),
    )
    parameters = reference["selected_config"]["locked_configuration_signature"][
        "parameters"
    ]
    temperature = reference["calibration"]["selected"]["temperature"]
    section(
        "limits",
        "贡献、用途与证据边界",
        f"**已完成的改进：** 官方磁图派生标量输入、按产品可用时间构造24小时目标、时间划分和概率校准、合格窗口回测、真实前瞻签发，以及保留工具调用/角色审阅/原件哈希的自动报告交付。\n\n**结论与用途：** M在当前历史队列内有受限判别与概率表现，C未优于恒报有；可用于研究验证和后续公平比较。尚未证明优于成熟同类模型、磁场相对历史输入的净增益、稳定业务预警效果或新因果机制。\n\n模型：RF{parameters['n_estimators']}树、leaf{parameters['min_samples_leaf']}/depth{parameters['max_depth']}、seed={parameters['random_state']}，阈值0.5，2022温度{temperature:.6f}。本次未训练、调参、再校准或读取未来真值。\n\n来源：hmi.sharp_cea_720s_nrt/JSOC关键词产品、GOES xrsf-l2-flrpt_science目录与分钟覆盖。完整QUALITY=1024仍是受限警告；版本信息、检索时间及原响应在本任务work目录，JSOC/HMI使用许可未核定。\n\n2023—2025是时间留出，部分数据此前在其他目标任务出现过，不能称外部盲测；历史固定13TAI与实时任意时刻存在差异。scientific_status=not_reviewed、formal_ResearchReviewStore_chain=false、operational_forecast_validated=false保持。\n\n**生成归属：** JW主Agent实际调用forecast_flare_24h工具自动计算、绘图和排版；原生Data只审阅本次新输入。解释为系统依据数值填充的报告模板，不冒充Data原话或新独立性能审查；JW模型未做图像目检。未来真值未知，须窗口结束且官方目录更新后核验；未安排自动核验或重发。",
    )
    section(
        "provenance",
        "原始结果与来源索引",
        "\n".join(
            [
                f"- 原模型checkpoint SHA256：`{forecast['model_origin_checkpoint_sha256']}`",
                f"- 本次实时manifest SHA256：`{forecast['live_manifest_sha256']}`",
                f"- 本次Data审阅SHA256：`{forecast['native_Data_review_sha256']}`",
                f"- 历史主队列SHA256：`{summary['dataset_sha256']}`",
                "- 原始签发：issued_forecast.json；完整图表与历史来源索引：report_manifest.json。",
                "- historical_evidence/保留九份逐字节核验的冻结原件。实际主Agent工具call ID、Data原话、typed消息和witness在任务work/flare_forecast目录。",
                "- 本次来源版本字段："
                + (
                    json.dumps(
                        context.get("version_literal_values"), ensure_ascii=False
                    )
                    if context
                    else "见当前任务原始来源响应"
                ),
            ]
        ),
    )
    title = "未来24小时太阳耀斑实验预报 · 全样本性能报告"
    text = (
        "# "
        + title
        + "\n\n"
        + "\n\n".join(f"## {s['title']}\n\n{s['text']}" for s in sections)
        + "\n"
    )
    (output / "report.md").write_text(text, encoding="utf-8")
    renderer = MarkdownIt("commonmark").enable("table")
    embeds = {
        r["id"]: base64.b64encode((output / (r["id"] + ".png")).read_bytes()).decode(
            "ascii"
        )
        for r in figures
    }
    bodies = []
    for s in sections:
        body = renderer.render(s["text"])
        for stem, encoded in embeds.items():
            body = body.replace(
                f'src="{stem}.png"', f'src="data:image/png;base64,{encoded}"'
            )
        bodies.append(
            f'<section id="{s["id"]}"><h2>{html.escape(s["title"])}</h2>{body}</section>'
        )
    navigation = " ".join(
        f'<a href="#{s["id"]}">{html.escape(s["title"])}</a>' for s in sections
    )
    style = "body{font-family:system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 24px;line-height:1.75;color:#162b40;background:#f2f6f8}header,section{background:white;border-radius:12px;padding:24px;margin:20px 0}header{border-top:5px solid #087f8c}h1,h2{color:#163c52;line-height:1.4}nav{display:flex;flex-wrap:wrap;gap:8px;font-size:13px}nav a{background:#edf4f5;padding:5px 10px;color:#086b75;border-radius:6px;text-decoration:none}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border-bottom:1px solid #cddae0;padding:10px;text-align:left}th{background:#edf4f5}img{width:100%;height:auto}code{overflow-wrap:anywhere;white-space:normal}p,li{overflow-wrap:anywhere}@media(max-width:600px){body{padding:0 10px}header,section{padding:14px}table{font-size:11px}th,td{padding:5px}h1{font-size:24px}}@media print{body{margin:0;background:white;max-width:none}header,section{padding:12px;border-radius:0}section{break-before:page}h2{break-after:avoid}img,table{break-inside:avoid}nav{display:none}}"
    page = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>'
        + title
        + "</title><style>"
        + style
        + "</style></head><body><header><h1>"
        + title
        + "</h1><p>当前概率、历史依据、样本规模与证据边界</p><nav>"
        + navigation
        + "</nav></header>"
        + "".join(bodies)
        + "</body></html>"
    )
    (output / "report.html").write_text(page, encoding="utf-8")
    write(
        output / "report_manifest.json",
        {
            "schema_version": "jw-flare-full-report-v1",
            "renderer": "native forecast_flare_24h tool",
            "historical_n": len(rows),
            "no_fit_or_recalibration": True,
            "future_truth_read": False,
            "JW_image_inspection": False,
            "figures": figures,
            "historical_sources": reference["inventory"],
            "live_input_context": context,
            "sections": [{"id": s["id"], "title": s["title"]} for s in sections],
            "report_html_sha256": sha(output / "report.html"),
            "report_md_sha256": sha(output / "report.md"),
        },
    )
    return text
