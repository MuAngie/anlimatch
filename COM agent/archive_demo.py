"""Fill the existing archive for the explicitly requested demo experiment."""
from copy import deepcopy

PROTEIN_REFERENCE = 'https://www.amway.com.cn/Content/newsBrandStrength/newsProduct/newsNutrilite/Key_Standards_for_Buying_Protein_Powder/news?channelId=100000257&contentId=2074421211336060929'
NOTE = 'Demo假设，仅供流程试验；不代表完整真实配方、现售标签审核或真实用量上限。原Excel说明和警告继续保留。'
MULTI = [('维生素A',300,'µg'),('维生素D',5,'µg'),('维生素E',5,'mg'),('维生素C',60,'mg'),
         ('维生素B1',1,'mg'),('维生素B2',1,'mg'),('维生素B6',1,'mg'),('叶酸',100,'µg'),
         ('维生素B12',1,'µg'),('钙',100,'mg'),('镁',50,'mg'),('铁',5,'mg'),('锌',5,'mg')]
# Quantity and composition defaults are test fixtures, not clinical or manufacturer recommendations.
# Each ingredient amount uses the product's operational unit, including grams for bulk powder.
RECIPES = [
    (['P001'],2,'组',60,460,MULTI),
    (['P002'],10,'克',400,310,[('蛋白质',.8,'g')]),
    (['P003'],1,'片',300,360,[('维生素B1',1.2,'mg'),('维生素B2',1.2,'mg'),('烟酸',6.6,'mg'),
        ('泛酸',5,'mg'),('维生素B6',1.2,'mg'),('叶酸',150,'µg'),('维生素B12',2,'µg'),('钙',95,'mg')]),
    (['P004','AMW-WH-007'],3,'片',250,239,[('钙',200,'mg'),('镁',50,'mg'),('锰',.5,'mg'),
        ('锌',2,'mg'),('铜',.2,'mg'),('维生素D',1.7,'µg')]),
    (['P005'],1,'片',200,347,[('维生素C',100,'mg')]),
    (['P006','AMW-WH-006'],1,'片',150,170,[('铁',10,'mg'),('叶酸',200,'µg')]),
    (['P007'],3,'片',54,267,[('膳食纤维',.5,'g'),('低聚果糖',.3,'g')]),
    (['P008'],1,'粒',180,400,[('β-胡萝卜素',1,'mg'),('α-胡萝卜素',.2,'mg'),('叶黄素',1,'mg'),('玉米黄质',.2,'mg')]),
    (['P009','AMW-CV-002'],1,'粒',60,340,[('茶多酚',100,'mg'),('茶黄素',10,'mg')]),
    (['P010','AMW-CV-004'],1,'粒',60,429,[('辅酶Q10',40,'mg'),('维生素E',5,'mg'),('迷迭香提取物',10,'mg')]),
    (['P011','AMW-WH-004'],1,'片',90,300,MULTI),
    (['P012'],1,'粒',120,215,[('小麦胚芽油',300,'mg'),('维生素E',18,'mg')]),
    (['P013','AMW-WH-008'],1,'片',60,528,[('骨碎补提取物',100,'mg'),('淫羊藿提取物',100,'mg'),('丹参提取物',100,'mg')]),
    (['P014','AMW-MET-001'],1,'片',60,260,[('白芸豆提取物',100,'mg'),('淡豆豉提取物',100,'mg')]),
    (['P015','AMW-MET-004'],10,'克',460,618,[('蛋白质',.6,'g')]),
    (['P016','AMW-CV-001'],1,'粒',135,300,[('EPA+DHA',345,'mg')]),
    (['P017','AMW-CV-005'],1,'片',60,402,[('银杏叶提取物',40,'mg'),('肉苁蓉提取物',100,'mg')]),
    (['P018','AMW-CV-006'],10,'克',420,630,[('大豆磷脂',.9,'g'),('维生素E',.1,'mg')]),
    (['P019','AMW-CV-003'],1,'袋',30,388,[('陈皮提取物',100,'mg'),('当归提取物',100,'mg'),('沙棘提取物',100,'mg')]),
    (['P020','AMW-WH-003'],1,'袋',30,368,[('黄精提取物',100,'mg')]),
    (['AMW-WH-001'],1,'袋',30,388,[('葛根提取物',100,'mg'),('啤酒花提取物',20,'mg')]),
    (['AMW-WH-002'],1,'片',30,300,[('西伯利亚大黄提取物',4,'mg'),('接骨木莓提取物',50,'mg'),
        ('枸杞提取物',50,'mg'),('维生素D',5,'µg'),('维生素K',20,'µg'),('镁',50,'mg')]),
    (['AMW-WH-005','AMW-MET-005'],1,'袋',30,300,[('益生菌制剂干粉（Demo质量）',.1,'g')]),
    (['AMW-MET-002'],1,'袋',30,388,[('桑叶提取物',100,'mg'),('葫芦巴提取物',100,'mg'),('肉桂提取物',50,'mg'),('乌梅提取物',100,'mg')]),
    (['AMW-MET-003'],1,'袋',30,300,[('蛋白质',10,'g'),('膳食纤维',3,'g'),('维生素C',30,'mg'),('钙',100,'mg')]),
    (['AMW-WH-009'],1,'袋',30,388,[('槲皮素',20,'mg'),('芦丁',20,'mg'),('鞣花酸',10,'mg'),('没食子酸',10,'mg')]),
]
BY_SKU = {sku:(dose,unit,pack,price,ingredients) for skus,dose,unit,pack,price,ingredients in RECIPES for sku in skus}


def fill_demo_fields(product):
    if product.get('demo_ready') or product.get('sku') not in BY_SKU:
        return product
    dose,unit,pack,price,ingredients = BY_SKU[product['sku']]
    assumptions = {}
    def fill(key, value, reason):
        if product.get(key) is None or product.get(key) == '':
            product[key] = value
            assumptions[key] = reason
    fill('unit',unit,'Demo剂型单位')
    fill('daily_units',dose,'Demo日量：参考原表或按剂型设置；不是真实使用建议')
    fill('units_per_pack',pack,'Demo包装数量；原表已知包装保留')
    fill('label_daily_max',product['daily_units'],'Demo计算上限等于默认日量，不代表真实标签或人体摄入上限')
    fill('label_source','Demo演示标签v1；'+NOTE,'虚构标签，仅用于计算')
    known = {i['name'] for i in product['ingredients']}
    for name,amount,mass_unit in ingredients:
        if name not in known:
            source = 'Demo假设成分，按每'+product['unit']+'计；非真实完整配方'
            if product['sku'] == 'P002': source += '；蛋白质80%比例参考：'+PROTEIN_REFERENCE
            product['ingredients'].append({'name':name,'amount_per_unit':amount,'unit':mass_unit,'source':source})
    assumptions['ingredients'] = '保留已有成分数值，补入演示计算成分；仅模拟本列表，不声明真实配方完整'
    raw = product.get('raw',{})
    text = ' '.join(str(v or '') for k,v in raw.items() if k in ('ingredients_raw','关键成分摘要','主要成分'))
    allergens = [s for s in ('大豆','小麦','鱼类','乳') if s in text or (s == '鱼类' and '鱼油' in text)]
    fill('allergens',allergens,'Demo过敏原：按原表已知原料模拟；空列表不证明真实产品无过敏原')
    restrictions = ['pregnant','breastfeeding']
    if any(s in product['name'] for s in ('鱼油','银杏','丹参','辅酶Q10')): restrictions += ['华法林','抗凝药']
    if '蛋白' in product['name']: restrictions += ['肾功能不全']
    if '铁质' in product['name']: restrictions += ['铁过载']
    fill('contraindications',restrictions,'Demo禁忌用于测试冲突检查；原表警告保留，不是完整真实禁忌清单')
    if not product.get('evidence'): product['evidence'] = ['Demo关联线索：'+text]
    quoted = product['price']
    if quoted.get('amount') is None: quoted['amount'] = price
    quoted.update(as_of='2026-10-11',valid_until='2027-10-11',currency='CNY',sku=product['sku'],
                  source='Demo报价：沿用Excel已知参考价，未知价使用假设值；非实时价格')
    assumptions['price'] = 'Demo报价及有效期，仅测试包装和金额计算'
    product.update(demo=True,demo_ready=True,demo_assumptions=assumptions,demo_note=NOTE)
    return product


def complete_demo_catalog(products):
    return [fill_demo_fields(deepcopy(p)) for p in products]
