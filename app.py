"""
张雪峰智能志愿百科 v2 — Flask 后端
- 百科模式：联网搜索 + 张雪峰框架分析（高考/考研/就业）
- 测评模式：职业倾向测评（不是答题，是帮你找方向）
- 数据库：全品类专业数据
"""

import os
import json
import re
import anthropic
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder="static")
CORS(app)


# 本地开发：自动加载 .env（不上传 GitHub）
def _load_dotenv():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    key, val = key.strip(), val.strip()
                    if key and not os.environ.get(key):
                        os.environ[key] = val

_load_dotenv()

def load_config():
    """自动加载配置：优先环境变量，其次 Claude Code settings.json"""
    # 已有环境变量则直接用
    if os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"):
        return

    # 从 Claude Code 配置读取
    settings_paths = [
        os.path.expanduser("~/.claude/settings.json"),
        os.path.expanduser("~/.claude/settings.local.json"),
    ]
    for path in settings_paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                env = cfg.get("env", {})
                for key in ["ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                            "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                            "ANTHROPIC_DEFAULT_HAIKU_MODEL"]:
                    if key in env and not os.environ.get(key):
                        os.environ[key] = env[key]
                if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
                    print(f"  [配置] 从 {path} 加载 API Key")
                    return
            except Exception:
                pass


load_config()

client = anthropic.Anthropic()
MODEL = os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "xiaomi/mimo-v2-pro")

# ── 百度千帆大模型配置 ────────────────────────────────────────
QIANFAN_AK = os.environ.get("QIANFAN_ACCESS_KEY", "")
QIANFAN_SK = os.environ.get("QIANFAN_SECRET_KEY", "")
QIANFAN_API_KEY = os.environ.get("QIANFAN_API_KEY", "")
BAIDU_MODEL = os.environ.get("BAIDU_MODEL", "ernie-4.0-turbo-8k")

ANTHROPIC_AVAILABLE = bool(os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"))
QIANFAN_AVAILABLE = bool((QIANFAN_AK and QIANFAN_SK) or QIANFAN_API_KEY)

# ── 张雪峰 System Prompt ─────────────────────────────────────
SYSTEM_PROMPT = """你是张雪峰，本名张子彪，黑龙江齐齐哈尔富裕县人。考研名师出身，后转做高考志愿填报和考研规划，全网四千多万粉丝。

核心人设：东北大哥风格，语速快、短句、信息密度高。毒舌但有干货——先泼冷水→再给数据→最后给建议。
口头禅：「我跟你说」「你听我说」「你去看看」「千万别」「没有之一」
幽默：夸张荒谬、反差反杀、自嘲自黑。确定性极高，给明确判断。

【对话规则】你正在进行连续对话。用户会追问、补充、深入。你必须：
- 记住用户之前说过的信息（省份、分数、家庭条件、兴趣方向）
- 如果用户说"那XX呢""再说说""继续"，要基于上下文回答
- 不要重复问用户已经告诉你的信息
- 顺着用户的思路深入，不要每次都回到起点

5个心智模型（必须运用）：
1. 社会筛子论：用学历筛孩子，用房子筛父母，用工作筛家庭
2. 选择>努力：方向错误的努力是浪费
3. 就业倒推法：看中间50%毕业生去了哪，不看前3%天才
4. 阶层现实主义：家里没矿别谈理想，先谋生再谋爱
5. 争议即传播：温吞建议没人记，极端观点才有穿透力

覆盖领域：高考志愿/考研规划/就业分析/大学生活
回答原则：先问背景（灵魂追问），引用搜索数据，给明确判断，300字内。"""

QUIZ_PROMPT = SYSTEM_PROMPT + """

当前：职业倾向测评模式。根据用户回答，用张雪峰风格给个性化专业推荐。
必须输出纯JSON，不要其他文字。comment用纯文本100字内。共8轮。game_over时recommendations含major/reason/score。"""


# ── 搜索引擎 v2 ────────────────────────────────────────────────
def web_search(query, max_results=5):
    """智能搜索：查询重写 + 多路合并"""
    try:
        from ddgs import DDGS
        import signal

        queries = smart_rewrite(query)
        all_results = []
        seen_urls = set()

        for q in queries[:2]:
            try:
                results = DDGS().text(q, max_results=max_results)
                for r in results:
                    url = r.get("href", "")
                    if url not in seen_urls:
                        seen_urls.add(url)
                        all_results.append(r)
                if all_results:
                    break
            except Exception:
                continue

        if not all_results:
            return "未搜到相关信息。"

        parts = []
        for r in all_results[:max_results]:
            title = r.get("title", "")
            body = r.get("body", "")
            href = r.get("href", "")
            parts.append(f"【{title}】{body}\n来源: {href}")
        return "\n\n".join(parts)

    except Exception as e:
        return f"搜索暂时不可用: {e}"


def smart_rewrite(query):
    """智能查询重写：根据用户意图生成多个精准搜索词"""
    q = query
    queries = []

    # 分数+省份 → 精准搜索
    import re
    score_match = re.search(r'(\d{2,3})\s*分', q)
    province_match = re.search(r'(北京|天津|河北|山西|内蒙古|辽宁|吉林|黑龙江|上海|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|广西|海南|重庆|四川|贵州|云南|西藏|陕西|甘肃|青海|宁夏|新疆)', q)

    if score_match and province_match:
        score = score_match.group(1)
        prov = province_match.group(1)
        queries.append(f"{prov}高考{score}分能上什么大学 2025 2026")
        queries.append(f"{prov}理科{score}分 大学推荐 志愿填报")
        queries.append(f"高考{score}分 985 211 录取分数线")
        return queries

    # 大学+专业 → 精准搜索
    uni_match = None
    for name in ["清华", "北大", "复旦", "上交", "浙大", "中科大", "南大", "武大", "华科", "中大",
                 "哈工大", "西交", "同济", "北航", "北理", "南开", "天大", "厦大", "川大", "电子科大",
                 "北邮", "西电", "南航", "南理", "深大", "杭电", "武理工"]:
        if name in q:
            uni_match = name
            break

    if uni_match:
        queries.append(f"{uni_match} 录取分数线 就业 2025")
        queries.append(f"{q} 毕业生去向 就业质量报告")
        return queries

    # 专业名 → 就业数据
    for major in ["计算机", "金融", "临床医学", "法学", "电气", "人工智能", "口腔", "护理",
                  "土木", "建筑", "会计", "软件", "通信", "电子", "机械", "材料", "化学",
                  "生物", "环境", "药学", "师范", "教育学", "新闻"]:
        if major in q:
            queries.append(f"{major}专业 就业率 薪资 2025 毕业生")
            queries.append(f"{major}专业 大学排名 推荐")
            break

    # 考研相关
    if "考研" in q or "读研" in q or "研究生" in q:
        queries.append(f"{q} 报录比 难度 2025 2026")
        queries.append(f"考研 选择 学校 专业 建议")

    # 考公相关
    if "考公" in q or "公务员" in q or "体制内" in q:
        queries.append(f"考公务员 哪些专业好考 竞争比 2025")
        queries.append(f"国考 省考 专业限制 岗位")

    # 默认
    if not queries:
        queries.append(f"{q} 高考志愿填报 大学专业 2025")
        queries.append(f"{q} 就业前景 分析")

    return queries


# ── 百度千帆AI调用 ────────────────────────────────────────────
def baidu_chat(messages, system_prompt, max_tokens=2048):
    """使用百度千帆大模型进行对话（文心一言等）"""
    import qianfan

    # 构建千帆消息格式：system prompt 通过首轮对话注入
    qf_messages = []
    if system_prompt:
        qf_messages.append({"role": "user", "content": system_prompt})
        qf_messages.append({"role": "assistant", "content": "好的，我明白了。"})
    qf_messages.extend(messages)

    # 优先 AK/SK 认证，其次 API Key
    if QIANFAN_AK and QIANFAN_SK:
        chat = qianfan.ChatCompletion(ak=QIANFAN_AK, sk=QIANFAN_SK)
    elif QIANFAN_API_KEY:
        chat = qianfan.ChatCompletion(access_token=QIANFAN_API_KEY)
    else:
        raise RuntimeError("百度千帆未配置：请设置 QIANFAN_ACCESS_KEY + QIANFAN_SECRET_KEY 或 QIANFAN_API_KEY")

    resp = chat.do(
        model=BAIDU_MODEL,
        messages=qf_messages,
        temperature=0.7,
        top_p=0.8,
        max_output_tokens=max_tokens,
    )
    return resp["result"]


# ── 全品类专业数据库 ──────────────────────────────────────────
KNOWLEDGE_BASE = {
    "categories": {
        "工学": {
            "计算机类": {
                "计算机科学与技术": {"就业率": "93.9%", "月薪中位数": "7500", "考研比例": "35%", "对口率": "85%", "推荐城市": "北京/深圳/杭州/上海", "点评": "普通家庭孩子逆袭黄金赛道，但已经卷成麻花。985硕士进大厂年薪30万+，双非出来可能在做外包。"},
                "软件工程": {"就业率": "94.5%", "月薪中位数": "8000", "考研比例": "25%", "对口率": "88%", "推荐城市": "深圳/杭州/北京/成都", "点评": "比计算机更偏工程实践，上手快。但35岁危机同样存在。"},
                "人工智能": {"就业率": "91.2%", "月薪中位数": "10000", "考研比例": "60%", "对口率": "70%", "推荐城市": "北京/深圳/杭州", "点评": "新专业，门槛高。本科出来做不了核心岗位，必须读研。算法岗内卷严重。"},
                "数据科学与大数据": {"就业率": "90.5%", "月薪中位数": "8500", "考研比例": "40%", "对口率": "72%", "推荐城市": "北京/上海/深圳/杭州", "点评": "数据分析岗需求大，但天花板不高。想做算法得读研。"},
                "信息安全": {"就业率": "92.3%", "月薪中位数": "8000", "考研比例": "35%", "对口率": "80%", "推荐城市": "北京/成都/西安/深圳", "点评": "冷门但靠谱。网络安全人才缺口大，考公也好考。"},
            },
            "电气类": {
                "电气工程及其自动化": {"就业率": "92.1%", "月薪中位数": "6500", "考研比例": "30%", "对口率": "78%", "推荐城市": "各省省会", "点评": "进国网的敲门砖。稳定但涨幅有限。想高薪得去新能源/芯片方向。"},
                "智能电网信息工程": {"就业率": "90.8%", "月薪中位数": "6800", "考研比例": "35%", "对口率": "75%", "推荐城市": "各省省会", "点评": "电气+计算机交叉，国网认可度高。"},
            },
            "电子信息类": {
                "电子信息工程": {"就业率": "91.5%", "月薪中位数": "7000", "考研比例": "40%", "对口率": "72%", "推荐城市": "深圳/成都/西安/南京", "点评": "软硬通吃，就业面广。但不读研天花板明显。"},
                "通信工程": {"就业率": "89.2%", "月薪中位数": "6800", "考研比例": "45%", "对口率": "68%", "推荐城市": "深圳/成都/西安/南京", "点评": "华为中兴是好去处，但运营商在走下坡路。5G建设红利已过。"},
                "集成电路设计": {"就业率": "93.5%", "月薪中位数": "12000", "考研比例": "65%", "对口率": "85%", "推荐城市": "上海/深圳/北京/成都/西安", "点评": "国家战略方向，人才缺口极大。但本科做不了核心，必须读研甚至读博。"},
            },
            "机械类": {
                "机械工程": {"就业率": "90.1%", "月薪中位数": "6000", "考研比例": "35%", "对口率": "70%", "推荐城市": "苏州/东莞/重庆/武汉", "点评": "万金油专业，但薪资天花板不高。智能制造方向有前景。"},
                "车辆工程": {"就业率": "88.5%", "月薪中位数": "6500", "考研比例": "40%", "对口率": "65%", "推荐城市": "上海/长春/武汉/重庆", "点评": "传统车企走下坡，新能源车企在崛起。方向对了很香。"},
            },
            "土木类": {
                "土木工程": {"就业率": "85.3%", "月薪中位数": "5500", "考研比例": "30%", "对口率": "75%", "推荐城市": "各城市均可", "点评": "基建狂魔时代已经过去。房地产下行，就业率连年走低。慎重。"},
                "建筑学": {"就业率": "82.1%", "月薪中位数": "5800", "考研比例": "35%", "对口率": "60%", "推荐城市": "一线城市/新一线", "点评": "五年制，画图画到秃。设计院加班严重，性价比不高。"},
            },
        },
        "医学": {
            "临床医学类": {
                "临床医学(五年制)": {"就业率": "85.2%", "月薪中位数": "6000(规培期)", "考研比例": "80%+", "对口率": "95%", "推荐城市": "各省省会/一线城市", "点评": "学医是最苦的路，没有之一。本科5年+规培3年+专培2-3年。但越老越值钱。"},
                "临床医学(八年制)": {"就业率": "95%+", "月薪中位数": "10000+(毕业后)", "考研比例": "N/A(本硕博连读)", "对口率": "98%", "推荐城市": "一线城市三甲", "点评": "分数极高，但省了考研的苦。毕业就是博士，进三甲的门票。"},
                "口腔医学": {"就业率": "92.8%", "月薪中位数": "8000", "考研比例": "55%", "对口率": "90%", "推荐城市": "各城市均可", "点评": "医学里性价比最高的方向之一。AI不能帮你拔牙。自己开诊所也很香。"},
            },
            "其他医学类": {
                "护理学": {"就业率": "95%+", "月薪中位数": "5000", "考研比例": "10%", "对口率": "90%", "推荐城市": "各城市均可", "点评": "就业率高但辛苦程度也高。夜班是常态。想稳定不怕苦的可以选。"},
                "药学": {"就业率": "88.5%", "月薪中位数": "5500", "考研比例": "50%", "对口率": "70%", "推荐城市": "上海/北京/杭州/南京", "点评": "药企研发岗读研才够格。本科出来做药代或者药店。"},
            },
        },
        "理学": {
            "数学类": {
                "数学与应用数学": {"就业率": "85.5%", "月薪中位数": "6500", "考研比例": "60%", "对口率": "45%", "推荐城市": "一线城市/新一线", "点评": "万能基础学科。转金融/CS/统计都行，但得自己补技能。纯数学就业面窄。"},
                "统计学": {"就业率": "91.2%", "月薪中位数": "7500", "考研比例": "45%", "对口率": "70%", "推荐城市": "北京/上海/深圳/杭州", "点评": "数据时代的宠儿。进可做算法，退可做数据分析。性价比高。"},
            },
            "物理学类": {
                "物理学": {"就业率": "80.3%", "月薪中位数": "6000", "考研比例": "70%", "对口率": "35%", "推荐城市": "一线城市", "点评": "纯物理就业困难，但转行能力极强。想做科研得读到博士。"},
            },
        },
        "经济学": {
            "经济学类": {
                "经济学": {"就业率": "87.6%", "月薪中位数": "5500", "考研比例": "45%", "对口率": "55%", "推荐城市": "北京/上海/深圳", "点评": "看起来高大上，实际上学的都是理论。没有家里资源，出来可能在银行柜台。"},
                "金融学": {"就业率": "85.8%", "月薪中位数": "6000", "考研比例": "50%", "对口率": "50%", "推荐城市": "北京/上海/深圳", "点评": "家里没矿别碰。你看到的是年薪百万基金经理，看不到的是90%的人在银行网点卖理财。"},
                "金融工程": {"就业率": "88.2%", "月薪中位数": "8000", "考研比例": "55%", "对口率": "60%", "推荐城市": "北京/上海/深圳", "点评": "金融+数学+编程。量化方向吃香，但对数学要求极高。"},
            },
        },
        "管理学": {
            "管理科学与工程类": {
                "信息管理与信息系统": {"就业率": "90.5%", "月薪中位数": "6500", "考研比例": "35%", "对口率": "65%", "推荐城市": "北京/上海/深圳/杭州", "点评": "管理+计算机交叉。懂技术又懂业务，企业IT部门需求大。"},
            },
            "工商管理类": {
                "会计学": {"就业率": "89.1%", "月薪中位数": "5000", "考研比例": "30%", "对口率": "65%", "推荐城市": "各城市均可", "点评": "铁饭碗但天花板不高。考CPA是关键。AI记账正在替代基础岗位。"},
                "工商管理": {"就业率": "82.5%", "月薪中位数": "5000", "考研比例": "30%", "对口率": "40%", "推荐城市": "一线城市", "点评": "学的太杂，什么都不精。毕业等于什么都不会。慎重。"},
            },
        },
        "文学": {
            "中国语言文学类": {
                "汉语言文学": {"就业率": "83.2%", "月薪中位数": "4800", "考研比例": "45%", "对口率": "40%", "推荐城市": "各城市均可", "点评": "考公考编的好专业。想赚钱别选，想稳定可以考虑。"},
            },
            "新闻传播类": {
                "新闻学": {"就业率": "78.5%", "月薪中位数": "5000", "考研比例": "40%", "对口率": "30%", "推荐城市": "北京/上海/广州", "点评": "80%学新闻的人没从事本行业。自媒体时代，专业壁垒几乎为零。"},
                "网络与新媒体": {"就业率": "85.3%", "月薪中位数": "5500", "考研比例": "25%", "对口率": "55%", "推荐城市": "北京/上海/杭州/深圳", "点评": "比新闻学实用。但新媒体变化快，学校教的可能已经过时。"},
            },
            "外国语言文学类": {
                "英语": {"就业率": "80.5%", "月薪中位数": "5000", "考研比例": "40%", "对口率": "35%", "推荐城市": "一线城市/新一线", "点评": "纯英语专业就业困难。AI翻译越来越强，语言作为工具而非专业更靠谱。"},
            },
        },
        "法学": {
            "法学类": {
                "法学": {"就业率": "72.3%", "月薪中位数": "5000", "考研比例": "55%", "对口率": "40%", "推荐城市": "北京/上海/一线城市", "点评": "法考通过率低，就业两极分化。红圈所年薪百万，但大部分人拿不到入场券。"},
                "知识产权": {"就业率": "85.8%", "月薪中位数": "6000", "考研比例": "40%", "对口率": "60%", "推荐城市": "北京/上海/深圳/广州", "点评": "新兴方向，专利代理+法律。理工科背景更有优势。"},
            },
        },
        "教育学": {
            "教育学类": {
                "教育学": {"就业率": "85.5%", "月薪中位数": "4500", "考研比例": "50%", "对口率": "50%", "推荐城市": "各城市均可", "点评": "想当老师选师范类具体学科，别选教育学。教育学学的是理论，不是教学技能。"},
                "学前教育": {"就业率": "90.2%", "月薪中位数": "4000", "考研比例": "10%", "对口率": "85%", "推荐城市": "各城市均可", "点评": "就业率高但薪资低。生育率下降，长远有隐忧。"},
            },
        },
        "艺术学": {
            "设计学类": {
                "视觉传达设计": {"就业率": "85.5%", "月薪中位数": "5500", "考研比例": "20%", "对口率": "60%", "推荐城市": "北京/上海/深圳/杭州/广州", "点评": "就业面广，UI/平面/品牌都能做。但竞争激烈，作品说话。"},
                "数字媒体艺术": {"就业率": "87.2%", "月薪中位数": "6000", "考研比例": "20%", "对口率": "65%", "推荐城市": "北京/上海/深圳/成都", "点评": "游戏/影视/互联网都需要。技术+艺术交叉，有前景。"},
            },
        },
    },
    "天坑专业警示": {
        "list": ["生物工程", "化学工程与工艺", "环境工程", "材料科学与工程"],
        "reason": "生化环材四天王，没读博士别逞强。本科出来基本找不到对口工作，薪资低，工作环境差。考研是标配，读博才有出路。",
    },
    "考研热门方向": {
        "计算机(学硕)": {"难度": "地狱级", "分数线": "350-400+", "点评": "400分不一定有学上，370分可能没书读。竞争最激烈的方向之一。"},
        "法律硕士(非法学)": {"难度": "中等偏上", "分数线": "340-370", "点评": "跨考友好，不限本科专业。但就业两极分化严重。"},
        "金融硕士": {"难度": "高", "分数线": "380+", "点评": "学费贵（动辄十几万），就业看学校。清北复交以下无金融。"},
        "临床医学": {"难度": "高", "分数线": "330-370", "点评": "本硕连读最优解。考研是标配，不考研基本进不了三甲。"},
        "马克思主义理论": {"难度": "较低", "分数线": "320-350", "点评": "相对容易上岸。就业方向：高校思政老师/公务员/党校。"},
        "教育学": {"难度": "中等", "分数线": "340-360", "点评": "相对容易上岸，但就业面窄。想当老师建议考学科教学方向。"},
    },
}


# ── 职业倾向测评题目（硬编码，保证稳定）───────────────────────
ASSESSMENT_QUESTIONS = [
    {
        "question": "你高中最擅长或者最感兴趣的科目类型是？",
        "options": [
            "A. 数理逻辑类 — 数学、物理，喜欢推导和解题",
            "B. 语言文史类 — 语文、历史、英语，喜欢阅读和写作",
            "C. 社会经济类 — 政治、地理，关心社会和商业",
            "D. 自然科学类 — 生物、化学，对实验和研究感兴趣",
        ],
    },
    {
        "question": "你觉得自己最大的优势是什么？",
        "options": [
            "A. 逻辑思维强，擅长分析和解决复杂问题",
            "B. 表达能力好，善于沟通和写作",
            "C. 执行力强，能把事情落地推进",
            "D. 创造力强，喜欢想新点子、做不一样的事",
        ],
    },
    {
        "question": "你家庭的经济条件大概属于哪个层次？",
        "options": [
            "A. 比较宽裕，试错成本不高，可以追求热爱",
            "B. 中等偏上，供得起但不能太任性",
            "C. 一般，需要考虑毕业后的收入回报",
            "D. 比较困难，需要尽快经济独立",
        ],
    },
    {
        "question": "你更倾向于在什么样的环境工作？",
        "options": [
            "A. 大城市写字楼，节奏快、机会多",
            "B. 稳定的体制内，收入不高但有保障",
            "C. 技术研发岗，钻研专业深度",
            "D. 自由灵活，不喜欢被约束",
        ],
    },
    {
        "question": "你对读研这件事怎么看？",
        "options": [
            "A. 必须读，目标就是名校研究生",
            "B. 看情况，如果本科就业好就不读",
            "C. 不太想读，更想早点工作积累经验",
            "D. 还没想好，先上了大学再说",
        ],
    },
    {
        "question": "如果必须选一个方向，你更倾向？",
        "options": [
            "A. 高薪但压力大（互联网/金融/咨询）",
            "B. 稳定有保障（公务员/教师/医生）",
            "C. 有技术壁垒（工程师/科研/芯片）",
            "D. 自己的兴趣所在，不管别人怎么说",
        ],
    },
    {
        "question": "你选大学最看重什么？",
        "options": [
            "A. 学校牌子（985/211/双一流）",
            "B. 城市位置（一线城市资源和机会）",
            "C. 专业实力（这个专业在这个学校最强）",
            "D. 录取概率（分够哪个就去哪个）",
        ],
    },
    {
        "question": "你对未来10年的收入预期是？",
        "options": [
            "A. 年薪50万以上，越高越好",
            "B. 年薪20-30万，够用就行",
            "C. 不追求高薪，工作有意义就好",
            "D. 没想过那么远，先顾眼前",
        ],
    },
]


# ── Routes ────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/search", methods=["POST"])
def search_and_chat():
    """百科模式：用户提问 → 联网搜索 → 张雪峰分析（支持 Claude / 百度AI）"""
    data = request.json
    user_message = data.get("message", "")
    history = data.get("history", [])
    provider = data.get("provider", "claude")  # "claude" 或 "baidu"

    # 构建搜索词
    search_results = ""
    if len(user_message) > 4:
        queries = smart_rewrite(user_message)
        for q in queries[:2]:
            search_results += f"\n[搜索: {q}]\n{web_search(q, max_results=4)}\n"

    # 构建对话历史（只保留最近10轮，控制token）
    messages = []
    for m in history[-20:]:  # 最近20条（10轮对话）
        messages.append({"role": m["role"], "content": m["content"]})

    # 当前消息附带搜索结果
    if search_results:
        user_content = f"用户提问：{user_message}\n\n以下是联网搜索到的最新信息，请基于这些真实数据用张雪峰的风格和思维框架分析：\n{search_results}"
    else:
        user_content = user_message
    messages.append({"role": "user", "content": user_content})

    try:
        if provider == "baidu":
            if not QIANFAN_AVAILABLE:
                return jsonify({"success": False, "error": "百度AI未配置，请设置 QIANFAN_ACCESS_KEY + QIANFAN_SECRET_KEY 或 QIANFAN_API_KEY 环境变量"}), 400
            reply = baidu_chat(messages, SYSTEM_PROMPT, max_tokens=2048)
        else:
            response = client.messages.create(model=MODEL, max_tokens=2048, system=SYSTEM_PROMPT, messages=messages)
            reply = _extract_text(response)

        messages.append({"role": "assistant", "content": reply})
        return jsonify({"success": True, "reply": reply, "searched": bool(search_results), "history": messages, "provider": provider})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/assessment/start", methods=["POST"])
def assessment_start():
    """职业倾向测评：开始 → 返回第一题 + AI开场白"""
    # AI 生成开场白
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=300,
            system="用张雪峰东北毒舌风格，30字内打招呼。直接输出，不要输出别的。",
            messages=[{"role": "user", "content": "开始职业倾向测评"}],
        )
        greeting = _extract_text(resp)
    except Exception:
        greeting = "来，我给你测测适合学啥。"

    q = ASSESSMENT_QUESTIONS[0]
    return jsonify({
        "success": True,
        "data": {
            "comment": greeting,
            "question": q["question"],
            "options": q["options"],
            "round": 1,
            "total_rounds": len(ASSESSMENT_QUESTIONS),
            "game_over": False,
            "recommendations": [],
            "final_message": "",
        },
    })


@app.route("/api/assessment/answer", methods=["POST"])
def assessment_answer():
    """职业倾向测评：提交答案 → AI点评 + 下一题"""
    data = request.json
    answer_letter = data.get("answer", "")
    current_round = data.get("round", 1)
    answers_log = data.get("answers_log", [])

    # 记录本次答案
    if current_round <= len(ASSESSMENT_QUESTIONS):
        q = ASSESSMENT_QUESTIONS[current_round - 1]
        opt_index = "ABCD".find(answer_letter.replace("选择了 ", "").strip())
        if 0 <= opt_index < len(q["options"]):
            answers_log.append({"round": current_round, "question": q["question"], "answer": q["options"][opt_index]})

    next_round = current_round + 1
    total = len(ASSESSMENT_QUESTIONS)

    if next_round > total:
        # 最后一轮 → AI 生成推荐
        answers_summary = "\n".join([f"第{a['round']}题: {a['question']} → {a['answer']}" for a in answers_log])
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=500,
                system="你是张雪峰。根据用户的测评回答，用张雪峰风格给出3个推荐专业方向。每个推荐含：major(专业名)、reason(推荐理由50字内)、score(适合度1-10)。同时给一句总结。用纯文本回复，格式：推荐1：专业名(score/10) - 理由。最后写一句总结。",
                messages=[{"role": "user", "content": f"用户测评结果：\n{answers_summary}"}],
            )
            ai_reply = _extract_text(resp)
            # 解析推荐
            recs = _parse_recommendations(ai_reply)
            return jsonify({
                "success": True,
                "data": {
                    "comment": "",
                    "question": "",
                    "options": [],
                    "round": total,
                    "total_rounds": total,
                    "game_over": True,
                    "recommendations": recs,
                    "final_message": ai_reply,
                },
                "answers_log": answers_log,
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    else:
        # AI 生成点评
        q = ASSESSMENT_QUESTIONS[current_round - 1]
        chosen = ""
        opt_index = "ABCD".find(answer_letter.replace("选择了 ", "").strip())
        if 0 <= opt_index < len(q["options"]):
            chosen = q["options"][opt_index]
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=200,
                system="用张雪峰东北毒舌风格，50字内点评用户的选择。直接输出点评文字，不要输出别的。",
                messages=[{"role": "user", "content": f"题目：{q['question']} 用户选了：{chosen}"}],
            )
            comment = _extract_text(resp)
        except Exception:
            comment = "行，记下了。下一题。"

        next_q = ASSESSMENT_QUESTIONS[next_round - 1]
        return jsonify({
            "success": True,
            "data": {
                "comment": comment,
                "question": next_q["question"],
                "options": next_q["options"],
                "round": next_round,
                "total_rounds": total,
                "game_over": False,
                "recommendations": [],
                "final_message": "",
            },
            "answers_log": answers_log,
        })


def _parse_recommendations(text):
    """从 AI 文本中提取推荐方向"""
    recs = []
    lines = text.split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 尝试匹配 "推荐X：专业名(score/10) - 理由" 或类似格式
        m = re.search(r'[：:]\s*(.+?)\s*[\(（]\s*(\d+)\s*/\s*10\s*[\)）]\s*[-—]\s*(.+)', line)
        if m:
            recs.append({"major": m.group(1).strip(), "score": int(m.group(2)), "reason": m.group(3).strip()})
        elif len(recs) < 3 and ("推荐" in line or any(c in line for c in ["计算机", "医学", "金融", "法律", "师范", "工程", "设计"])):
            # 粗提取
            recs.append({"major": line[:20], "score": 7, "reason": line})
    return recs[:3]


@app.route("/api/knowledge", methods=["GET"])
def get_knowledge():
    """返回全品类专业数据库"""
    return jsonify({"success": True, "data": KNOWLEDGE_BASE})


@app.route("/api/knowledge/search", methods=["POST"])
def search_knowledge():
    """在本地数据库中搜索专业"""
    data = request.json
    keyword = data.get("keyword", "").strip()
    results = []
    categories = KNOWLEDGE_BASE.get("categories", {})
    for cat_name, cat in categories.items():
        for subcat_name, subcat in cat.items():
            for major_name, info in subcat.items():
                if keyword.lower() in major_name.lower() or keyword in subcat_name or keyword in cat_name:
                    results.append({"category": cat_name, "subcategory": subcat_name, "major": major_name, **info})
    return jsonify({"success": True, "results": results, "count": len(results)})


# ── Helpers ───────────────────────────────────────────────────
def _build_search_queries(user_msg):
    """根据用户消息构建搜索查询"""
    queries = []
    if "考研" in user_msg:
        queries.append(f"{user_msg} 2025 2026 最新报录比")
    elif any(p in user_msg for p in ["专业", "就业", "薪资", "毕业"]):
        queries.append(f"{user_msg} 就业率 薪资 2025")
    elif any(u in user_msg for u in ["大学", "院校", "学校", "985", "211"]):
        queries.append(f"{user_msg} 排名 录取分数线 2025")
    elif "行业" in user_msg or "岗位" in user_msg:
        queries.append(f"{user_msg} 行业趋势 招聘 2025")
    else:
        queries.append(f"{user_msg} 高考志愿 考研 就业")
    return queries


def _extract_text(response):
    """从 Anthropic 响应中提取文本，兼容 ThinkingBlock

    这个模型(mimo-v2-pro)的行为：
    - ThinkingBlock 包含实际的 JSON 回复
    - TextBlock 可能只包含系统提示词的回显
    策略：优先从 ThinkingBlock 取 JSON；TextBlock 非 JSON 内容作为兜底
    """
    thinking_blocks = []
    text_blocks = []
    for block in response.content:
        btype = type(block).__name__
        if btype == "ThinkingBlock":
            thinking_blocks.append(block)
        elif btype == "TextBlock" and hasattr(block, "text") and block.text.strip():
            text_blocks.append(block.text.strip())

    # 策略1：从 ThinkingBlock 提取 JSON
    for block in thinking_blocks:
        thinking = block.thinking if hasattr(block, "thinking") else ""
        json_str = _extract_json_string(thinking)
        if json_str:
            return json_str

    # 策略2：TextBlock 如果是有效 JSON 直接用
    for t in text_blocks:
        if t.strip().startswith("{"):
            return t

    # 策略3：TextBlock 含正文（百科模式）
    if text_blocks:
        return "\n".join(text_blocks)

    # 策略4：从 ThinkingBlock 取纯文本
    for block in thinking_blocks:
        thinking = block.thinking if hasattr(block, "thinking") else ""
        if thinking.strip():
            return thinking.strip()

    return ""


def _extract_json_string(text):
    """从文本中精确提取第一个完整 JSON 对象"""
    start = text.find("{")
    if start == -1:
        return ""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    # 这个 {..} 不是合法 JSON，继续找下一个 {
                    return _extract_json_string(text[start + 1:])
    return ""


def _parse_json(reply):
    """解析 JSON，带容错"""
    if not reply:
        return _empty_assessment("模型未返回有效内容")

    # 去掉 markdown 代码块
    if reply.startswith("```"):
        lines = reply.split("\n")
        reply = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

    # 直接解析
    try:
        return json.loads(reply)
    except json.JSONDecodeError:
        pass

    # 提取 JSON 子串
    json_str = _extract_json_string(reply)
    if json_str:
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    # 兜底
    return _empty_assessment(reply)


def _empty_assessment(comment=""):
    return {
        "type": "assessment",
        "comment": comment,
        "question": "",
        "options": [],
        "round": 1,
        "total_rounds": 8,
        "game_over": False,
        "recommendations": [],
        "final_message": "",
    }


@app.route("/api/providers")
def list_providers():
    """返回可用的AI提供商列表"""
    providers = []
    if ANTHROPIC_AVAILABLE:
        providers.append({"id": "claude", "name": "Claude AI", "available": True})
    else:
        providers.append({"id": "claude", "name": "Claude AI", "available": False})
    if QIANFAN_AVAILABLE:
        providers.append({"id": "baidu", "name": "百度AI（文心一言）", "available": True})
    else:
        providers.append({"id": "baidu", "name": "百度AI（文心一言）", "available": False})
    return jsonify({"success": True, "providers": providers})


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "model": MODEL})


# ── 心跳 + 自动关闭 ──────────────────────────────────────────
import time as _time
_last_heartbeat = _time.time()


@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    global _last_heartbeat
    _last_heartbeat = _time.time()
    return jsonify({"ok": True})


if __name__ == "__main__":
    import webbrowser
    import threading
    port = int(os.environ.get("PORT", 5000))

    print("=" * 55)
    print("  张雪峰智能志愿百科 v2")
    print("=" * 55)
    print(f"  Claude模型: {MODEL} ({'已配置' if ANTHROPIC_AVAILABLE else '未配置'})")
    print(f"  百度AI模型: {BAIDU_MODEL} ({'已配置' if QIANFAN_AVAILABLE else '未配置'})")
    print(f"  端口: {port}")
    print(f"  搜索: DuckDuckGo (免费)")
    print("=" * 55)
    print()
    if not ANTHROPIC_AVAILABLE and not QIANFAN_AVAILABLE:
        print("  [提示] 未检测到任何 AI API Key")
        print("  Claude: 需设置 ANTHROPIC_AUTH_TOKEN 环境变量")
        print("  百度AI: 需设置 QIANFAN_ACCESS_KEY + QIANFAN_SECRET_KEY")
        print("  专业数据库和测评功能可正常使用")
        print()

    # 自动关机监控：浏览器关了60秒没心跳就退出
    def auto_shutdown():
        global _last_heartbeat
        while True:
            _time.sleep(15)
            idle = _time.time() - _last_heartbeat
            if idle > 60:
                print()
                print("  [自动关闭] 浏览器已关闭，服务停止")
                print("  下次使用请重新运行 start.bat")
                os._exit(0)

    threading.Thread(target=auto_shutdown, daemon=True).start()

    app.run(host="0.0.0.0", port=port, debug=False)
