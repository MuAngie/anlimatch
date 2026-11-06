"""Review narrative edits without allowing changes to products, doses or quotes."""
from copy import deepcopy

from engine import markdown
from evaluator import PROMPT, reviewed_output
from model_client import ModelClient


def edit_report(case, catalog, original, changes, reviewer=None):
    required = {"summary", "lifestyle", "follow_up", "rationales"}
    if not isinstance(changes, dict) or set(changes) != required:
        raise ValueError("只能修改报告摘要、搭配依据和建议文字")
    if not isinstance(changes["summary"], str) or not changes["summary"].strip():
        raise ValueError("请填写报告分析摘要")
    for key in ("lifestyle", "follow_up"):
        if not isinstance(changes[key], list) or any(not isinstance(s, str) or not s.strip() for s in changes[key]):
            raise ValueError("建议须按条填写")
    rationales = changes["rationales"]
    if (not isinstance(rationales, dict) or set(rationales) != {p["id"] for p in original["selected_products"]}
            or any(not isinstance(s, str) or not s.strip() for s in rationales.values())):
        raise ValueError("请填写每个已选产品的搭配依据")
    report = deepcopy(original)
    report["analysis"].update(summary=changes["summary"].strip(), original_summary=case["text"],
                              lifestyle=changes["lifestyle"], follow_up=changes["follow_up"])
    for product in report["selected_products"]:
        product["rationale"] = rationales[product["id"]].strip()
    source, model = "测试复核", "未调用"
    if reviewer is None:
        client = ModelClient()
        reviewer = lambda data: client.call(PROMPT, data, "eval_schema.json")
        source, model = client.source, client.model
    changed = [key for key in ('summary','lifestyle','follow_up') if report['analysis'].get(key) != original['analysis'].get(key)]
    changed += ['rationale:'+p['id'] for p in report['selected_products']
                if p['rationale'] != next(q['rationale'] for q in original['selected_products'] if q['id']==p['id'])]
    output = reviewed_output(case, catalog, report, reviewer=reviewer, source=source, model_name=model,
                             narrative_edit={'changed_fields':changed,'fixed_fields':['products','daily_units','pricing','days']})
    if output["report"]:
        report["edited_at"] = output["evaluation"]["evaluated_at"]
        report["method"] = "报告文字经人工修改；产品、每日用量和报价保留原计算结果；修改后由大模型独立复核。提醒随报告保留，组合仍为待专业复核草案。"
        output["markdown"] = markdown(report)
    return output
