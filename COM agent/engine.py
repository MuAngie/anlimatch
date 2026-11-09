"""Local health-product workflow. Clinical conclusions must be supplied, not inferred."""
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
from io import BytesIO
import json
from pathlib import Path
import re
import warnings
from copy import deepcopy

TODAY = lambda: date.today().isoformat()
GOALS = {
    "蛋白质补充": ["蛋白质補充", "蛋白质补充", "补充蛋白质", "蛋白质摄入不足"],
    "补充维生素B6": ["补充维生素B6", "维生素B6补充", "维生素B6摄入不足"],
    "补充B族维生素": ["补充B族维生素", "B族维生素补充", "B族维生素摄入不足"],
    "补钙": ["补钙", "钙摄入不足", "钙补充"],
    "膳食纤维补充": ["膳食纤维补充", "膳食纤维摄入不足", "补充膳食纤维"],
    "补铁与叶酸": ["补铁与叶酸", "补充铁与叶酸"],
    "血脂管理": ["血脂管理", "辅助降血脂"],
}
MASS = {"g": Decimal(1000), "mg": Decimal(1), "µg": Decimal("0.001"), "ug": Decimal("0.001"), "mcg": Decimal("0.001")}


def number(value, field, minimum=0):
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field}必须填写数字") from None
    if not n.is_finite() or n < minimum:
        raise ValueError(f"{field}必须大于等于{minimum}")
    return n


def money(n):
    return float(n.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def require_date(value, field):
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError(f"{field}须为YYYY-MM-DD日期") from None


def fill_archive_fields(product, archive_date=None):
    """Copy source-supported archive fields without approving historical labels."""
    raw = product["raw"]
    if "product_id" not in raw and "当前公开规格" not in raw:
        return
    fields = {
        "产品分类": raw.get("法律分类") or raw.get("product_category"),
        "产品说明": raw.get("product_description"),
        "主要方向 / 功能": raw.get("保健功能/营养目的") or raw.get("primary_directions"),
        "辅助方向": raw.get("supportive_directions"),
        "规格": raw.get("当前公开规格"),
        "建议食用量原文": raw.get("建议食用量"),
        "主要成分": raw.get("关键成分摘要") or raw.get("ingredients_raw"),
        "适宜人群": raw.get("适宜人群") or raw.get("applicable_population"),
        "不适宜人群": raw.get("不适宜人群"),
        "注意事项": raw.get("warnings") or raw.get("备注"),
        "批准 / 备案号": raw.get("批准/备案号"),
        "核验状态": raw.get("法规核验状态"),
        "数据质量等级": raw.get("数据质量等级"),
        "资料日期": raw.get("抓取日期") or archive_date,
        "资料来源": raw.get("官方产品/价格来源") or raw.get("source_url"),
        "辅助来源": raw.get("批件/辅助来源"),
    }
    product["archive_info"] = {k: str(v) for k, v in fields.items() if v not in (None, "")}
    if not product["price"]["as_of"] and archive_date:
        product["price"]["as_of"] = str(archive_date)[:10]
    goals = list(product["goals"])
    for key in ("primary_directions", "supportive_directions"):
        goals.extend(s.strip() for s in str(raw.get(key) or "").split("；") if s.strip())
    for tag in product["tags_raw"]:
        values = tag["values"]
        # The two archive batches have different tag layouts.
        index = 3 if len(values) == 10 else 2
        if values[index]:
            goals.append(str(values[index]))
    product["goals"] = sorted(set(goals))
    evidence = list(product["evidence"])
    origin = f'{product["source"]["file"]} / {product["source"]["sheet"]} 第{product["source"]["row"]}行'
    for key in ("主要方向 / 功能", "辅助方向", "资料来源", "辅助来源"):
        if fields[key]:
            evidence.append(f'{origin}，{key}：{fields[key]}')
    for tag in product["tags_raw"]:
        values = tag["values"]
        index = 5 if len(values) == 10 else 4
        evidence.append(f'{product["source"]["file"]} / 健康标签 第{tag["row"]}行：{values[index]}；审核状态：{values[-1]}')
    product["evidence"] = list(dict.fromkeys(evidence))
    product["archive_evidence"] = list(product["evidence"])
    dose = str(raw.get("建议食用量") or "")
    # A nutrient serving basis such as “每3片” is not a daily dose.
    if product["daily_units"] is None and not re.search("历史|待核|以.*标签", dose):
        direct = re.search(r"每日(?:食用)?(\d+(?:\.\d+)?)(片|粒|袋|份|克)", dose)
        repeated = re.search(r"每日(\d+)次[，,；; ]*每次(\d+(?:\.\d+)?)(片|粒|袋|份|克)", dose)
        if direct or repeated:
            product["daily_units"] = float(direct[1]) if direct else float(repeated[1]) * float(repeated[2])
            product["unit"] = direct[2] if direct else repeated[3]
            product["label_source"] = f'{origin}，建议食用量：{dose}；来源：{fields["资料来源"] or "未提供"}；现售标签待核验'
    spec = str(raw.get("当前公开规格") or "")
    pieces = re.search(r"(\d+(?:\.\d+)?)(片|粒|袋|份)(?:/|$)", spec)
    weight = re.fullmatch(r"(\d+(?:\.\d+)?)克/(?:罐|瓶|袋)(?:（[^）]*）)?", spec)
    # Multi-bag/multi-tablet packages require manual identification of each unit.
    if not re.search(r"[袋瓶罐盒]\s*×", spec) and "两种" not in str(raw.get("剂型") or ""):
        pack = pieces or weight
        if pack:
            unit = pieces[2] if pieces else "克"
            if product["unit"] in (None, "", unit):
                product["unit"] = unit
                if product["units_per_pack"] is None:
                    product["units_per_pack"] = float(pack[1])
    restrictions = fields["不适宜人群"]
    if product["contraindications"] is None and restrictions and not re.match("待核|未列|无", str(restrictions)):
        product["contraindications"] = [str(restrictions)]
    if product["allergens"] is None:
        declared = re.findall(r"(大豆|鱼类|小麦|乳|蛋|花生|坚果)过敏", str(restrictions or "") + str(fields["注意事项"] or ""))
        if declared:
            product["allergens"] = sorted(set(declared))
    units = {"毫克": "mg", "微克": "µg", "克": "g", "mg": "mg", "µg": "µg", "g": "g"}
    for row in product["ingredients_raw"]:
        values = row["values"]
        amount = re.fullmatch(r"(\d+(?:\.\d+)?)(毫克|微克|克|mg|µg|g)", str(values[3]))
        basis = re.fullmatch(r"每(\d+)?(片|粒|袋|份|克)", str(values[4]))
        if amount and basis and basis[2] == product["unit"] and "官方" in str(values[5]) and values[6] == "已核":
            product["ingredients"].append({"name": values[2], "amount_per_unit": float(amount[1]) / float(basis[1] or 1),
                "unit": units[amount[2]], "source": f'{product["source"]["file"]} / 成分明细 第{row["row"]}行，{values[5]}：{values[7]}',
                "source_amount": str(values[3]), "source_basis": str(values[4])})
    # Keep the text-only catalog's explicitly stated per-unit amounts traceable.
    if not product["ingredients"] and raw.get("ingredients_raw"):
        for basis_count, basis_unit, amount, mass_unit, name in re.findall(
                r"每(\d+)?(片|粒|袋|份|克)含(\d+(?:\.\d+)?)(毫克|微克|克|mg|µg|g)([^。；]+)", str(raw["ingredients_raw"])):
            if product["unit"] in (None, "", basis_unit):
                product["unit"] = basis_unit
                product["ingredients"].append({"name": name.strip(), "amount_per_unit": float(amount) / float(basis_count or 1),
                    "unit": units[mass_unit], "source": f'{origin}，成分原文：{raw["ingredients_raw"]}；来源：{fields["资料来源"]}',
                    "source_amount": amount + mass_unit, "source_basis": "每" + (basis_count or "1") + basis_unit})


def read_excel(source, filename=None):
    """Accept existing archive layout and a flat product table; retain provenance."""
    import openpyxl
    name = filename or Path(source).name
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        book = openpyxl.load_workbook(BytesIO(source) if isinstance(source, bytes) else source,
                                      read_only=True, data_only=True)
    result = []
    archive_date = next((r[2] for r in book["抓取说明"].values if len(r) > 2 and r[0] == "范围"), None) if "抓取说明" in book.sheetnames else None
    for sheet in book.worksheets:
        rows = list(sheet.values)
        hi = next((i for i, r in enumerate(rows) if r and r[0] in ("product_id", "产品ID")
                   and len(r) > 1 and r[1] in ("product_name", "产品标准名称", "产品名称")), None)
        if hi is None:
            continue
        for rownum, row in enumerate(rows[hi + 1:], hi + 2):
            if not row or not row[0]:
                continue
            raw = {str(k): v for k, v in zip(rows[hi], row) if k is not None}
            tags, ingredients_raw = [], []
            for tab, destination in (("健康标签", tags), ("成分明细", ingredients_raw)):
                if tab in book.sheetnames:
                    destination.extend({"row": n, "values": list(r)} for n, r in enumerate(book[tab].values, 1) if r and r[0] == row[0])
            search = " ".join(str(v or "") for k, v in raw.items() if k in
                              ("健康目标", "primary_directions", "supportive_directions", "保健功能/营养目的"))
            search += " " + " ".join(" ".join(str(v or "") for v in t["values"][2:4]) for t in tags)
            goals = [g for g, aliases in GOALS.items() if any(a in search for a in aliases)]
            if raw.get("健康目标"):
                goals += [s.strip() for s in re.split("[；;,，]", str(raw["健康目标"])) if s.strip()]
            # Only flat explicitly numeric fields are operational. Historical free text stays raw.
            def numeric(key):
                v = raw.get(key)
                if v is None:
                    return None
                return float(number(v, f"{row[0]} {key}"))
            ingredient_json = raw.get("成分JSON")
            ingredients = json.loads(ingredient_json) if ingredient_json else []
            def items(key):
                v = raw.get(key)
                if v is None:
                    return None
                return [] if str(v).strip() == "无" else [s.strip() for s in re.split("[；;,，]", str(v)) if s.strip()]
            product = {
                "id": f"{name}:{row[0]}", "name": raw.get("product_name") or raw.get("产品标准名称") or raw.get("产品名称"),
                "sku": raw.get("SKU") or str(row[0]), "goals": sorted(set(goals)),
                "category": raw.get("法律分类") or raw.get("product_category") or "待核验",
                "status": "inactive" if raw.get("status") == "inactive" or raw.get("官网在售状态") == "停售" else "candidate",
                "daily_units": numeric("每日用量"), "unit": raw.get("单位"),
                "label_daily_max": numeric("说明书每日最大量"), "units_per_pack": numeric("包装含量"),
                "label_source": raw.get("标签来源"), "ingredients": ingredients,
                "ingredients_complete": raw.get("成分完整") == "是",
                "allergens": items("过敏原"), "contraindications": items("禁忌"),
                "label_reviewed": raw.get("审核状态") == "已审核", "evidence": [raw["依据"]] if raw.get("依据") else [],
                "price": {"amount": numeric("价格") if "价格" in raw else numeric("当前公开价(元)"),
                          "as_of": str(raw.get("价格日期") or raw.get("抓取日期") or "")[:10],
                          "valid_until": str(raw.get("价格有效期") or "")[:10], "currency": raw.get("币种") or "CNY",
                          "source": raw.get("价格来源") or raw.get("官方产品/价格来源"), "sku": raw.get("SKU") or str(row[0])},
                "source": {"file": name, "sheet": sheet.title, "row": rownum},
                "raw": raw, "tags_raw": tags, "ingredients_raw": ingredients_raw, "demo": False,
            }
            fill_archive_fields(product, archive_date)
            result.append(product)
    book.close()
    if not result:
        raise ValueError("未找到产品表。表头前两列应为产品ID、产品名称（也支持现有归档表头）。")
    if len({p["id"] for p in result}) != len(result):
        raise ValueError("产品ID重复，请按SKU区分同一产品的不同规格。")
    return result


def demo_catalog():
    common = {"category": "虚拟营养补充剂", "status": "candidate", "daily_units": 1,
              "label_daily_max": 1, "label_source": "虚拟示例标签v1，非真实产品说明书", "ingredients_complete": True,
              "label_reviewed": True, "allergens": [], "contraindications": [], "demo": True,
              "source": {"file": "虚拟示例库", "sheet": "示例", "row": 1}}
    definitions = [
        ("A", "产品 A · 植物蛋白粉", "蛋白质补充", "份", 30, 128, "蛋白质", 8, "g",
         "https://ods.od.nih.gov/HealthInformation/healthinformation.aspx"),
        ("C", "产品 C · 维生素 B6 片", "补充维生素B6", "片", 60, 89, "维生素B6", 1.2, "mg",
         "https://ods.od.nih.gov/factsheets/VitaminB6-Consumer/"),
        ("D", "产品 D · 钙营养片", "补钙", "片", 60, 96, "钙", 200, "mg",
         "https://ods.od.nih.gov/factsheets/Calcium-Consumer/"),
    ]
    return [dict(common, id=f"DEMO-{pid}", sku=f"DEMO-{pid}", name=name, goals=[goal], unit=unit,
                 units_per_pack=pack, ingredients=[{"name": nutrient, "amount_per_unit": amount, "unit": nu,
                                                  "source": "虚拟示例标签v1"}], evidence=[link],
                 price={"amount": price, "currency": "CNY", "as_of": TODAY(), "valid_until": TODAY(),
                        "source": "虚拟示例报价，仅演示计算", "sku": f"DEMO-{pid}"})
            for pid, name, goal, unit, pack, price, nutrient, amount, nu, link in definitions]


DEMO_TEXT = """用户：示例用户 001（虚构）
专业分析摘要：已评估日常饮食，明确存在蛋白质与维生素B6摄入不足；本示例仅演示补充目标匹配，不用于真实健康决策。
营养目标：蛋白质补充；补充维生素B6。
生活方式建议：保证规律饮食，优先增加多样化食物，保持适量活动。
复查建议：按原专业人员制定的计划复评饮食摄入。
风险结论：无就医优先项（虚拟示例）。"""


def extract(text):
    """Match explicit positive goal lines; do not derive indications from biomarkers."""
    goal_lines = [line for line in text.splitlines() if re.match(r"\s*(营养目标|补充目标|明确营养需求|健康目标)\s*[:：]", line)]
    goals = []
    for line in goal_lines:
        for segment in re.split("[；;，,。]", line.split("：", 1)[-1].split(":", 1)[-1]):
            if re.search("不需|无需|不建议|禁止|不补|排除|未确认|待确认|没有|否认|不宜|勿", segment):
                continue
            for goal, aliases in GOALS.items():
                if any(a.casefold() in segment.casefold() for a in aliases) and goal not in goals:
                    goals.append(goal)
    return {"summary": text.strip(), "goals": goals,
            "lifestyle": [l.split("：", 1)[-1] for l in text.splitlines() if l.startswith("生活方式建议：")],
            "follow_up": [l.split("：", 1)[-1] for l in text.splitlines() if l.startswith("复查建议：")],
            "mode": "local_rules", "goal_source": goal_lines}


def product_checks(p, profile, as_of):
    missing, blocked = [], []
    demo_ready = p.get('demo') is True and p.get('demo_ready') is True
    for key, label in (("daily_units", "每日用量"), ("unit", "用量单位"), ("label_daily_max", "标签每日上限"),
                       ("units_per_pack", "包装含量"), ("label_source", "现行标签来源")):
        if p.get(key) is None or p.get(key) == "":
            missing.append(label)
    if not p.get("label_reviewed") and not demo_ready:
        missing.append("现售标签审核")
    if (not p.get("ingredients_complete") and not demo_ready) or not p.get("ingredients"):
        missing.append("完整活性成分含量")
    if not p.get("evidence"):
        missing.append("目标关联依据")
    for key in ("allergens", "contraindications"):
        if p.get(key) is None:
            missing.append("过敏原" if key == "allergens" else "禁忌")
    if p.get("daily_units") is not None and p.get("label_daily_max") is not None:
        daily = number(p["daily_units"], "每日用量", Decimal("0.000001"))
        if daily > number(p["label_daily_max"], "标签上限") and not p.get("professional_adjustment"):
            blocked.append("每日用量超过标签范围，须提供该用户的专业调整方案")
    for allergen in p.get("allergens") or []:
        if any(allergen in a or a in allergen for a in profile.get("allergies") or []):
            blocked.append(f"过敏冲突：{allergen}")
    for restriction in p.get("contraindications") or []:
        user_terms = (profile.get("conditions") or []) + (profile.get("medications") or []) + [profile.get("pregnancy", "unknown")]
        if any(t and (t in restriction or restriction in t) for t in user_terms):
            blocked.append(f"禁忌冲突：{restriction}")
    for ingredient in p.get("ingredients") or []:
        if not ingredient.get("name") or ingredient.get("amount_per_unit") is None or not ingredient.get("source"):
            missing.append("成分名称/含量/来源")
        if ingredient.get("unit") not in MASS:
            missing.append("成分单位换算需确认")
    return list(dict.fromkeys(missing)), blocked


def quote(p, days, as_of):
    pr = p.get("price") or {}
    reason = None
    if pr.get("amount") is None:
        reason = "缺少价格"
    elif pr.get("currency") != "CNY":
        reason = "币种不是CNY，不能合并报价"
    elif not pr.get("source") or pr.get("sku") != p.get("sku"):
        reason = "价格来源或SKU未核对"
    else:
        try:
            if not require_date(pr.get("as_of"), "价格日期") <= as_of <= require_date(pr.get("valid_until"), "价格有效期"):
                reason = "价格过期或尚未生效"
        except ValueError:
            reason = "价格日期或有效期缺失"
    required = number(p["daily_units"], "每日量") * days
    pack = number(p["units_per_pack"], "包装含量", Decimal("0.000001"))
    count = int((required / pack).to_integral_value(rounding=ROUND_CEILING))
    unit_price = number(pr["amount"], "价格") if reason is None else None
    return {"packs": count, "required_units": float(required), "remaining_units": float(pack * count - required),
            "unit_price": float(unit_price) if unit_price is not None else None,
            "purchase_cost": money(unit_price * count) if unit_price is not None else None,
            "consumption_cost": money(unit_price * required / pack) if unit_price is not None else None,
            "price_reason": reason, "price_source": pr.get("source"), "price_date": pr.get("as_of"),
            "valid_until": pr.get("valid_until")}


def is_forbidden(product, forbidden):
    identities = {str(product.get(key, "")).strip().casefold() for key in ("id", "sku", "name")}
    return any(term.strip().casefold() in identities for term in forbidden)


def compose(payload, catalog, goal_matches=None):
    text = payload.get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("请粘贴已专业分析的文字结论")
    as_of = require_date(payload.get("as_of") or TODAY(), "报告日期")
    days = number(payload.get("days", 30), "周期天数", 1)
    if days != int(days) or days > 365:
        raise ValueError("周期应为1至365的整数天")
    budget = number(payload["budget"], "预算") if payload.get("budget") not in (None, "") else None
    shipping, discount = number(payload.get("shipping", 0), "运费"), number(payload.get("discount", 0), "优惠")
    profile = payload.get("profile") or {}
    forbidden = profile.get("forbidden_products") or []
    if not isinstance(forbidden, list) or any(not isinstance(term, str) or not term.strip() for term in forbidden):
        raise ValueError("禁用商品应为产品ID、SKU或完整名称的数组")
    analysis = extract(text)
    if payload.get("goal_context"):
        context = payload["goal_context"]
        analysis.update(goals=[g["catalog_goal"] or g["name"] for g in context["goals"]],
                        mode="model_confirmed", goal_source=[g["evidence"] for g in context["goals"]],
                        goal_context=context)
    missing, blocked = [], []
    if payload.get("goal_context"):
        missing.extend(payload["goal_context"].get("missing", []))
    if not payload.get("reviewer") or not payload.get("reviewed_at") or not payload.get("analysis_approved"):
        missing.append("原专业分析的审核状态、审核人或审核日期未确认")
    elif require_date(payload["reviewed_at"], "审核日期") > as_of:
        raise ValueError("审核日期不能晚于报告日期")
    risk = payload.get("risk", "unknown")
    for line in text.splitlines():
        if re.match(r"\s*(风险结论|风险提示|就医建议)\s*[:：]", line):
            conclusion = re.split("[:：]", line, maxsplit=1)[-1]
            if re.search("就医优先|暂停搭配|暂停推荐|紧急就医", conclusion) and not re.search("无|无需|没有|不需", conclusion):
                risk = "medical_first"
    if risk == "medical_first":
        blocked.append("原专业分析要求就医/复查优先，暂停产品组合")
    elif risk != "cleared":
        missing.append("原专业分析是否允许产品搭配尚未确认")
    for key, label in (("age", "年龄"), ("allergies", "过敏史"), ("conditions", "疾病史"),
                       ("medications", "正在用药"), ("current_supplements", "在用补充剂")):
        if profile.get(key) is None:
            missing.append(f"未确认{label}")
    if profile.get("pregnancy") not in ("none", "pregnant", "breastfeeding"):
        missing.append("孕哺状态未知")
    if profile.get("age") is not None and number(profile["age"], "年龄") < 18:
        blocked.append("第一版未建立儿童专用规则，请转专业个体评估")
    if not analysis["goals"]:
        missing.append("未识别明确营养目标，请在文字中提供“营养目标：…”；不从异常指标推断补充需求")
    candidates, selected, excluded = [], [], []
    covered = set()
    # Greedy selection favors direct coverage and fewer products, never optimizes sales value.
    ranked = sorted(catalog, key=lambda p: (-len(set(p.get("goals", [])) & set(analysis["goals"])), str(p.get("id"))))
    adjustments = payload.get("adjustments") or []
    if not isinstance(adjustments, list):
        raise ValueError("本次剂量调整应为JSON数组")
    ids = [a.get("catalog_id") for a in adjustments]
    if len(ids) != len(set(ids)):
        raise ValueError("同一产品只能填写一条本次剂量调整")
    if set(ids) - {p["id"] for p in catalog}:
        raise ValueError("剂量调整的catalog_id不在本次产品库中")
    for original in ranked:
        p = dict(original)
        if is_forbidden(p, forbidden):
            excluded.append({"id": p["id"], "name": p["name"], "reason": "用户禁用商品"})
            continue
        p.pop("professional_adjustment", None)
        adjustment = next((a for a in adjustments if a.get("catalog_id") == p["id"]), None)
        if adjustment:
            if any(not adjustment.get(k) for k in ("reason", "source", "reviewer", "reviewed_at", "profile_id")) or adjustment.get("profile_id") != profile.get("id"):
                missing.append(p["name"] + "：剂量调整缺少针对本用户的原因、依据或审核记录，未应用调整")
            elif require_date(adjustment["reviewed_at"], "调整审核日期") > as_of:
                raise ValueError("剂量调整审核日期不能晚于报告日期")
            else:
                p["daily_units"] = float(number(adjustment.get("daily_units"), "调整每日用量", Decimal("0.000001")))
                p["professional_adjustment"] = adjustment
                missing.append(p["name"] + "：已应用提供的个体剂量调整记录，仍须核实专业身份及依据适用性")
        matches = ([g for g in analysis["goals"] if g in p.get("goals", [])] if goal_matches is None
                   else goal_matches.get(p["id"], []))
        if not matches:
            excluded.append({"id": p["id"], "name": p["name"], "reason": "不匹配当前已明确营养目标"})
            continue
        if p.get("status") == "inactive":
            excluded.append({"id": p["id"], "name": p["name"], "reason": "来源标注停售/停用"})
            continue
        issues, conflicts = product_checks(p, profile, as_of)
        candidates.append({"id": p["id"], "name": p["name"], "goals": matches, "missing": issues, "conflicts": conflicts, "source": p.get("source"), "raw": p.get("raw", {})})
        if conflicts:
            excluded.append({"id": p["id"], "name": p["name"], "reason": "；".join(conflicts)})
            continue
        if issues:
            excluded.append({"id": p["id"], "name": p["name"], "reason": "待核验：" + "、".join(issues)})
            continue
        if goal_matches is None and set(matches) <= covered:
            excluded.append({"id": p["id"], "name": p["name"], "reason": "该目标已有直接匹配产品，避免重复搭配"})
            continue
        rationale = "原分析明确目标“" + "、".join(matches) + "”；依据当前标签的默认日量，不按指标偏离程度加量。"
        if p.get("professional_adjustment"):
            a = p["professional_adjustment"]
            rationale = f"本次用量从标签默认{original['daily_units']}{original['unit']}调整为{p['daily_units']}{p['unit']}；原因：{a['reason']}；依据：{a['source']}；专业复核：{a['reviewer']}，{a['reviewed_at']}。调整仅针对用户{a['profile_id']}。"
        selected.append(dict(p, matched_goals=matches, quote=quote(p, days, as_of), rationale=rationale))
        covered.update(matches)
    uncovered = [g for g in analysis["goals"] if g not in covered]
    if uncovered:
        missing.append("未覆盖需求：" + "、".join(uncovered) + "，请补齐候选标签/依据，不强凑组合")
    if selected:
        missing.append("产品组合仍需专业人员核对个体禁忌、药物相互作用及实际饮食摄入")
    if profile.get("pregnancy") in ("pregnant", "breastfeeding"):
        missing.append("孕哺人群需要专项个体复核")
    totals = {}
    for p in selected:
        for ing in p["ingredients"]:
            amount_mg = number(ing["amount_per_unit"], "成分含量") * MASS[ing["unit"]] * number(p["daily_units"], "每日量")
            item = totals.setdefault(ing["name"], {"mg": Decimal(0), "sources": [], "display_unit": ing["unit"]})
            item["mg"] += amount_mg
            item["sources"].append(p["name"])
    exposures = payload.get("exposures") or []
    nutrients = []
    for name, value in totals.items():
        ex = next((e for e in exposures if e.get("name") == name), {})
        unit = ex.get("unit", value["display_unit"])
        if unit not in MASS:
            raise ValueError(f"{name}摄入表单位不支持：{unit}")
        product_amount = value["mg"] / MASS[unit]
        diet = number(ex["diet_amount"], "饮食摄入") if ex.get("diet_amount") is not None else None
        other = number(ex["supplement_amount"], "其他补充剂摄入") if ex.get("supplement_amount") is not None else None
        total = product_amount + diet + other if diet is not None and other is not None else None
        limit = number(ex["upper_limit"], "上限") if ex.get("upper_limit") is not None else None
        scope = ex.get("limit_scope")
        evaluated = total if scope == "all" else product_amount + other if scope == "supplements" and other is not None else None
        result = "待核验"
        if limit is not None and ex.get("limit_source") and scope in ("all", "supplements"):
            lower_bound = product_amount + (other or 0) + ((diet or 0) if scope == "all" else 0)
            if lower_bound > limit:
                result = "超过提供的上限"
                blocked.append(f"{name} 已知摄入至少 {float(lower_bound):g}{unit} 超过提供的上限 {float(limit):g}{unit}")
            elif evaluated is not None:
                result = "未超过所提供上限（适用性仍须复核）"
            else:
                missing.append(f"{name}：其他来源摄入未知，无法确认合计是否在上限内")
        else:
            missing.append(f"{name}：饮食、在用补充剂或适用摄入上限资料不足")
        nutrients.append({"name": name, "unit": unit, "product_amount": float(product_amount),
                          "diet_amount": float(diet) if diet is not None else None,
                          "supplement_amount": float(other) if other is not None else None,
                          "total": float(total) if total is not None else None,
                          "upper_limit": float(limit) if limit is not None else None,
                          "limit_source": ex.get("limit_source"), "limit_scope": scope, "result": result, "sources": value["sources"]})
    known = sum((Decimal(str(p["quote"]["purchase_cost"])) for p in selected if p["quote"]["purchase_cost"] is not None), Decimal(0))
    unpriced = [p["name"] for p in selected if p["quote"]["purchase_cost"] is None]
    if discount > known + shipping:
        raise ValueError("整单优惠不能超过已知商品购买金额与运费之和")
    total_price = known + shipping - discount if selected and not unpriced else None
    if unpriced:
        missing.append("待报价：" + "、".join(unpriced))
    if total_price is not None and budget is not None and total_price > budget:
        missing.append("组合超过预算，请调整周期或换用经核验的替代产品；不通过减量掩盖费用")
    demo = any(p.get("demo") for p in catalog)
    if demo:
        missing.insert(0, "虚拟产品、标签及报价仅用于演示流程")
    if blocked:
        selected, nutrients = [], []
        total_price, known, unpriced = None, Decimal(0), []
    report = {"id": "REPORT-" + as_of.isoformat(), "as_of": as_of.isoformat(), "days": int(days),
              "input_text":text, "product_sources":[deepcopy(p) for p in catalog if p['id'] in {s['id'] for s in selected}],
              "status": "blocked" if blocked else "needs_review", "demo": demo, "analysis": analysis,
              "review": {"reviewer": payload.get("reviewer"), "reviewed_at": payload.get("reviewed_at"), "declared_approved": payload.get("analysis_approved", False)},
              "profile": profile, "selected_products": selected, "candidates": candidates, "excluded_products": excluded,
              "nutrients": nutrients, "uncovered_goals": uncovered, "missing": list(dict.fromkeys(missing)), "blocked": blocked,
              "pricing": {"known_subtotal": money(known), "total": money(total_price) if total_price is not None else None,
                          "shipping": float(shipping), "discount": float(discount), "budget": float(budget) if budget is not None else None,
                          "unpriced": unpriced, "budget_remaining": money(budget - total_price) if budget is not None and total_price is not None else None},
              "method": "本地规则：只匹配明确目标；先核验标签和禁忌；优先直接覆盖，减少重复；未使用大语言模型。"}
    return report


def clean_report_text(value):
    text = str(value or '').replace('\r\n', '\n').replace('\r', '\n')
    return re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', text).strip('\n')


def product_usage(product):
    for fields in (product, product.get('raw') or {}):
        for key in ('usage_method', '使用方式', '使用方法', '食用方法', '建议食用方法'):
            value = fields.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return '未提供，请核对现售标签后确认'


def report_main_problems(report):
    analysis = report['analysis']
    summary = analysis.get('summary') or ''
    if analysis.get('original_summary') or ('input_text' in report and summary != report['input_text']):
        return summary
    context = analysis.get('goal_context') or {}
    return context.get('summary') or ('本次已确认的需求：' + '、'.join(analysis.get('goals') or []) if analysis.get('goals') else '未识别明确需求，请核对原专业分析。')


def markdown(r):
    def cell(value):
        return '待确认' if value is None else str(value).replace('|', '\\|').replace('\r', ' ').replace('\n', ' ')
    def literal(value, language='text'):
        fence = '`' * max(3, max((len(s) for s in re.findall(r'`+', value)), default=0) + 1)
        return [fence + language, value, fence]
    lines = ['# 用户健康分析与产品组合报告', '', f"报告日期：{r['as_of']} · 周期：{r['days']}天", '',
             '用户：' + cell(r['profile'].get('name') or r['profile'].get('id')) + '；用户编号：' + cell(r['profile'].get('id')), '',
             '状态：' + ('暂停产品搭配' if r['status'] == 'blocked' else '待专业复核的草案'), '',
             '虚拟产品、用量和报价仅用于演示。' if r['demo'] else '方案需由专业人员结合个体资料审核。', '',
             '## 1 本次有什么主要问题', '', clean_report_text(report_main_problems(r)), '',
             '本次确认的需求：' + ('、'.join(r['analysis']['goals']) or '尚未识别')]
    if r.get('edited_at'):
        lines += ['', '报告文字已人工修改并重新复核：' + cell(r['edited_at'])]
    if r.get('warnings'):
        lines += ['', '### 组合提醒（待确认）', ''] + ['- ' + s for s in r['warnings']]
    advice = r['analysis']['lifestyle'] + r['analysis']['follow_up']
    if advice:
        lines += ['', '### 建议与复查', ''] + ['- ' + s for s in advice]
    lines += ['', '## 2 选取了什么产品，为什么选取这些产品', '']
    for p in r['selected_products']:
        lines += ['### ' + cell(p['name']), '', '对应需求：' + '、'.join(p['matched_goals']), '', p['rationale'], '']
    if not r['selected_products']:
        lines += ['没有可执行组合，请核对附录中的资料缺项和筛选记录。']
    lines += ['', '## 3 产品的剂量、使用方式及价格总和', '',
              '| 产品 / SKU | 每日用量 | 使用方式 | 周期包装数 | 购买金额（元） | 消耗成本（元） |',
              '|---|---|---|---|---|---|']
    for p in r['selected_products']:
        q = p['quote']
        lines += ['| ' + ' | '.join(cell(v) for v in (p['name'] + ' / ' + p['sku'],
                  f"{p['daily_units']:g}{p['unit']}", product_usage(p), q['packs'], q['purchase_cost'], q['consumption_cost'])) + ' |']
    pr = r['pricing']
    lines += ['', '总购买金额：' + (f"{pr['total']:.2f}元" if pr['total'] is not None else '待报价或已暂停，未把未知价格当0元。'), '',
              f"已知商品小计：{pr['known_subtotal']:.2f}元；运费：{pr['shipping']:.2f}元；优惠：{pr['discount']:.2f}元。", '',
              '包装数 = 向上取整（每日量 × 周期天数 ÷ 每包装含量）；消耗成本与实际购买费用分别计算。使用方式缺失时不自行补充服用时间或方法。']
    if pr['budget_remaining'] is not None:
        lines += ['', ('预算余量：' if pr['budget_remaining'] >= 0 else '超出预算：') + f"{abs(pr['budget_remaining']):.2f}元"]
    for p in r['selected_products']:
        q = p['quote']
        lines += ['', cell(p['name']) + '：价格来源 ' + cell(q['price_source']) + '；日期 ' + cell(q['price_date']) + '；有效至 ' + cell(q['valid_until']) + '。' + (q['price_reason'] or '')]
    lines += ['', '## 4 附属内容', '', '### 4.1 输入信息原文', '',
              '仅整理多余空行，保留输入内容；原始数据仍保存在报告中。', '']
    original = r.get('input_text', r['analysis'].get('original_summary', r['analysis']['summary']))
    lines += literal(clean_report_text(original))
    lines += ['', '### 4.2 推荐产品的原始信息', '']
    if 'product_sources' not in r:
        lines += ['历史报告未保存独立原始快照，以下为报告内保留的产品资料。', '']
    for p in r.get('product_sources', r['selected_products']):
        lines += ['#### ' + cell(p['name']), ''] + literal(json.dumps(p, ensure_ascii=False, indent=2, default=str), 'json') + ['']
    lines += ['', '### 每日成分合计', '', '| 成分 | 本组合 | 饮食 | 其他补充剂 | 合计 | 提供的上限 | 单位 | 核验 |', '|---|---|---|---|---|---|---|---|']
    for n in r['nutrients']:
        lines += ['| ' + ' | '.join(cell(n[k]) for k in ('name','product_amount','diet_amount','supplement_amount','total','upper_limit','unit','result')) + ' |']
    context = r['analysis'].get('goal_context')
    if context:
        lines += ['', '### 需求提取与确认依据', '']
        lines += ['- ' + cell(g['name']) + '：' + cell(g['evidence']) for g in context['goals']]
        lines += ['- 注意：' + cell(s) for s in context['cautions']]
    if r.get('matching'):
        lines += ['', '### 产品关联依据', '', cell(r['matching']['summary'])]
        for item in r['matching']['products']:
            if item['status'] == 'related':
                lines += ['', '- ' + cell(item['name']) + '：' + cell(item['reason'])]
                lines += ['  - 原文依据（' + cell(e['field']) + '）：' + cell(e['quote']) for e in item['evidence']]
    lines += ['', '### 筛选记录', ''] + ['- ' + cell(p['name']) + '：' + cell(p['reason']) for p in r['excluded_products']]
    lines += ['', '### 阻断与待确认', ''] + ['- ' + s for s in r['blocked'] + r['missing']]
    if r.get('learning', {}).get('rules'):
        lines += ['', '### 命中的人工审核规则', '', '规则不覆盖用户禁用、标签用量及安全校验。']
        for rule in r['learning']['rules']:
            lines += ['', '- ' + cell(rule['title']) + ' · 规则 ' + rule['id'] + ' · 版本 ' + str(rule['revision']) + ' · 来源案例 ' + rule['case_id'],
                      '  - 依据：' + cell(rule['reason']), '  - 局限：' + cell(rule['limitations'])]
    lines += ['', '审核人：' + cell(r['review']['reviewer']) + '；审核日期：' + cell(r['review']['reviewed_at']), '', r['method'], '']
    if r.get('evaluation'):
        ev = r['evaluation']
        verdict = '有提醒，待确认' if ev['verdict'] == 'needs_review' else '通过（仍需专业复核）'
        lines += ['### 大模型复核', '', f"模型：{ev['model']}；结果：{verdict}；复核时间：{ev['evaluated_at']}。", '', ev['summary']]
        lines += ['- ' + item['name'] + '（' + ('待确认' if item['status'] == 'unknown' else '通过') + '）：' + item['reason'] for item in ev['checks']]
        lines += ['', '复核仅依据所提供资料检查一致性，不代表临床审核或保证发现所有风险。', '']
    return '\n'.join(lines)
