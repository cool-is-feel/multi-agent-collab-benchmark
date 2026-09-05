# -*- coding: utf-8 -*-
"""生成 MultiAgentCollabBench 数据集（200 题 / 10 类）。

按 benchmark/test.txt 的要求：
- 至少 200 题，10 个类别，每类约 20 题
- 难度分 easy / medium / hard 三档
- 每题体现多智能体协作优势（分工、讨论、跨领域等）
- 输出为指定 JSON 结构

用法:
    python generate_benchmark.py [输出路径]
默认输出: ../data/MultiAgentCollabBench.json
"""
import json
import sys
from pathlib import Path

CATEGORIES = [
    "open_reasoning",   # 1 开放式推理
    "math",             # 2 数学问题求解
    "code",             # 3 代码生成与调试
    "fact_checking",    # 4 事实核查与信息整合
    "creative_writing", # 5 创意写作
    "planning",         # 6 规划与决策
    "translation",      # 7 多语言翻译与本地化
    "science",          # 8 科学问题回答
    "debate",           # 9 辩论与观点整合
    "tool_use",         # 10 工具使用与 API 调用
]

_RAW = []  # (category, difficulty, description, input, expected_output, collaboration_type, num_agents_suggested)


def Q(category, difficulty, description, inp, expected_output,
      collaboration_type="role_based", num_agents_suggested=2):
    _RAW.append((category, difficulty, description, inp, expected_output,
                 collaboration_type, num_agents_suggested))


# ================= 1. 开放式推理 open_reasoning (20) =================
# 简单 7
Q("open_reasoning", "easy", "判断「远程办公对员工生产率的影响」，请从至少 3 个相互独立的角度分别分析，并说明各角度关注的焦点。", "", "评估标准：识别出 >=3 个独立角度（如沟通效率、工作自主性、生活平衡/监督成本等），且每个角度有明确关注点，逻辑自洽。", "free_form", 2)
Q("open_reasoning", "easy", "评估「是否应在中小学推行统一校服」：分别给出至少 2 个支持理由和 2 个反对理由。", "", "评估标准：正反各 >=2 个理由，理由具体且互不重复，覆盖经济/心理/管理等不同维度。", "free_form", 2)
Q("open_reasoning", "easy", "分析「短视频流行」的三个主要原因，并按影响力大小排序。", "", "评估标准：给出 >=3 个原因，有排序，且排序附有简要依据；原因不局限于单一维度。", "free_form", 2)
Q("open_reasoning", "easy", "决策「周末去爬山还是在家休息」：列出影响该决策的关键因素，并按重要性排序说明理由。", "", "评估标准：列出 >=4 个因素（如天气、体力、社交、恢复需求），有排序与理由。", "free_form", 2)
Q("open_reasoning", "easy", "解释「为什么有人早上精力好、晚上精力差」，从生理节律与生活习惯两方面说明。", "", "评估标准：同时覆盖生理（昼夜节律/睡眠类型）与习惯（作息、咖啡因、运动）两方面，解释清晰。", "free_form", 2)
Q("open_reasoning", "easy", "设计 3 个用来判断一个人是否适合创业的问题，并说明每个问题背后的考察点。", "", "评估标准：3 个问题各不相同，分别考察风险承受、执行力、学习/资源能力等，考察点说明清楚。", "free_form", 2)
Q("open_reasoning", "easy", "回答「纸质书会消失吗」：给出你的判断，并列出支持该判断的 2 条关键理由。", "", "评估标准：有明确判断 + >=2 条理由，理由可来自技术/成本/习惯/体验等维度。", "free_form", 2)
# 中等 7
Q("open_reasoning", "medium", "分析「人工智能是否会加剧社会不平等」，从经济、教育、就业三个维度分别论证。", "", "评估标准：三个维度各给出机制性分析（不空喊），并能指出谁受益、谁受损，结论有据。", "free_form", 3)
Q("open_reasoning", "medium", "识别「免费软件（如免费 App）如何盈利」的至少 3 种商业模式，并比较各自的适用条件。", "", "评估标准：>=3 种模式（广告、增值订阅、数据/导流、企业版等），比较其适用条件而非罗列。", "free_form", 2)
Q("open_reasoning", "medium", "评估一项「城市核心区禁燃油车」政策：列出主要利益相关方及其可能的态度与诉求。", "", "评估标准：识别 >=4 类利益相关方（车主、居民、商户、政府、车企等），各附态度与诉求。", "free_form", 3)
Q("open_reasoning", "medium", "就「终身学习是必需品还是奢侈品」进行论证：给出正反两方观点，并给出你的结论。", "", "评估标准：正反各有实质论据，结论明确且与论据衔接，避免和稀泥。", "debate", 2)
Q("open_reasoning", "medium", "分析一次团队项目失败的可能原因，至少覆盖沟通、分工、目标三个维度，各给一个具体场景。", "", "评估标准：三个维度各含 >=1 个具体可感场景，且能指向相应的改进措施。", "free_form", 2)
Q("open_reasoning", "medium", "列出「远程医疗」的优缺点各 3 项，并说明哪类人群最受益。", "", "评估标准：优缺点各 >=3 项，指出受益人群（如偏远地区/慢病/轻症）并说明原因。", "free_form", 2)
Q("open_reasoning", "medium", "设计一个简单的实验来验证「音乐能提升专注力」这一假设，包括变量、对照组与衡量指标。", "", "评估标准：包含自变量（是否听音乐）、因变量（专注度指标）、对照组、控制变量，方案可执行。", "free_form", 2)
# 困难 6
Q("open_reasoning", "hard", "论证「基础科学研究是否应完全由政府资助」，从长期/短期收益、知识外溢、市场失灵三方面展开。", "", "评估标准：三方面均有机制性论证，承认反面观点并回应，结论有立场而非折中。", "debate", 3)
Q("open_reasoning", "hard", "评估「全民基本收入（UBI）」的可行性：识别该主张的核心假设，并逐一挑战其是否成立。", "", "评估标准：识别 >=3 个核心假设（如财政可持续、不降低劳动参与），逐一质疑并给出证据或反例。", "free_form", 3)
Q("open_reasoning", "hard", "为一家濒临倒闭的实体书店设计转型策略，至少覆盖 4 个不同维度（如选品、体验、线上、社区）。", "", "评估标准：>=4 个维度，每个维度有可落地措施，且措施之间相互协同而非孤立。", "free_form", 3)
Q("open_reasoning", "hard", "分析「信息茧房」的形成机制，并提出一个可验证的干预方案（含验证指标）。", "", "评估标准：机制分析有因果链条；干预方案具体且给出可量化验证指标。", "free_form", 3)
Q("open_reasoning", "hard", "论证「技术是否中立」：给出正反两套哲学论证，并说明你倾向哪一方及理由。", "", "评估标准：正反各有一套逻辑完整的论证，明确表态并给出理由，引用概念准确。", "debate", 2)
Q("open_reasoning", "hard", "为「城市交通拥堵」建立多因素因果分析框架，区分直接因素、结构因素与反馈回路。", "", "评估标准：框架分层清晰（直接/结构/反馈），至少覆盖供需两端，并指出关键杠杆点。", "free_form", 3)


# ================= 2. 数学问题求解 math (20) =================
# 简单 7
Q("math", "easy", "计算 12 × 15 + 8 × 25（可分工：各自计算一项乘积后再求和）。", "", "380", "role_based", 2)
Q("math", "easy", "解一元一次方程 2x + 5 = 21。", "", "x = 8", "role_based", 2)
Q("math", "easy", "一个长方形长 8、宽 5，求它的面积。", "", "40", "role_based", 2)
Q("math", "easy", "计算 3/4 + 2/3，结果用最简分数表示。", "", "17/12", "role_based", 2)
Q("math", "easy", "求 1 到 100 所有整数之和。", "", "5050", "role_based", 2)
Q("math", "easy", "一件原价 250 元的商品先打 8 折，再享受满 200 减 30，实际应付多少元？", "", "170 元", "role_based", 2)
Q("math", "easy", "计算 7! / 5!。", "", "42", "role_based", 2)
# 中等 7
Q("math", "medium", "解二元一次方程组：x + y = 10，x - y = 4，求 x 的值。", "", "7", "role_based", 2)
Q("math", "medium", "求二次方程 x² - 5x + 6 = 0 的两个根之和。", "", "5", "role_based", 2)
Q("math", "medium", "同时掷两枚公平骰子，求点数和为 7 的概率。", "", "6/36 = 1/6", "role_based", 2)
Q("math", "medium", "本金 10000 元，年利率 5%，按复利计算 3 年后的本息和（精确到分）。", "", "约 11576.25 元", "role_based", 2)
Q("math", "medium", "一个三角形三边分别为 3、4、5，求它的面积。", "", "6", "role_based", 2)
Q("math", "medium", "等差数列 2, 5, 8, 11, ... 的第 20 项是多少？", "", "59", "role_based", 2)
Q("math", "medium", "圆的半径为 7，求面积（π 取 3.14）。", "", "153.86", "role_based", 2)
# 困难 6
Q("math", "hard", "求 ∫(3x² + 2x)dx 在区间 [0, 1] 上的定积分值。", "", "2", "role_based", 2)
Q("math", "hard", "解线性方程组：2x + 3y = 7，5x - y = 9，求 x 的值。", "", "2", "role_based", 2)
Q("math", "hard", "一个袋子有 3 个红球、2 个蓝球，不放回地随机抽取 2 个，求恰好 1 红 1 蓝的概率。", "", "3/5（= 0.6）", "role_based", 2)
Q("math", "hard", "从 10 人中选出 3 人组成小组，共有多少种选法？", "", "C(10,3) = 120", "role_based", 2)
Q("math", "hard", "求极限 lim(x→0) sin(x)/x。", "", "1", "role_based", 2)
Q("math", "hard", "用 100 米篱笆围成一个矩形区域，求能围成的最大面积（可分工：一人建面积模型，一人求极值）。", "", "625 平方米（正方形 25×25）", "role_based", 2)


# ================= 3. 代码生成与调试 code (20) =================
# 简单 7
Q("code", "easy", "编写 Python 函数 is_even(n)，判断整数 n 是否为偶数，返回布尔值。", "n 为整数", "def is_even(n):\n    return n % 2 == 0", "role_based", 2)
Q("code", "easy", "编写 Python 代码计算列表 nums 所有元素之和（不使用内置 sum）。", "nums = [1, 2, 3, 4]", "total = 0\nfor x in nums:\n    total += x\n# total == 10", "role_based", 2)
Q("code", "easy", "编写函数 max2(a, b)，返回两个数中较大的一个。", "a, b 为数字", "def max2(a, b):\n    return a if a > b else b", "role_based", 2)
Q("code", "easy", "下面代码运行报错，请定位错误并给出修复（一人找错、一人复核）：\nprint(x)", "x 未定义", "错误：x 未定义。修复：先赋值，如 x = 10 再 print(x)。", "role_based", 2)
Q("code", "easy", "编写 Python 代码将字符串 s 反转。", "s = 'hello'", "s[::-1]  # 'olleh'", "role_based", 2)
Q("code", "easy", "编写函数 factorial(n) 计算 n 的阶乘。", "n >= 0", "def factorial(n):\n    r = 1\n    for i in range(2, n+1):\n        r *= i\n    return r", "role_based", 2)
Q("code", "easy", "编写 Python 代码判断字符串 s 是否为回文。", "s = 'racecar'", "s == s[::-1]  # True", "role_based", 2)
# 中等 7
Q("code", "medium", "实现「两数之和」：给定数组 nums 和目标 target，返回两个元素下标（恰好一组解）。", "nums = [2,7,11,15], target = 9", "用哈希表 O(n)：\nseen = {}\nfor i, v in enumerate(nums):\n    if target - v in seen:\n        return [seen[target - v], i]\n    seen[v] = i", "role_based", 2)
Q("code", "medium", "实现二分查找：在有序数组中查找目标值，返回下标或 -1。", "nums 升序", "def bisect(nums, t):\n    lo, hi = 0, len(nums)-1\n    while lo <= hi:\n        mid = (lo+hi)//2\n        if nums[mid] == t: return mid\n        if nums[mid] < t: lo = mid+1\n        else: hi = mid-1\n    return -1", "role_based", 2)
Q("code", "medium", "实现一个 Stack 类，支持 push、pop、top（栈顶）方法。", "无", "class Stack:\n    def __init__(self): self.items = []\n    def push(self, x): self.items.append(x)\n    def pop(self): return self.items.pop()\n    def top(self): return self.items[-1]", "role_based", 2)
Q("code", "medium", "读取 JSON 文件并统计某个字段出现的总次数。", "文件 data.json 为对象列表", "import json\nfrom collections import Counter\nwith open('data.json') as f:\n    items = json.load(f)\ncnt = Counter(i.get('field') for i in items)", "role_based", 2)
Q("code", "medium", "编写正则表达式匹配「简化版」合法邮箱（用户名@域名.后缀）。", "无", "r'^[\\w.+-]+@[\\w-]+\\.[a-zA-Z]{2,}$'", "role_based", 2)
Q("code", "medium", "用 ThreadPoolExecutor 并行处理列表 items，并对每个元素调用 f，收集结果。", "无", "from concurrent.futures import ThreadPoolExecutor\nwith ThreadPoolExecutor() as ex:\n    results = list(ex.map(f, items))", "role_based", 2)
Q("code", "medium", "下面代码在输入为 0 时会崩溃，请定位并修复（一人复现、一人修）：\nprint(10 // x)", "x = 0", "除零错误。修复：加保护，如 if x == 0: 处理边界，或 try/except ZeroDivisionError。", "role_based", 2)
# 困难 6
Q("code", "hard", "实现一个 LRU 缓存类，get 和 put 均为 O(1)。", "容量为 capacity", "用 OrderedDict：\nfrom collections import OrderedDict\nclass LRU:\n    def __init__(self, cap): self.cap = cap; self.d = OrderedDict()\n    def get(self, k):\n        if k not in self.d: return -1\n        self.d.move_to_end(k); return self.d[k]\n    def put(self, k, v):\n        if k in self.d: self.d.move_to_end(k)\n        self.d[k] = v\n        if len(self.d) > self.cap: self.d.popitem(last=False)", "role_based", 2)
Q("code", "hard", "编写一个装饰器 timer，用于打印被装饰函数的执行耗时（秒）。", "无", "import time\ndef timer(f):\n    def w(*a, **k):\n        t = time.time(); r = f(*a, **k)\n        print(time.time() - t); return r\n    return w", "role_based", 2)
Q("code", "hard", "实现快速排序（对列表原地排序或返回新列表均可）。", "无", "def qs(a):\n    if len(a) <= 1: return a\n    p = a[len(a)//2]\n    l = [x for x in a if x < p]\n    m = [x for x in a if x == p]\n    r = [x for x in a if x > p]\n    return qs(l) + m + qs(r)", "role_based", 2)
Q("code", "hard", "用 threading 实现生产者-消费者模型（一个生产者、一个消费者，用队列传递）。", "无", "import threading, queue\nq = queue.Queue()\ndef prod():\n    for i in range(10): q.put(i)\ndef cons():\n    while True:\n        x = q.get(); print(x); q.task_done()\nthreading.Thread(target=prod).start()\nthreading.Thread(target=cons, daemon=True).start()", "role_based", 2)
Q("code", "hard", "下面多线程代码偶发数据竞争，请定位问题并给出修复（一人分析竞态、一人验证修复）。", "共享计数器 counter += 1", "竞态：counter += 1 非原子。修复：用 threading.Lock 包裹，或使用 itertools.count / 原子操作。", "role_based", 2)
Q("code", "hard", "编写一个简单 REST 客户端，调用分页 API 并合并所有页结果。", "接口返回 {items, next_page}", "import requests\nitems = []; page = 1\nwhile True:\n    r = requests.get(url, params={'page': page}).json()\n    items.extend(r['items'])\n    if not r.get('next_page'): break\n    page = r['next_page']", "role_based", 2)


# ================= 4. 事实核查与信息整合 fact_checking (20) =================
# 简单 7
Q("fact_checking", "easy", "根据输入的两条来源判断说法「某公司成立于 2010 年」是否有依据。", "来源 A：公司官网称 2010 年成立；来源 B：媒体报道称 2012 年成立。", "结论：存疑/矛盾。两来源年份不一致，需要更权威来源核实。", "role_based", 2)
Q("fact_checking", "easy", "判断「地球是太阳系最大的行星」是否正确，并说明。", "", "错误。太阳系最大的行星是木星。", "role_based", 2)
Q("fact_checking", "easy", "从下面文字中提取 3 个关键事实。", "2023 年，公司 X 发布新产品 Y，营收同比增长 15%，CEO 宣布明年进入欧洲市场。", "关键事实：1) 2023 年发布产品 Y；2) 营收同比增长 15%；3) 明年计划进入欧洲市场。", "role_based", 2)
Q("fact_checking", "easy", "判断「水在标准大气压下 100°C 沸腾」是否正确。", "", "正确（标准大气压下水的沸点为 100°C）。", "role_based", 2)
Q("fact_checking", "easy", "比较输入的两个来源对同一事件的报道，指出其中的矛盾点。", "来源 A：事故造成 3 人受伤；来源 B：事故造成 5 人受伤。", "矛盾点：受伤人数不一致（3 vs 5），需核实。", "role_based", 2)
Q("fact_checking", "easy", "判断「长城是唯一能从太空肉眼看到的建筑」这一说法是否属实。", "", "不属实，是常见谣言；太空可见的长城说法缺乏科学依据。", "role_based", 2)
Q("fact_checking", "easy", "判断输入的两条信息是否一致，并说明。", "信息 A：城市人口 800 万；信息 B：城市人口 820 万。", "不一致（800 万 vs 820 万），存在约 20 万差异，需注明统计口径。", "role_based", 2)
# 中等 7
Q("fact_checking", "medium", "根据输入的 3 条来源，整合「某产品召回事件」的时间线与关键数字。", "来源 1：3 月宣布召回；来源 2：涉及 10 万台；来源 3：4 月公布原因。", "时间线：3 月宣布召回 → 4 月公布原因；关键数字：涉及 10 万台。", "role_based", 2)
Q("fact_checking", "medium", "判断输入的健康说法是否有科学依据，并给出理由。", "说法：「喝碱性水能改变体质、预防癌症」。", "无可靠科学依据：人体酸碱平衡由机体调节，饮用水的酸碱度不能显著改变血液 pH，预防癌症说法无证据支持。", "role_based", 2)
Q("fact_checking", "medium", "交叉验证输入的两个统计来源，判断数据是否一致。", "来源 A：2022 年该国 GDP 增长 3.1%；来源 B：增长 3.1%（但为初步估算）。", "一致（均为 3.1%），但来源 B 标注为初步估算，应留意后续修订。", "role_based", 2)
Q("fact_checking", "medium", "判断输入说法「维生素 C 能预防感冒」的证据强度。", "", "证据强度弱：部分研究显示维 C 可能略微缩短病程，但预防普通感冒的证据不充分。", "role_based", 2)
Q("fact_checking", "medium", "从输入的多段文本中提取关键实体（人名、地名、数字）并去重。", "甲说「张三去了北京」；乙说「张三去了上海」；丙说「费用 300 元」。", "实体：人名=张三；地名=北京、上海；数字=300 元（去重后）。", "role_based", 2)
Q("fact_checking", "medium", "判断输入标题是否为「标题党」，并改写为中性标题。", "标题：「震惊！这种食物竟然致癌！」", "是标题党（夸大、缺乏依据）。中性改写：「某食物被疑与癌症风险相关，仍需更多研究」。", "role_based", 2)
Q("fact_checking", "medium", "找出输入短文中的 2 处事实错误并纠正。", "短文：「太阳围绕地球转。光速约为 30 米/秒，水由氢和氧组成。」", "错误 1：「太阳围绕地球转」→ 应为「地球围绕太阳转」；错误 2：「光速约为 30 米/秒」→ 光速约 30 万公里/秒。", "role_based", 2)
# 困难 6
Q("fact_checking", "hard", "对输入声明逐句核查，为每句标注「属实/失实/存疑」并说明依据。", "声明：「2021 年，公司营收 10 亿，比 2020 年增长 50%，成为行业第一。」（附来源数据）", "逐句标注并给出依据，如「营收 10 亿」需核对，「增长 50%」需核对 2020 年基数，「行业第一」需权威排名佐证。", "role_based", 2)
Q("fact_checking", "hard", "综合多个来源，评估输入统计结论的可靠性（从样本、方法、利益冲突三方面）。", "结论：「90% 的用户满意该产品。」来源为厂商自测，样本 100 人。", "可靠性低：样本小、来源为利益相关方、方法不透明，存在幸存者偏差与利益冲突风险。", "role_based", 2)
Q("fact_checking", "hard", "判断输入的因果宣称是「相关」还是「因果」，并说明判断依据。", "宣称：「吃巧克力越多的人越瘦，所以巧克力导致变瘦。」", "相关而非因果：观察性相关，可能存在第三变量（如运动量）或反向因果，需随机对照实验才能推断因果。", "role_based", 2)
Q("fact_checking", "hard", "三个来源给出冲突数据，判断哪个更可信并说明理由。", "来源 A：官方统计 1.2%；来源 B：自媒体 5%；来源 C：学术论文 1.3%。", "A 与 C 接近且来源更权威，可信度高于 B（自媒体且异常偏高）。优先采信官方统计。", "role_based", 2)
Q("fact_checking", "hard", "识别输入文本中的立场偏见，指出哪些事实被选择性呈现或省略。", "文本：「该政策导致失业激增。」仅引用支持者数据。", "偏见：只呈现单一立场与支持性数据，省略反对证据、绝对数字与时间对照，用「激增」等情绪化措辞。", "role_based", 2)
Q("fact_checking", "hard", "综合多来源，写一份 100 字内的中性事实摘要，去除所有观点与评价。", "多来源混合报道（含各方观点与事实数据）。", "只保留可核实的事实（人物/时间/地点/数字），去除形容词、立场与推断，100 字内。", "role_based", 2)


# ================= 5. 创意写作 creative_writing (20) =================
# 简单 7
Q("creative_writing", "easy", "写一段 80 字左右的场景描写「雨天的咖啡馆」。", "", "评估标准：有具体感官细节（视/听/味），画面感强，语言流畅，约 80 字。", "role_based", 2)
Q("creative_writing", "easy", "为下面这个故事起 3 个候选标题，并选一个说明理由。", "故事梗概：一名邮差发现一封信永远寄不出去，于是亲自踏上送信之路。", "评估标准：3 个标题风格各异且贴合主题，理由说明清楚。", "role_based", 2)
Q("creative_writing", "easy", "写一个 100 字以内的微小说，必须包含「钥匙、信、钟表」三个元素。", "", "评估标准：三元素齐全且自然融入，有完整起承或留白，100 字内。", "role_based", 2)
Q("creative_writing", "easy", "写一段 80 字左右的人物外貌描写，体现「疲惫但倔强」。", "", "评估标准：外貌细节能传达「疲惫」与「倔强」两种气质，不直说这两个词。", "role_based", 2)
Q("creative_writing", "easy", "把下面这句平淡的话改写得更有画面感（可换措辞、增加细节）。", "他很难过地离开了。", "评估标准：改写后具象化（动作/神态/环境烘托），避免直接写「难过」。", "role_based", 2)
Q("creative_writing", "easy", "写一句 15 字以内的广告语，推广一款降噪耳机。", "", "评估标准：15 字内、朗朗上口、突出「降噪/安静」卖点。", "role_based", 2)
Q("creative_writing", "easy", "写一段 80 字左右描写「初雪」的文字。", "", "评估标准：调动多种感官，画面清新，有个人感受。", "role_based", 2)
# 中等 7
Q("creative_writing", "medium", "构思一个主题为「重逢」的短篇故事梗概，包含起承转合。", "", "评估标准：四段结构完整，有冲突与转折，结尾有情感落点。", "role_based", 2)
Q("creative_writing", "medium", "写一段两个角色的对白，体现彼此的性格冲突。", "", "评估标准：对白推动矛盾，两个角色语言风格可区分，有潜台词。", "role_based", 2)
Q("creative_writing", "medium", "为品牌「环保水杯」写 3 条不同风格的 slogan，并各说明定位。", "", "评估标准：3 条风格各异（如理性/情感/幽默），定位说明准确。", "role_based", 2)
Q("creative_writing", "medium", "以「如果城市会说话」开头，写一篇 150 字左右的短文。", "", "评估标准：延续该拟人设定，意象连贯，150 字左右，主题完整。", "role_based", 2)
Q("creative_writing", "medium", "把下面这段科普文字改写成面向 8 岁儿童的通俗版本。", "科普：光合作用是植物利用光能把二氧化碳和水转化为有机物并释放氧气的过程。", "评估标准：去术语、用比喻，儿童能听懂，核心信息不丢失。", "role_based", 2)
Q("creative_writing", "medium", "写一个 100 字左右的悬疑故事开头，制造悬念。", "", "评估标准：快速建立悬念（异常事件/未解问题），吸引继续阅读。", "role_based", 2)
Q("creative_writing", "medium", "为下面这首诗续写两行，保持韵脚与意境。", "原诗：\n夜深人静月如钩，\n独坐窗前忆旧游。", "评估标准：续写两行押韵（ou），意境延续（思念/夜/月），语言凝练。", "role_based", 2)
# 困难 6
Q("creative_writing", "hard", "构思一个「双线叙事」的故事框架，说明两条线如何最终交汇。", "", "评估标准：两条线各自独立、有内在联系，交汇点合理且有情感或主题张力。", "role_based", 2)
Q("creative_writing", "hard", "写一段 150 字左右，同时营造「温暖」与「不安」两种氛围。", "", "评估标准：两种看似矛盾的氛围并置且自然，细节能同时传递两重感受。", "role_based", 2)
Q("creative_writing", "hard", "把下面这段严肃公告改写成幽默风格，同时不丢失关键信息。", "公告：因系统维护，本平台将于明日凌晨 0:00-6:00 暂停服务，敬请谅解。", "评估标准：幽默但不失信息（时间、原因、范围均保留），语气轻松。", "role_based", 2)
Q("creative_writing", "hard", "为同一主题写 3 个不同视角的故事开头各 80 字（第一人称、第三人称限制、全知视角）。", "主题：一个人在地铁上捡到一个陌生人的手机。", "评估标准：3 个开头叙事视角清晰可辨，各 80 字左右，信息透露程度符合各视角特点。", "role_based", 2)
Q("creative_writing", "hard", "写一首短诗（不限格式），主题「时间的重量」。", "", "评估标准：有明确意象与情感，语言凝练，避免直白说教。", "role_based", 2)
Q("creative_writing", "hard", "设计一个世界观设定（200 字内），包含物理规则、社会结构、核心冲突三要素。", "", "评估标准：三要素齐备且相互自洽，200 字内，有可延展性。", "role_based", 2)


# ================= 6. 规划与决策 planning (20) =================
# 简单 7
Q("planning", "easy", "制定一个 1 天的城市一日游行程，按上午/下午/晚上三段安排并注明时间。", "", "评估标准：三段各有具体地点与大致时间，路线合理、衔接顺畅。", "hierarchical", 2)
Q("planning", "easy", "为一顿 3 人晚餐制定菜单，总预算 100 元，列出菜品与估算花费。", "", "评估标准：菜品搭配合理，花费 <=100 元且标注清楚，兼顾荤素。", "hierarchical", 2)
Q("planning", "easy", "安排一周 4 次的晨跑计划（哪天、跑多久、强度如何）。", "", "评估标准：一周 4 次分布合理，含时长与强度，有休息日。", "hierarchical", 2)
Q("planning", "easy", "制定「搬家当天」的任务清单，按执行顺序排列。", "", "评估标准：步骤有序（打包→搬运→归位→清洁等），关键事项不遗漏。", "hierarchical", 2)
Q("planning", "easy", "为一个 30 分钟的晨间高效流程排序（起床、洗漱、早餐、运动、计划当日）。", "", "评估标准：顺序合理，30 分钟内可完成，各环节时间分配现实。", "hierarchical", 2)
Q("planning", "easy", "规划一个 2 小时的学习时段，把任务拆成若干块并分配时间。", "", "评估标准：任务拆解清晰、时间分配合理、含短暂休息。", "hierarchical", 2)
Q("planning", "easy", "制定一个周末大扫除的分区计划（客厅/厨房/卧室/卫生间）。", "", "评估标准：分区明确，各区有重点与顺序，避免返工。", "hierarchical", 2)
# 中等 7
Q("planning", "medium", "为 4 人团队制定一个为期 2 周的软件开发冲刺排期，含里程碑与负责人。", "", "评估标准：有里程碑、任务拆解、负责人与时间安排，前后依赖合理。", "hierarchical", 3)
Q("planning", "medium", "规划一次 3 天 2 夜的短途旅行：交通、住宿、景点、预算四要素齐全。", "", "评估标准：四要素齐全，行程密度适中，预算合理且分项列明。", "hierarchical", 2)
Q("planning", "medium", "为「新产品上线」制定项目计划，并包含至少 3 项风险及应对。", "", "评估标准：有阶段划分，风险识别具体且有对应缓解措施。", "hierarchical", 3)
Q("planning", "medium", "设计一个兼顾工作、运动、学习、休息的每日时间表。", "", "评估标准：四类活动均覆盖，时间分配现实，有缓冲与休息。", "hierarchical", 2)
Q("planning", "medium", "为一场 30 人会议制定议程，并分配主持人/记录员/计时员等角色。", "", "评估标准：议程有主题与时间，角色分工明确，流程可控。", "hierarchical", 3)
Q("planning", "medium", "制定一个 3 个月的健身计划，含目标、分阶段安排与衡量指标。", "", "评估标准：目标可量化，分阶段（适应/提升/巩固），有衡量指标。", "hierarchical", 2)
Q("planning", "medium", "为「组织一次社区义卖」做资源与人力规划。", "", "评估标准：列出所需资源（场地/物料/宣传）与人力分工，可行。", "hierarchical", 3)
# 困难 6
Q("planning", "hard", "为一个多部门协作项目绘制甘特图，并识别关键路径。", "", "评估标准：任务依赖关系正确，识别出关键路径，瓶颈任务标注清楚。", "hierarchical", 3)
Q("planning", "hard", "为新产品制定完整「上市策略」：定价、渠道、推广、风险应对。", "", "评估标准：四维度均有可执行方案，相互协同，含风险与应对。", "hierarchical", 3)
Q("planning", "hard", "规划一次国际商务行程，在时间、成本、时差多约束下给出优化方案。", "", "评估标准：考虑多约束（航班/酒店/时差/会议），给出权衡与优化，含备选。", "hierarchical", 3)
Q("planning", "hard", "制定一个城市级应急疏散方案，含分阶段、资源调配与责任分工。", "", "评估标准：分阶段清晰，资源（交通/医疗/避难）与责任人明确，有兜底预案。", "hierarchical", 3)
Q("planning", "hard", "为「公司 3 年减少碳排放 30%」制定分年度路线图。", "", "评估标准：分年度有阶段性目标与举措，可量化，考虑成本与可行性。", "hierarchical", 3)
Q("planning", "hard", "制定一个满足多利益相关方诉求的冲突协调方案（如新建商场 vs 周边居民）。", "", "评估标准：识别各利益相关方诉求，方案有妥协机制，可落地并说明取舍。", "hierarchical", 3)


# ================= 7. 多语言翻译与本地化 translation (20) =================
# 简单 7
Q("translation", "easy", "将下面英文句子翻译成中文。", "The quick brown fox jumps over the lazy dog.", "敏捷的棕色狐狸跳过那只懒狗。", "role_based", 2)
Q("translation", "easy", "将下面中文句子翻译成英文。", "今天天气很好。", "The weather is nice today.", "role_based", 2)
Q("translation", "easy", "将「谢谢你的帮助」翻译成英文，并说明其敬语/礼貌程度。", "", "Thank you for your help.（中性、礼貌，通用场合合适）", "role_based", 2)
Q("translation", "easy", "将下面英文习语翻译成中文，并说明含义。", "break the ice", "打破僵局（直译「破冰」，指消除初次见面的尴尬）。", "role_based", 2)
Q("translation", "easy", "将下面中文问候语翻译成英文。", "你好，很高兴认识你。", "Hello, nice to meet you.", "role_based", 2)
Q("translation", "easy", "判断下面译文是否忠实于原文，并说明。", "原文：He is a big fish in a small pond.\n译文：他是小池塘里的大鱼。", "忠实：保留了原比喻，语义对应（也可意译「矮子里拔高个」，但直译不丢信息）。", "role_based", 2)
Q("translation", "easy", "将下面英文翻译成中文，注意中英语序差异。", "I will call you when I arrive.", "我到了就给你打电话。（时间状语在中文中前置）", "role_based", 2)
# 中等 7
Q("translation", "medium", "翻译下面含文化隐喻的英文，并说明该隐喻应如何处理。", "It's raining cats and dogs.", "译为「倾盆大雨」。英文隐喻不能直译，应译为中文对应表达而非字面。", "role_based", 2)
Q("translation", "medium", "将下面产品文案翻译成英文，符合英文营销语气。", "一杯好咖啡，开启美好一天。", "A great cup of coffee to start your day.", "role_based", 2)
Q("translation", "medium", "翻译下面含双关的英文，并说明双关如何处理。", "Time flies like an arrow; fruit flies like a banana.", "该句利用 flies 的双关（时间飞逝 / 果蝇喜欢）。译文难以保留双关，需说明或改写成中文可对应的俏皮话。", "role_based", 2)
Q("translation", "medium", "将下面正式邮件语句翻译成中文，保持礼貌委婉语气。", "We regret to inform you that the position has been filled.", "我们很遗憾地通知您，该职位已招满。", "role_based", 2)
Q("translation", "medium", "将下面成语翻译成英文，分别给出直译与意译。", "画蛇添足", "直译：draw a snake and add feet；意译：gild the lily / to overdo it。", "role_based", 2)
Q("translation", "medium", "将下面英文广告语本地化为中文，保持冲击力。", "Just do it.", "放手去做。/ 只管去做。（保留简洁有力、行动号召的语气）", "role_based", 2)
Q("translation", "medium", "找出下面机器翻译中的 2 处错误并修正。", "原文：The company released a new product and saw strong sales.\n机译：公司发布了一个新产品并看到了强烈的销售。", "错误 1：「看到了强烈的销售」不通顺 → 应译「销量强劲」；错误 2：「新产品」可用「新品」更自然。修正：公司发布了新品，销量强劲。", "role_based", 2)
# 困难 6
Q("translation", "hard", "翻译下面含潜在文化禁忌的文案，并说明哪些地方需要本地化调整。", "英文广告语提到特定宗教节日与猪肉类产品。", "需识别宗教/饮食禁忌：替换或中性化相关元素，说明调整理由与替代方案。", "role_based", 2)
Q("translation", "hard", "将下面文言文翻译成英文，并简要说明翻译取舍。", "学而时习之，不亦说乎？", "Is it not a pleasure to learn and practice what one has learned?（保留反问语气，sacrificing 文言韵律）", "role_based", 2)
Q("translation", "hard", "为一款 App 的 UI 文案做中文本地化（多个短句）。", "Sign in / Forgot password? / Get started", "登录 / 忘记密码？/ 开始使用（短小、符合中文 UI 习惯、语气一致）。", "role_based", 2)
Q("translation", "hard", "翻译下面法律文本，保持准确性与术语一致性。", "The parties shall be liable for any breach of this Agreement.", "任何一方违反本协议均应承担相应责任。（术语「breach=违约」「parties=当事人/各方」保持一致）", "role_based", 2)
Q("translation", "hard", "处理下面含幽默与地域梗的内容，说明你的本地化策略。", "一段含本地俚语与地域笑话的英文内容。", "策略：识别梗的类型，对可对应的用中文梗替换、不可对应的用注释或改写，避免直译失去笑点。", "role_based", 2)
Q("translation", "hard", "翻译并审校下面技术文档，统一术语。", "The cache stores frequently accessed data to reduce latency.", "缓存存储频繁访问的数据以降低延迟。（「cache」统一译「缓存」、「latency」译「延迟」）", "role_based", 2)


# ================= 8. 科学问题回答 science (20) =================
# 简单 7
Q("science", "easy", "为什么天空是蓝色的？", "", "大气分子对短波长蓝光散射更强（瑞利散射），使天空呈现蓝色。", "free_form", 2)
Q("science", "easy", "简述光合作用的基本过程。", "", "植物利用光能，将二氧化碳和水转化为有机物（葡萄糖）并释放氧气。", "free_form", 2)
Q("science", "easy", "为什么冰会浮在水面上？", "", "冰的密度小于液态水（水结冰体积膨胀），因此浮在水面。", "free_form", 2)
Q("science", "easy", "解释「惯性」，并举例说明。", "", "惯性是物体保持原有运动状态的性质；例如急刹车时乘客身体前倾。", "free_form", 2)
Q("science", "easy", "为什么会有四季？", "", "地球自转轴倾斜约 23.5°，公转时不同半球受日照角度与时长变化，形成四季。", "free_form", 2)
Q("science", "easy", "解释「蒸发」与「沸腾」的区别。", "", "蒸发是液体表面任何温度下缓慢汽化；沸腾是达到沸点时液体内部剧烈汽化的现象。", "free_form", 2)
Q("science", "easy", "为什么晚上能看到星星、白天看不到？", "", "白天太阳散射光太强，掩盖了星光；夜晚没有太阳散射光，星光可见。", "free_form", 2)
# 中等 7
Q("science", "medium", "解释「温室效应」的机制及其与全球变暖的关系。", "", "温室气体（CO2、CH4 等）吸收地表长波辐射再辐射，减少热量散失；浓度升高导致增温加剧。", "free_form", 2)
Q("science", "medium", "解释疫苗的工作原理。", "", "疫苗引入灭活/减毒抗原，激发免疫系统产生抗体和记忆细胞，使机体在真正感染时能快速应答。", "free_form", 2)
Q("science", "medium", "用通俗语言解释「量子纠缠」。", "", "两个粒子处于关联状态，无论相距多远，对一个的测量会瞬间决定另一个的状态（比喻：一双「配对」的手套）。", "free_form", 2)
Q("science", "medium", "为什么深海生物能在高压下生存？", "", "其体内压力与外界平衡，细胞膜和酶适应高压，缺乏气腔等易受压结构。", "free_form", 2)
Q("science", "medium", "解释 DNA 复制的「半保留」机制。", "", "DNA 双链解开，各以一条旧链为模板合成新链，子代 DNA 各含一条亲代链与一条新链。", "free_form", 2)
Q("science", "medium", "黑洞本身不发光，如何被观测到？", "", "通过其对周围物质的引力效应、吸积盘辐射、引力透镜及引力波间接观测。", "free_form", 2)
Q("science", "medium", "抗生素耐药性是如何产生的？", "", "抗生素形成选择压力，具有耐药突变的细菌存活并繁殖，耐药基因在种群中扩散。", "free_form", 2)
# 困难 6
Q("science", "hard", "解释相对论中的「时间膨胀」，并举一个可观测证据。", "", "高速运动或强引力场中时间流逝变慢；证据如 GPS 卫星时钟需相对论修正、宇宙线 μ 子寿命延长。", "free_form", 3)
Q("science", "hard", "解释气候变化中的多重反馈回路（至少一个正反馈、一个负反馈）。", "", "正反馈：冰融化减少反照率→升温加剧；负反馈：CO2 升高促进植物生长→吸收更多 CO2。", "free_form", 3)
Q("science", "hard", "解释 CRISPR 基因编辑的原理，并简述其主要伦理争议。", "", "Cas 蛋白在向导 RNA 引导下定点切割 DNA，借助修复机制实现编辑；争议涉及生殖系编辑、脱靶风险、设计婴儿等。", "free_form", 3)
Q("science", "hard", "列举暗物质存在的证据，并说明可能的候选粒子。", "", "证据：星系旋转曲线、引力透镜、宇宙微波背景各向异性；候选如 WIMP、轴子等。", "free_form", 2)
Q("science", "hard", "解释多普勒效应及其在宇宙学中的红移应用。", "", "波源远离时波长变长（红移）；星系红移表明宇宙在膨胀，用于测量退行速度与距离。", "free_form", 2)
Q("science", "hard", "解释混沌系统的「蝴蝶效应」及其对天气预报的影响。", "", "混沌系统对初值极端敏感，微小扰动可被放大；天气预报因此存在可预测性极限。", "free_form", 2)


# ================= 9. 辩论与观点整合 debate (20) =================
# 简单 7
Q("debate", "easy", "就「学生该不该带手机上学」分别给出一个支持和一个反对的论据。", "", "评估标准：正反各一条论据，具体且互不重叠。", "debate", 2)
Q("debate", "easy", "就「养猫还是养狗更好」分别给出一个支持和一个反对的论据。", "", "评估标准：正反各一条论据，具体且互不重叠。", "debate", 2)
Q("debate", "easy", "就「早上学习还是晚上学习更好」分别给出一个支持和一个反对的论据。", "", "评估标准：正反各一条论据，具体且互不重叠。", "debate", 2)
Q("debate", "easy", "就「纸质书 vs 电子书」分别给出一个支持和一个反对的论据。", "", "评估标准：正反各一条论据，具体且互不重叠。", "debate", 2)
Q("debate", "easy", "就「网购 vs 实体店购物」分别给出一个支持和一个反对的论据。", "", "评估标准：正反各一条论据，具体且互不重叠。", "debate", 2)
Q("debate", "easy", "就「城市生活 vs 乡村生活」分别给出一个支持和一个反对的论据。", "", "评估标准：正反各一条论据，具体且互不重叠。", "debate", 2)
Q("debate", "easy", "就「早起型 vs 晚睡型作息」分别给出一个支持和一个反对的论据。", "", "评估标准：正反各一条论据，具体且互不重叠。", "debate", 2)
# 中等 7
Q("debate", "medium", "就「是否应强制接种疫苗」分别给出至少 2 条支持与 2 条反对论据，并给出整合后的结论。", "", "评估标准：正反各 >=2 条论据，结论在权衡双方后给出，不回避矛盾。", "debate", 2)
Q("debate", "medium", "论证「人工智能取代人类工作是利大于弊还是弊大于利」，给出正反观点并总结。", "", "评估标准：正反各有实质论据，覆盖经济/社会/伦理维度，结论明确。", "debate", 3)
Q("debate", "medium", "就「远程办公应成为常态吗」给出正反论证与你的结论。", "", "评估标准：正反各有论据，结论有依据，考虑不同行业/岗位差异。", "debate", 2)
Q("debate", "medium", "就「大学教育是否应免费」给出正反论证与结论。", "", "评估标准：正反各有论据（公平 vs 财政/质量），结论明确。", "debate", 2)
Q("debate", "medium", "就「社交媒体对青少年的影响」给出正反论证与结论。", "", "评估标准：正反各有论据，涉及心理/社交/学习等维度，结论平衡。", "debate", 2)
Q("debate", "medium", "就「是否应征收碳税」给出正反论证与结论。", "", "评估标准：正反各有论据（减排激励 vs 企业成本/公平），结论明确。", "debate", 2)
Q("debate", "medium", "就「动物实验是否合理」给出正反论证与结论。", "", "评估标准：正反各有论据（科研必要性 vs 伦理），结论有立场。", "debate", 2)
# 困难 6
Q("debate", "hard", "就「人工智能武器化」进行正反论证，并识别应坚守的伦理红线。", "", "评估标准：正反论证深入，识别伦理红线（如人类决策权），有原则性结论。", "debate", 3)
Q("debate", "hard", "就「是否允许基因编辑人类胚胎」进行正反论证，并提出监管建议。", "", "评估标准：正反论证涉及伦理/医学/社会，监管建议具体可操作。", "debate", 3)
Q("debate", "hard", "就「全民基本收入」进行正反论证，并评估其可行性。", "", "评估标准：正反论据充分（减贫/激励 vs 财政/通胀），可行性评估有量化考量。", "debate", 3)
Q("debate", "hard", "就「隐私与安全之间的平衡」进行正反论证，并给出权衡原则。", "", "评估标准：正反论证深入，提出可操作的权衡原则（如比例原则）。", "debate", 3)
Q("debate", "hard", "就「是否应主动追求技术奇点」进行正反论证与结论。", "", "评估标准：正反论证有思想深度，涉及风险/收益/控制问题，结论有立场。", "debate", 2)
Q("debate", "hard", "就「自由市场 vs 政府干预」进行正反论证，并给出综合立场。", "", "评估标准：正反论证覆盖效率/公平/市场失灵，综合立场有理论依据。", "debate", 3)


# ================= 10. 工具使用与 API 调用 tool_use (20) =================
# 简单 7
Q("tool_use", "easy", "从候选工具中为下面任务选择最合适的工具，并说明理由。", "任务：查询今天某城市的天气。候选：计算器 / 天气 API / 翻译工具。", "选择「天气 API」，因为任务需要实时外部数据，其余工具不匹配。", "role_based", 2)
Q("tool_use", "easy", "解析下面 API 响应，提取 temperature 字段的值。", "{\"city\": \"北京\", \"temperature\": 28, \"condition\": \"晴\"}", "temperature = 28", "role_based", 2)
Q("tool_use", "easy", "判断下面 HTTP 状态码的含义。", "状态码 404", "404 = 资源未找到（Not Found）。", "role_based", 2)
Q("tool_use", "easy", "根据 API 错误响应判断处理方式。", "{\"error\": \"invalid_api_key\", \"code\": 401}", "401 未授权：检查并更换 API key 后重试。", "role_based", 2)
Q("tool_use", "easy", "为下面操作选择正确的 HTTP 方法。", "操作：创建一个新用户资源。", "POST（用于创建资源）。", "role_based", 2)
Q("tool_use", "easy", "根据下面 CSV 数据回答平均分是多少。", "姓名,分数\nA,80\nB,90\nC,70", "平均分 = 80", "role_based", 2)
Q("tool_use", "easy", "为下面任务选择合适的工具，并说明理由。", "任务：把「你好」翻译成英文。", "选择「翻译工具」，因为任务属于语言翻译。", "role_based", 2)
# 中等 7
Q("tool_use", "medium", "设计一个调用链：先查天气 API，再根据结果决定是否调用提醒 API。", "需求：若明天下雨则提醒带伞。", "调用链：1) 调用天气 API 获取明日降水概率；2) 若 >阈值则调用提醒 API 发送「记得带伞」。", "role_based", 2)
Q("tool_use", "medium", "解析分页 API 响应，判断如何获取下一页。", "{\"items\": [...], \"next_cursor\": \"abc123\"}", "用 next_cursor 作为下一页参数继续请求，直到无 next_cursor。", "role_based", 2)
Q("tool_use", "medium", "遇到 API 限流（429）时给出重试策略。", "状态码 429", "采用指数退避重试（如等待 1s、2s、4s 再试），并尊重 Retry-After 头。", "role_based", 2)
Q("tool_use", "medium", "组合两个 API：先地理编码（地名→坐标），再查天气，说明数据如何传递。", "地名「上海」", "1) 地理编码 API(上海) → 返回经纬度；2) 把经纬度传给天气 API → 返回天气。", "role_based", 2)
Q("tool_use", "medium", "从嵌套 JSON 中提取深层字段 user.profile.city。", "{\"user\": {\"profile\": {\"city\": \"杭州\"}}}", "user.profile.city = 杭州", "role_based", 2)
Q("tool_use", "medium", "根据错误码诊断 API 调用失败是参数错误还是权限问题。", "{\"code\": 403, \"message\": \"forbidden\"}", "403 = 权限问题（有身份但无权限），应检查权限/scope 而非参数。", "role_based", 2)
Q("tool_use", "medium", "为下面任务选择合适的工具并说明理由。", "任务：计算 12345 的平方根。候选：数据库查询 / 计算器 / 网络搜索。", "选择「计算器」，因为是确定性数学计算，无需外部数据或检索。", "role_based", 2)
# 困难 6
Q("tool_use", "hard", "设计一个多步骤 Agent 工具调用流程，含条件分支与错误重试。", "任务：根据用户问题，先检索文档，若无结果则联网搜索，调用失败则重试。", "流程：检索 → 若有结果返回；无结果 → 搜索 → 失败重试（最多 N 次）→ 整合返回。", "role_based", 3)
Q("tool_use", "hard", "解析一个流式（分块）API 响应，说明如何拼接。", "响应分块：\"{\\\"data\\\": \"hel\"}\", \"{\\\"data\\\": \"lo\"}\"", "按顺序拼接各块的 data 字段，最终得到 hello；需处理 chunk 边界。", "role_based", 2)
Q("tool_use", "hard", "处理 API 幂等性问题：设计一个安全的重试/去重方案。", "任务：确保「下单」请求只执行一次，即使网络重试。", "为每次请求生成幂等键（idempotency-key），服务端按键去重，重试携带同键。", "role_based", 2)
Q("tool_use", "hard", "设计多个工具调用的并行与依赖关系（哪些可并行、哪些必须串行）。", "工具：查天气、查交通、查餐厅、生成出行建议（依赖前三者）。", "天气/交通/餐厅三者相互独立可并行；「生成出行建议」依赖前三者结果，必须串行最后执行。", "role_based", 3)
Q("tool_use", "hard", "说明 OAuth 授权码流程获取 access token 的步骤。", "客户端已注册并有 client_id/secret。", "步骤：1) 重定向用户到授权页；2) 用户授权后回调携带 code；3) 用 code 换 token；4) 用 token 访问资源。", "role_based", 2)
Q("tool_use", "hard", "为一个复杂任务设计完整的工具调用方案（多工具组合 + 结果整合）。", "任务：策划周末出行（查天气、查景点、查交通，最终给建议）。", "方案：并行调用天气/景点/交通 API → 汇总各结果 → 按约束（天气好则户外、交通便利）生成建议。", "role_based", 3)


def build():
    per_cat = {c: 0 for c in CATEGORIES}
    out = []
    for (cat, diff, desc, inp, exp, ctype, n) in _RAW:
        per_cat[cat] += 1
        out.append({
            "task_id": f"{cat}_{per_cat[cat]:02d}",
            "category": cat,
            "difficulty": diff,
            "description": desc,
            "input": inp,
            "expected_output": exp,
            "collaboration_required": True,
            "collaboration_type": ctype,
            "num_agents_suggested": n,
        })
    return out, per_cat


def main():
    out, per_cat = build()
    target = Path(__file__).resolve().parent.parent / "data" / "MultiAgentCollabBench.json"
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"已生成 {len(out)} 题 -> {target}")
    for c in CATEGORIES:
        print(f"  {c}: {per_cat[c]}")


if __name__ == "__main__":
    main()
