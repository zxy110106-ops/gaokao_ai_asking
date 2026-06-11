"""
张雪峰智能志愿百科 v2 — 自包含版
====================================
一个文件，包含后端+前端+知识库+测评系统。

启动方法：
  1. 安装依赖：pip install flask flask-cors anthropic ddgs
  2. 运行：python zxf.py
  3. 浏览器自动打开 http://localhost:5000

或双击 启动.bat（Windows）
====================================
"""

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

app = Flask(__name__, static_folder="static")
CORS(app)

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


# ── 搜索引擎 ──────────────────────────────────────────────────
def web_search(query, max_results=5):
    """DDGS 搜索，返回摘要文本"""
    try:
        from ddgs import DDGS
        results = DDGS().text(query, max_results=max_results)
        if not results:
            return "未找到相关结果。"
        parts = []
        for r in results:
            title = r.get("title", "")
            body = r.get("body", "")
            href = r.get("href", "")
            parts.append(f"【{title}】{body}\n来源: {href}")
        return "\n\n".join(parts)
    except Exception as e:
        return f"搜索暂时不可用: {e}"


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
EMBEDDED_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>张雪峰 · 智能志愿百科</title>
<meta name="description" content="用张雪峰的思维框架分析高考志愿、考研规划和就业方向。职业倾向测评帮你找对方向，38+专业数据库随查随用。">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>峰</text></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;700;900&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#1a1a1e;--bg2:#242428;--steel:#2e2e34;
  --yellow:#F5A623;--orange:#E85D26;--safety:#FFD23F;
  --blue:#4A90D9;--green:#2ECC71;--red:#E74C3C;
  --text:#D4D0C8;--text-bright:#F0EDE6;--text-dim:#7A7670;
  --grid:rgba(245,166,35,0.04);
  --mono:'Space Mono','Courier New',monospace;
  --sans:'Noto Sans SC','PingFang SC','Microsoft YaHei',sans-serif;
  --r:4px;
}
html{font-size:16px;scroll-behavior:smooth}
body{font-family:var(--sans);background:var(--bg);color:var(--text);min-height:100vh;overflow-x:hidden;position:relative}
body::before{content:'';position:fixed;inset:0;z-index:0;pointer-events:none;background:linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);background-size:40px 40px}
body::after{content:'';position:fixed;inset:0;z-index:0;pointer-events:none;background:radial-gradient(ellipse at 30% 20%,rgba(245,166,35,0.06),transparent 60%),radial-gradient(ellipse at 80% 80%,rgba(74,144,217,0.04),transparent 50%)}
.grain{position:fixed;inset:0;z-index:9999;pointer-events:none;opacity:.03;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")}
.app{position:relative;z-index:1;max-width:820px;margin:0 auto;padding:0 20px 60px}

/* ── Splash ─────────────────────────────────── */
.splash{position:fixed;inset:0;z-index:10000;background:var(--bg);display:flex;flex-direction:column;align-items:center;justify-content:center;transition:opacity .5s,visibility .5s}
.splash.hide{opacity:0;visibility:hidden;pointer-events:none}
.splash h2{font-size:2.4rem;font-weight:900;color:var(--text-bright);margin-bottom:8px;letter-spacing:4px}
.splash h2 span{color:var(--yellow)}
.splash .tag{font-family:var(--mono);font-size:.72rem;color:var(--text-dim);letter-spacing:3px;margin-bottom:28px}
.splash .feats{display:flex;gap:20px;margin-bottom:36px;flex-wrap:wrap;justify-content:center}
.splash .f{text-align:center;padding:14px 18px;border:1px solid var(--steel);background:var(--bg2);min-width:150px}
.splash .f .ic{font-size:1.4rem;margin-bottom:4px}
.splash .f .lb{font-family:var(--mono);font-size:.68rem;color:var(--yellow);letter-spacing:1px}
.splash .f .ds{font-size:.78rem;color:var(--text-dim);margin-top:3px}
.splash .go{background:var(--yellow);color:var(--bg);border:none;padding:14px 44px;font-family:var(--mono);font-size:.85rem;font-weight:700;letter-spacing:3px;cursor:pointer;transition:all .2s}
.splash .go:hover{background:var(--safety);transform:translateY(-2px)}
.splash .note{margin-top:20px;font-size:.68rem;color:var(--text-dim);font-family:var(--mono);letter-spacing:1px}

/* ── Header ─────────────────────────────────── */
.header{padding:40px 0 24px;border-bottom:2px solid var(--yellow);margin-bottom:20px;position:relative}
.header::after{content:'';position:absolute;bottom:-2px;left:0;width:100px;height:2px;background:var(--orange)}
.header .tag{font-family:var(--mono);font-size:.68rem;color:var(--yellow);letter-spacing:3px;margin-bottom:6px;display:flex;align-items:center;gap:8px}
.header .tag::before{content:'';width:7px;height:7px;background:var(--yellow);display:inline-block;animation:blink 2s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
.header h1{font-size:2.6rem;font-weight:900;color:var(--text-bright);letter-spacing:4px;line-height:1.1}
.header h1 span{color:var(--yellow)}
.header .sub{font-family:var(--mono);font-size:.72rem;color:var(--text-dim);margin-top:8px;letter-spacing:1px}
.header .qt{margin-top:12px;padding:10px 14px;border-left:3px solid var(--orange);background:rgba(232,93,38,0.06);font-size:.85rem;line-height:1.6;font-style:italic}
.header .qt .src{display:block;margin-top:4px;font-style:normal;font-size:.72rem;color:var(--text-dim)}

/* ── Tabs ───────────────────────────────────── */
.tabs{display:flex;margin-bottom:20px;border:1px solid var(--steel);background:var(--bg2)}
.tab{flex:1;padding:13px 0;text-align:center;cursor:pointer;font-family:var(--mono);font-size:.78rem;font-weight:700;letter-spacing:2px;color:var(--text-dim);background:transparent;border:none;transition:all .2s}
.tab:hover{color:var(--text);background:rgba(255,255,255,.02)}
.tab.active{color:var(--bg);background:var(--yellow)}

/* ── Panels ─────────────────────────────────── */
.panel{display:none}.panel.active{display:flex;flex-direction:column;min-height:55vh}

/* ── Chat ───────────────────────────────────── */
.chat{flex:1;overflow-y:auto;padding:8px 0 16px;scrollbar-width:thin;scrollbar-color:var(--steel) transparent}
.msg{display:flex;gap:10px;margin-bottom:14px;animation:up .35s ease-out;max-width:100%}
@keyframes up{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
.msg.user{flex-direction:row-reverse}
.av{width:38px;height:38px;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:.75rem;font-family:var(--mono);font-weight:700}
.av.ai{background:var(--yellow);color:var(--bg);clip-path:polygon(0 0,100% 0,100% 75%,75% 100%,0 100%)}
.av.me{background:var(--blue);color:#fff;clip-path:polygon(0 0,100% 0,100% 100%,25% 100%,0 75%)}
.bub{background:var(--bg2);border:1px solid var(--steel);padding:12px 16px;max-width:80%;line-height:1.8;font-size:.9rem}
.msg.user .bub{background:rgba(74,144,217,0.08);border-color:rgba(74,144,217,0.2)}

/* ── Input ──────────────────────────────────── */
.input-bar{display:flex;border:1px solid var(--steel);background:var(--bg2);margin-top:8px}
.input-bar input{flex:1;background:transparent;border:none;padding:13px 16px;color:var(--text-bright);font-family:var(--sans);font-size:.9rem;outline:none}
.input-bar input::placeholder{color:var(--text-dim)}
.send-btn{background:var(--yellow);color:var(--bg);border:none;padding:13px 22px;font-family:var(--mono);font-size:.78rem;font-weight:700;letter-spacing:2px;cursor:pointer;transition:background .15s}
.send-btn:hover{background:var(--safety)}

/* ── AI Selector ────────────────────────────── */
.ai-selector{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.ai-selector .ai-label{font-family:var(--mono);font-size:.65rem;color:var(--text-dim);letter-spacing:1px}
.ai-selector select{background:var(--bg2);border:1px solid var(--steel);color:var(--text-bright);padding:6px 12px;border-radius:6px;font-size:.78rem;font-family:var(--sans);cursor:pointer;outline:none;transition:border-color .2s}
.ai-selector select:focus{border-color:var(--yellow)}
.ai-selector select option{background:var(--bg2);color:var(--text-bright)}

/* ── Chips ───────────────────────────────────── */
.chips{display:flex;flex-wrap:wrap;gap:7px;margin:12px 0}
.chip{background:var(--steel);border:1px solid rgba(245,166,35,0.15);padding:7px 13px;font-size:.78rem;color:var(--text-dim);cursor:pointer;transition:all .2s}
.chip:hover{border-color:var(--yellow);color:var(--yellow);background:rgba(245,166,35,0.06)}

/* ── Loading ────────────────────────────────── */
.ld{display:flex;align-items:center;gap:8px;padding:6px 0;color:var(--text-dim);font-family:var(--mono);font-size:.78rem}
.ld .bars{display:flex;gap:2px;align-items:end;height:14px}
.ld .bars span{width:3px;background:var(--yellow);animation:bar .8s infinite ease-in-out}
.ld .bars span:nth-child(1){animation-delay:0s;height:7px}
.ld .bars span:nth-child(2){animation-delay:.15s;height:12px}
.ld .bars span:nth-child(3){animation-delay:.3s;height:9px}
.ld .bars span:nth-child(4){animation-delay:.45s;height:14px}
@keyframes bar{0%,100%{transform:scaleY(.5);opacity:.4}50%{transform:scaleY(1);opacity:1}}

/* ── Quiz ───────────────────────────────────── */
.quiz-card{background:var(--bg2);border:1px solid var(--steel);padding:18px;margin:10px 0;animation:up .4s ease-out}
.quiz-card h3{font-family:var(--mono);font-size:.72rem;color:var(--yellow);letter-spacing:2px;margin-bottom:12px}
.opts{display:flex;flex-direction:column;gap:7px}
.opt{display:flex;align-items:center;gap:12px;background:rgba(255,255,255,.015);border:1px solid var(--steel);padding:12px 14px;cursor:pointer;transition:all .2s;font-size:.88rem;color:var(--text);width:100%;text-align:left;font-family:var(--sans)}
.opt:hover:not(.disabled){border-color:var(--yellow);background:rgba(245,166,35,0.04);transform:translateX(4px)}
.opt .ltr{width:30px;height:30px;display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-weight:700;font-size:.78rem;background:var(--steel);color:var(--text-dim);flex-shrink:0;transition:all .2s}
.opt:hover:not(.disabled) .ltr{background:var(--yellow);color:var(--bg)}
.opt.sel{border-color:var(--yellow);background:rgba(245,166,35,0.06)}
.opt.sel .ltr{background:var(--yellow);color:var(--bg)}
.opt.disabled{cursor:default;opacity:.5}

/* ── Stats ──────────────────────────────────── */
.stats{display:flex;gap:20px;padding:10px 0;font-family:var(--mono);font-size:.72rem}
.stats .s{display:flex;flex-direction:column;gap:2px}
.stats .s .l{color:var(--text-dim);letter-spacing:2px}
.stats .s .v{font-size:1.1rem;font-weight:700;color:var(--yellow)}
.prog{display:flex;gap:5px;margin:8px 0}
.pd{width:22px;height:3px;background:var(--steel);transition:all .3s}
.pd.on{background:var(--yellow);width:30px}
.pd.done{background:var(--green)}

/* ── Recs ───────────────────────────────────── */
.rec-grid{display:flex;flex-direction:column;gap:8px;margin-top:10px}
.rec-card{background:var(--bg2);border:1px solid var(--yellow);padding:14px;position:relative;overflow:hidden}
.rec-card::before{content:'';position:absolute;top:0;left:0;width:3px;height:100%;background:var(--yellow)}
.rec-card .rm{font-weight:900;font-size:1rem;color:var(--text-bright);margin-bottom:4px}
.rec-card .rr{font-size:.82rem;color:var(--text);line-height:1.6}
.rec-card .rs{position:absolute;top:10px;right:14px;font-family:var(--mono);font-size:1.3rem;font-weight:700;color:var(--yellow)}

/* ── Data ───────────────────────────────────── */
.dsearch{display:flex;border:1px solid var(--steel);background:var(--bg2);margin-bottom:14px}
.dsearch input{flex:1;background:transparent;border:none;padding:11px 14px;color:var(--text-bright);font-family:var(--sans);font-size:.88rem;outline:none}
.dsearch button{background:var(--yellow);color:var(--bg);border:none;padding:11px 18px;font-family:var(--mono);font-size:.72rem;font-weight:700;cursor:pointer}
.dsec{margin-bottom:16px;border:1px solid var(--steel);background:var(--bg2)}
.dsec .dh{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid var(--steel);cursor:pointer}
.dsec .dh:hover{background:rgba(255,255,255,.02)}
.dsec .dh h4{font-family:var(--mono);font-size:.78rem;color:var(--yellow);letter-spacing:2px}
.dsec .dh .ar{color:var(--text-dim);transition:transform .2s;font-size:.78rem}
.dsec.open .dh .ar{transform:rotate(180deg)}
.dbody{display:none}.dsec.open .dbody{display:block}
.dtbl{width:100%;border-collapse:collapse;font-size:.8rem}
.dtbl th{text-align:left;padding:9px 10px;font-family:var(--mono);font-size:.68rem;color:var(--text-dim);letter-spacing:1px;border-bottom:1px solid var(--steel);background:rgba(0,0,0,.15);position:sticky;top:0}
.dtbl td{padding:8px 10px;border-bottom:1px solid rgba(46,46,52,.5);color:var(--text);vertical-align:top}
.dtbl tr:hover td{background:rgba(245,166,35,0.03)}
.dtbl .mn{font-weight:700;color:var(--text-bright)}
.dtbl .good{color:var(--green)}.dtbl .warn{color:var(--red)}
.dtbl .note{font-size:.75rem;color:var(--text-dim);line-height:1.5;max-width:260px}
.wbox{background:rgba(231,76,60,0.06);border:1px solid rgba(231,76,60,0.3);border-left:3px solid var(--red);padding:12px 14px;margin-bottom:14px}
.wbox h4{color:var(--red);font-family:var(--mono);font-size:.78rem;letter-spacing:2px;margin-bottom:4px}
.wbox p{font-size:.82rem;line-height:1.6}

/* ── Share ──────────────────────────────────── */
.share-btn{position:fixed;bottom:16px;right:16px;z-index:100;background:var(--steel);border:1px solid var(--yellow);color:var(--yellow);padding:8px 14px;font-family:var(--mono);font-size:.68rem;letter-spacing:1px;cursor:pointer;transition:all .2s}
.share-btn:hover{background:var(--yellow);color:var(--bg)}

/* ── Footer ─────────────────────────────────── */
.footer{text-align:center;padding:28px 0 8px;font-family:var(--mono);font-size:.62rem;color:var(--text-dim);letter-spacing:2px}
.footer a{color:var(--yellow);text-decoration:none}

/* ── Toast ──────────────────────────────────── */
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:var(--bg2);border:1px solid var(--yellow);padding:9px 18px;font-family:var(--mono);font-size:.8rem;z-index:100;animation:ti .3s}
@keyframes ti{from{opacity:0;transform:translateX(-50%) translateY(14px)}}

/* ── Mobile ─────────────────────────────────── */
@media(max-width:600px){
  .header h1{font-size:1.7rem;letter-spacing:2px}
  .app{padding:0 10px 32px}
  .bub{max-width:92%;padding:10px 12px}
  .tab{font-size:.68rem;letter-spacing:1px;padding:11px 0}
  .splash h2{font-size:1.7rem}
  .splash .feats{gap:10px}
  .splash .f{min-width:110px;padding:10px}
  .share-btn{bottom:10px;right:10px;padding:7px 10px}
  .dtbl{font-size:.72rem}
  .dtbl th,.dtbl td{padding:6px 7px}
}
</style>
</head>
<body>
<div class="grain"></div>

<!-- Splash -->
<div class="splash" id="splash">
  <h2>张<span>雪峰</span>说</h2>
  <div class="tag">SYS://INTELLIGENT-CAREER-ADVISOR // V2</div>
  <div class="feats">
    <div class="f"><div class="ic"></div><div class="lb">百科问答</div><div class="ds">38+专业数据库</div></div>
    <div class="f"><div class="ic"></div><div class="lb">职业测评</div><div class="ds">8题找到方向</div></div>
    <div class="f"><div class="ic"></div><div class="lb">就业数据</div><div class="ds">9大类全覆盖</div></div>
  </div>
  <button class="go" onclick="enterApp()">ENTER SYSTEM</button>
  <div class="note">基于张雪峰5大心智模型 · 纯前端 · 零依赖</div>
</div>

<div class="app">

  <div class="header">
    <div class="tag">SYS://ZHANG-XUEFENG-V2</div>
    <h1>张<span>雪峰</span>说</h1>
    <div class="sub">// 选择比努力更重要，但"有得选"的前提是你足够努力。</div>
    <div class="qt">
      「社会就是一个大筛子，用学历筛孩子，用房子筛父母，用工作筛家庭。」
      <span class="src">—— 张雪峰，黑龙江齐齐哈尔人，考研名师，全网4000万粉丝</span>
    </div>
  </div>

  <div class="tabs">
    <div class="tab active" onclick="switchTab('chat')" id="tChat">百科问答</div>
    <div class="tab" onclick="switchTab('assess')" id="tAssess">职业测评</div>
    <div class="tab" onclick="switchTab('data')" id="tData">专业数据库</div>
  </div>

  <!-- CHAT -->
  <div class="panel active" id="pChat">
    <div class="chat" id="chatBox">
      <div class="msg"><div class="av ai">峰</div><div class="bub">我跟你说，这个系统<strong>不是闹着玩的</strong>。38+专业就业数据，张雪峰框架帮你分析。<br><br>高考志愿、考研规划、就业方向——随便问，我给你<strong>掰扯明白</strong>。<br><br><span style="color:var(--text-dim);font-size:.78rem">试试下面的问题 ↓</span></div></div>
      <div class="chips" id="chips">
        <span class="chip" onclick="ask(this)">计算机专业就业前景</span>
        <span class="chip" onclick="ask(this)">双非考研985值不值</span>
        <span class="chip" onclick="ask(this)">生化环材真的很坑吗</span>
        <span class="chip" onclick="ask(this)">文科生选什么专业好</span>
        <span class="chip" onclick="ask(this)">考公还是考研</span>
        <span class="chip" onclick="ask(this)">金融还值得学吗</span>
        <span class="chip" onclick="ask(this)">临床医学5年还是8年</span>
        <span class="chip" onclick="ask(this)">AI会取代哪些专业</span>
      </div>
    </div>
    <div class="ai-selector">
      <span class="ai-label">AI引擎</span>
      <select id="aiProvider" onchange="switchProvider()">
        <option value="claude">Claude AI</option>
        <option value="baidu" disabled>百度AI（未配置）</option>
      </select>
    </div>
    <div class="input-bar">
      <input type="text" id="chatIn" placeholder="问张雪峰任何关于高考/考研/就业的问题..." onkeydown="if(event.key==='Enter')send()">
      <button class="send-btn" onclick="send()">SEND</button>
    </div>
  </div>

  <!-- ASSESS -->
  <div class="panel" id="pAssess">
    <div class="stats" id="aStats" style="display:none">
      <div class="s"><span class="l">ROUND</span><span class="v" id="aR">1/8</span></div>
      <div class="s"><span class="l">进度</span><span class="v" id="aP">0%</span></div>
    </div>
    <div class="prog" id="aDots" style="display:none"></div>
    <div class="chat" id="assChat">
      <div class="msg"><div class="av ai">峰</div><div class="bub">来做一套<strong>职业倾向测评</strong>。不是考你知道多少，是帮你<strong>找到方向</strong>。<br><br>8个问题，测完我给你推荐专业。</div></div>
      <div class="chips"><span class="chip" onclick="startAss()">开始测评</span></div>
    </div>
  </div>

  <!-- DATA -->
  <div class="panel" id="pData">
    <div class="dsearch">
      <input type="text" id="dIn" placeholder="搜索专业：计算机、金融、临床..." onkeydown="if(event.key==='Enter')searchD()">
      <button onclick="searchD()">SEARCH</button>
    </div>
    <div id="dCon"><div style="text-align:center;padding:36px;color:var(--text-dim);font-family:var(--mono);font-size:.78rem">LOADING DATABASE...</div></div>
  </div>

  <div class="footer">POWERED BY <a href="https://github.com/ZhangYuanJie-SJTU/zhangxuefeng-quiz" target="_blank">ZHANGXUEFENG-QUIZ</a> · ZHANGXUEFENG.SKILL</div>
</div>

<button class="share-btn" onclick="shareIt()" id="shareBtn" style="display:none">SHARE</button>

<script>
// ═══════════════════════════════════════════════
// 张雪峰智能志愿百科 · 纯前端版
// ═══════════════════════════════════════════════

// ── 知识库 ──────────────────────────────────────
const DB={
"计算机科学与技术":{cat:"工学/计算机类",rate:"93.9%",salary:"7500",kaoyan:"35%",match:"85%",city:"北京/深圳/杭州/上海",tip:"普通家庭孩子逆袭黄金赛道，但已经卷成麻花。985硕士进大厂年薪30万+，双非出来可能在做外包。"},
"软件工程":{cat:"工学/计算机类",rate:"94.5%",salary:"8000",kaoyan:"25%",match:"88%",city:"深圳/杭州/北京/成都",tip:"比计算机更偏工程实践，上手快。但35岁危机同样存在。"},
"人工智能":{cat:"工学/计算机类",rate:"91.2%",salary:"10000",kaoyan:"60%",match:"70%",city:"北京/深圳/杭州",tip:"新专业，门槛高。本科出来做不了核心岗位，必须读研。算法岗内卷严重。"},
"数据科学与大数据":{cat:"工学/计算机类",rate:"90.5%",salary:"8500",kaoyan:"40%",match:"72%",city:"北京/上海/深圳/杭州",tip:"数据分析岗需求大，但天花板不高。想做算法得读研。"},
"信息安全":{cat:"工学/计算机类",rate:"92.3%",salary:"8000",kaoyan:"35%",match:"80%",city:"北京/成都/西安/深圳",tip:"冷门但靠谱。网络安全人才缺口大，考公也好考。"},
"电气工程及其自动化":{cat:"工学/电气类",rate:"92.1%",salary:"6500",kaoyan:"30%",match:"78%",city:"各省省会",tip:"进国网的敲门砖。稳定但涨幅有限。想高薪得去新能源/芯片方向。"},
"电子信息工程":{cat:"工学/电子信息类",rate:"91.5%",salary:"7000",kaoyan:"40%",match:"72%",city:"深圳/成都/西安/南京",tip:"软硬通吃，就业面广。但不读研天花板明显。"},
"通信工程":{cat:"工学/电子信息类",rate:"89.2%",salary:"6800",kaoyan:"45%",match:"68%",city:"深圳/成都/西安/南京",tip:"华为中兴是好去处，但运营商在走下坡路。5G建设红利已过。"},
"集成电路设计":{cat:"工学/电子信息类",rate:"93.5%",salary:"12000",kaoyan:"65%",match:"85%",city:"上海/深圳/北京/成都/西安",tip:"国家战略方向，人才缺口极大。但本科做不了核心，必须读研甚至读博。"},
"机械工程":{cat:"工学/机械类",rate:"90.1%",salary:"6000",kaoyan:"35%",match:"70%",city:"苏州/东莞/重庆/武汉",tip:"万金油专业，但薪资天花板不高。智能制造方向有前景。"},
"车辆工程":{cat:"工学/机械类",rate:"88.5%",salary:"6500",kaoyan:"40%",match:"65%",city:"上海/长春/武汉/重庆",tip:"传统车企走下坡，新能源车企在崛起。方向对了很香。"},
"土木工程":{cat:"工学/土木类",rate:"85.3%",salary:"5500",kaoyan:"30%",match:"75%",city:"各城市均可",tip:"基建狂魔时代已经过去。房地产下行，就业率连年走低。慎重。"},
"建筑学":{cat:"工学/土木类",rate:"82.1%",salary:"5800",kaoyan:"35%",match:"60%",city:"一线城市/新一线",tip:"五年制，画图画到秃。设计院加班严重，性价比不高。"},
"临床医学(五年制)":{cat:"医学/临床医学类",rate:"85.2%",salary:"6000(规培期)",kaoyan:"80%+",match:"95%",city:"各省省会/一线城市",tip:"学医是最苦的路，没有之一。本科5年+规培3年+专培2-3年。但越老越值钱。"},
"口腔医学":{cat:"医学/临床医学类",rate:"92.8%",salary:"8000",kaoyan:"55%",match:"90%",city:"各城市均可",tip:"医学里性价比最高之一。AI不能帮你拔牙。自己开诊所也很香。"},
"护理学":{cat:"医学/其他",rate:"95%",salary:"5000",kaoyan:"10%",match:"90%",city:"各城市均可",tip:"就业率高但辛苦程度也高。夜班是常态。想稳定不怕苦的可以选。"},
"药学":{cat:"医学/其他",rate:"88.5%",salary:"5500",kaoyan:"50%",match:"70%",city:"上海/北京/杭州/南京",tip:"药企研发岗读研才够格。本科出来做药代或者药店。"},
"数学与应用数学":{cat:"理学/数学类",rate:"85.5%",salary:"6500",kaoyan:"60%",match:"45%",city:"一线城市/新一线",tip:"万能基础学科。转金融/CS/统计都行，但得自己补技能。纯数学就业面窄。"},
"统计学":{cat:"理学/数学类",rate:"91.2%",salary:"7500",kaoyan:"45%",match:"70%",city:"北京/上海/深圳/杭州",tip:"数据时代的宠儿。进可做算法，退可做数据分析。性价比高。"},
"物理学":{cat:"理学/物理学类",rate:"80.3%",salary:"6000",kaoyan:"70%",match:"35%",city:"一线城市",tip:"纯物理就业困难，但转行能力极强。想做科研得读到博士。"},
"经济学":{cat:"经济学/经济学类",rate:"87.6%",salary:"5500",kaoyan:"45%",match:"55%",city:"北京/上海/深圳",tip:"看起来高大上，实际上学的都是理论。没有家里资源，出来可能在银行柜台。"},
"金融学":{cat:"经济学/经济学类",rate:"85.8%",salary:"6000",kaoyan:"50%",match:"50%",city:"北京/上海/深圳",tip:"家里没矿别碰。你看到的是年薪百万基金经理，看不到的是90%的人在银行网点卖理财。"},
"金融工程":{cat:"经济学/经济学类",rate:"88.2%",salary:"8000",kaoyan:"55%",match:"60%",city:"北京/上海/深圳",tip:"金融+数学+编程。量化方向吃香，但对数学要求极高。"},
"会计学":{cat:"管理学/工商管理类",rate:"89.1%",salary:"5000",kaoyan:"30%",match:"65%",city:"各城市均可",tip:"铁饭碗但天花板不高。考CPA是关键。AI记账正在替代基础岗位。"},
"工商管理":{cat:"管理学/工商管理类",rate:"82.5%",salary:"5000",kaoyan:"30%",match:"40%",city:"一线城市",tip:"学的太杂，什么都不精。毕业等于什么都不会。慎重。"},
"信息管理与信息系统":{cat:"管理学/管工类",rate:"90.5%",salary:"6500",kaoyan:"35%",match:"65%",city:"北京/上海/深圳/杭州",tip:"管理+计算机交叉。懂技术又懂业务，企业IT部门需求大。"},
"汉语言文学":{cat:"文学/中国语言文学类",rate:"83.2%",salary:"4800",kaoyan:"45%",match:"40%",city:"各城市均可",tip:"考公考编的好专业。想赚钱别选，想稳定可以考虑。"},
"新闻学":{cat:"文学/新闻传播类",rate:"78.5%",salary:"5000",kaoyan:"40%",match:"30%",city:"北京/上海/广州",tip:"80%学新闻的人没从事本行业。自媒体时代，专业壁垒几乎为零。"},
"网络与新媒体":{cat:"文学/新闻传播类",rate:"85.3%",salary:"5500",kaoyan:"25%",match:"55%",city:"北京/上海/杭州/深圳",tip:"比新闻学实用。但新媒体变化快，学校教的可能已经过时。"},
"英语":{cat:"文学/外国语言文学类",rate:"80.5%",salary:"5000",kaoyan:"40%",match:"35%",city:"一线城市/新一线",tip:"纯英语专业就业困难。AI翻译越来越强，语言作为工具而非专业更靠谱。"},
"法学":{cat:"法学/法学类",rate:"72.3%",salary:"5000",kaoyan:"55%",match:"40%",city:"北京/上海/一线城市",tip:"法考通过率低，就业两极分化。红圈所年薪百万，但大部分人拿不到入场券。"},
"知识产权":{cat:"法学/法学类",rate:"85.8%",salary:"6000",kaoyan:"40%",match:"60%",city:"北京/上海/深圳/广州",tip:"新兴方向，专利代理+法律。理工科背景更有优势。"},
"教育学":{cat:"教育学/教育学类",rate:"85.5%",salary:"4500",kaoyan:"50%",match:"50%",city:"各城市均可",tip:"想当老师选师范类具体学科，别选教育学。教育学学的是理论，不是教学技能。"},
"学前教育":{cat:"教育学/教育学类",rate:"90.2%",salary:"4000",kaoyan:"10%",match:"85%",city:"各城市均可",tip:"就业率高但薪资低。生育率下降，长远有隐忧。"},
"视觉传达设计":{cat:"艺术学/设计学类",rate:"85.5%",salary:"5500",kaoyan:"20%",match:"60%",city:"北京/上海/深圳/杭州/广州",tip:"就业面广，UI/平面/品牌都能做。但竞争激烈，作品说话。"},
"数字媒体艺术":{cat:"艺术学/设计学类",rate:"87.2%",salary:"6000",kaoyan:"20%",match:"65%",city:"北京/上海/深圳/成都",tip:"游戏/影视/互联网都需要。技术+艺术交叉，有前景。"},
};

// ── 测评题目 ────────────────────────────────────
const QS=[
{q:"你高中最擅长或者最感兴趣的科目类型是？",o:["A. 数理逻辑类 — 数学、物理，喜欢推导和解题","B. 语言文史类 — 语文、历史、英语，喜欢阅读和写作","C. 社会经济类 — 政治、地理，关心社会和商业","D. 自然科学类 — 生物、化学，对实验和研究感兴趣"]},
{q:"你觉得自己最大的优势是什么？",o:["A. 逻辑思维强，擅长分析和解决复杂问题","B. 表达能力好，善于沟通和写作","C. 执行力强，能把事情落地推进","D. 创造力强，喜欢想新点子、做不一样的事"]},
{q:"你家庭的经济条件大概属于哪个层次？",o:["A. 比较宽裕，试错成本不高","B. 中等偏上，供得起但不能太任性","C. 一般，需要考虑毕业后的收入回报","D. 比较困难，需要尽快经济独立"]},
{q:"你更倾向于在什么样的环境工作？",o:["A. 大城市写字楼，节奏快、机会多","B. 稳定的体制内，收入不高但有保障","C. 技术研发岗，钻研专业深度","D. 自由灵活，不喜欢被约束"]},
{q:"你对读研这件事怎么看？",o:["A. 必须读，目标就是名校研究生","B. 看情况，如果本科就业好就不读","C. 不太想读，更想早点工作积累经验","D. 还没想好，先上了大学再说"]},
{q:"如果必须选一个方向，你更倾向？",o:["A. 高薪但压力大（互联网/金融/咨询）","B. 稳定有保障（公务员/教师/医生）","C. 有技术壁垒（工程师/科研/芯片）","D. 自己的兴趣所在，不管别人怎么说"]},
{q:"你选大学最看重什么？",o:["A. 学校牌子（985/211/双一流）","B. 城市位置（一线城市资源和机会）","C. 专业实力（这个专业在这个学校最强）","D. 录取概率（分够哪个就去哪个）"]},
{q:"你对未来10年的收入预期是？",o:["A. 年薪50万以上，越高越好","B. 年薪20-30万，够用就行","C. 不追求高薪，工作有意义就好","D. 没想过那么远，先顾眼前"]},
];

// ── 张雪峰点评库 ────────────────────────────────
const COMMENTS=[
"行，记下了。你这选择我大概知道你是什么路子了。",
"嗯，还算实在。下一题。",
"我跟你说，你这个选择很有意思——先别急着高兴，后面再说。",
"可以，你这个底子还不错。继续。",
"行吧，你这个情况我见多了。下一题。",
"你这个选择暴露了你的性格——稳字当头。",
"有意思，你这个思路我得好好分析一下。",
"得，又一个想不清楚的。没事，我帮你捋。",
];

// ── 推荐逻辑 ────────────────────────────────────
function recommend(answers){
  let scores={};
  for(let k in DB) scores[k]=5;
  const a=answers.map(x=>"ABCD".indexOf(x));

  // 科目偏好
  if(a[0]===0){["计算机科学与技术","软件工程","人工智能","电气工程及其自动化","集成电路设计","统计学","数学与应用数学","信息安全","金融工程"].forEach(k=>{if(scores[k])scores[k]+=2})}
  if(a[0]===1){["汉语言文学","新闻学","英语","法学","教育学"].forEach(k=>{if(scores[k])scores[k]+=2})}
  if(a[0]===2){["经济学","金融学","会计学","工商管理","知识产权"].forEach(k=>{if(scores[k])scores[k]+=2})}
  if(a[0]===3){["临床医学(五年制)","口腔医学","药学","数学与应用数学","物理学"].forEach(k=>{if(scores[k])scores[k]+=2})}

  // 家庭条件
  if(a[2]===3){["计算机科学与技术","软件工程","电气工程及其自动化","护理学","会计学"].forEach(k=>{if(scores[k])scores[k]+=2});["金融学","工商管理","新闻学"].forEach(k=>{if(scores[k])scores[k]-=2})}
  if(a[2]<=1){["金融学","金融工程","经济学","工商管理"].forEach(k=>{if(scores[k])scores[k]+=1})}

  // 工作环境
  if(a[3]===1){["电气工程及其自动化","护理学","教育学","汉语言文学","学前教育"].forEach(k=>{if(scores[k])scores[k]+=2})}
  if(a[3]===2){["计算机科学与技术","人工智能","集成电路设计","临床医学(五年制)","数学与应用数学"].forEach(k=>{if(scores[k])scores[k]+=2})}

  // 收入预期
  if(a[7]===0){["计算机科学与技术","软件工程","金融工程","集成电路设计","人工智能"].forEach(k=>{if(scores[k])scores[k]+=2})}
  if(a[7]===2||a[7]===3){["教育学","护理学","汉语言文学","学前教育"].forEach(k=>{if(scores[k])scores[k]+=1})}

  // 读研意愿
  if(a[4]===2){["计算机科学与技术","软件工程","会计学","护理学","视觉传达设计"].forEach(k=>{if(scores[k])scores[k]+=1});["人工智能","集成电路设计","临床医学(五年制)","物理学"].forEach(k=>{if(scores[k])scores[k]-=1})}

  // 排序取前3
  let sorted=Object.entries(scores).sort((a,b)=>b[1]-a[1]);
  return sorted.slice(0,3).map(([name,s])=>({
    major:name,
    score:Math.min(10,Math.max(5,Math.round(s*1.2))),
    reason:DB[name].tip,
    data:DB[name]
  }));
}

// ── 聊天知识匹配 ────────────────────────────────
// ── 省份+分数→志愿推荐 ─────────────────────────
const SCORE_TIERS=[
  {min:660,label:"顶尖985",schools:"清北复交浙",majors:"计算机/金融/临床8年制/电子信息",tip:"这个分数段选择权在你手上。优先选城市+学校，专业可以稍后考虑。清北的牌子本身就是一个筛子——能过这个筛子的人不多。"},
  {min:620,label:"中上985",schools:"南开/武大/华科/中大/厦大/哈工大/西交",majors:"计算机/电气/电子信息/临床5年制/金融",tip:"这个分数段竞争最激烈。我的建议：**城市>学校>专业**。北上广深杭的211，可能比偏远985更有价值。实习机会、校友资源、就业信息——这些东西，你在鹤岗学四年是接触不到的。"},
  {min:580,label:"中下985/强势211",schools:"兰大/中海洋/央财/上财/北邮/南航/南理工",majors:"计算机/软件工程/电气/通信/统计学",tip:"这个分数段是性价比最高的区间。你够不到顶尖985的热门专业，但211的王牌专业随便挑。**理工科选专业，文科选学校**——这句话在这个分数段最适用。"},
  {min:540,label:"普通211/强势双非",schools:"合工大/安大/河海/江南/苏大/深大/杭电",majors:"计算机/软件工程/电气/会计/信息安全",tip:"这个分数段我建议你重点关注**城市**。深圳大学不是211，但毕业生在深圳的就业不比很多211差。城市决定你的实习机会、人脉圈子和就业起点。"},
  {min:500,label:"普通一本/公办二本",schools:"省属重点/行业院校",majors:"计算机/软件工程/护理/会计/电气",tip:"这个分数段别追求学校牌子了——**专业为王**。选一个就业率高、有技术壁垒的专业，比上一个听起来好听但毕业就失业的专业强十倍。计算机、电气、护理——这些专业不需要985的牌子也能找到工作。"},
  {min:450,label:"二本/民办本科",schools:"公办二本优先/民办本科",majors:"计算机/护理/学前教育/会计",tip:"这个分数段说实话，别太纠结学校了。**能不能就业**是第一位的。选一个大城市（就业机会多）+ 好就业的专业（计算机、护理），比选一个偏远地区的'好学校'实际得多。"},
  {min:0,label:"专科",schools:"公办高职/职业本科",majors:"护理/计算机/电气自动化/学前教育",tip:"专科不是终点。很多专科学校的就业率比三本高。关键是选对方向——护理、计算机、电气自动化，这些专业出来就能干活。后面想提升，专升本也是一条路。"},
];

const PROVINCE_NAMES=["北京","天津","河北","山西","内蒙古","辽宁","吉林","黑龙江","上海","江苏","浙江","安徽","福建","江西","山东","河南","湖北","湖南","广东","广西","海南","重庆","四川","贵州","云南","西藏","陕西","甘肃","青海","宁夏","新疆"];

function handleVolunteerRequest(msg){
  // 提取分数
  let scoreMatch=msg.match(/(\d{3})\s*分/);
  let score=scoreMatch?parseInt(scoreMatch[1]):0;

  // 提取省份
  let province='';
  for(let p of PROVINCE_NAMES){
    if(msg.includes(p)){province=p;break}
  }
  if(!province){
    if(msg.includes("高考")||msg.includes("志愿")||msg.includes("报考")||msg.includes("填")){
      // 没说省份但问了志愿
      return `我跟你说，填志愿之前，先回答我三个问题：\n\n1. **哪个省的？** 不同省份分数线差距巨大，江苏600分和河南600分完全不是一个概念\n2. **多少分？** 我需要具体分数才能给你定位\n3. **家里做什么的？** 家庭条件不同，策略完全不同\n\n你先告诉我这些，我帮你**掰扯明白**。`;
    }
    return null; // 没匹配到志愿相关
  }

  if(!score){
    return `**${province}**的考生？行，但我还需要你的**分数**。\n\n我跟你说，不同分数段的策略完全不同：\n- 650+：顶尖985随便挑\n- 600-650：中上985/强势211\n- 550-600：普通211\n- 500-550：一本线附近\n- 500以下：专业为王\n\n你多少分？我帮你定位。`;
  }

  // 有省份有分数 → 查档位
  let tier=null;
  for(let t of SCORE_TIERS){
    if(score>=t.min){tier=t;break}
  }
  if(!tier)tier=SCORE_TIERS[SCORE_TIERS.length-1];

  return `好，**${province}**，理科**${score}分**。我给你捋清楚——\n\n你这个分数属于 **${tier.label}** 档位。\n\n**能冲的学校：** ${tier.schools}\n**推荐专业：** ${tier.majors}\n\n${tier.tip}\n\n---\n\n⚠️ 注意：以上是基于近年录取数据的大致定位。具体到${province}的投档线每年波动不同，建议结合你所在省份的一分一段表和目标学校近三年的录取分数做精确判断。\n\n**社会就是个筛子。** 你现在要做的，是找到那条你能过得去、但又不浪费分数的筛缝。`;
}

// ── 聊天知识匹配 ────────────────────────────────
function findAnswer(msg){
  msg=msg.toLowerCase();
  const raw=msg;

  // 0. 打招呼
  if(/^(你好|hi|hello|hey|您好|嗨|哈喽|在吗|在不在|有人吗)/.test(raw)){
    return "我跟你说，别整那些虚的。直接说问题——\n\n你想了解**哪个专业**？或者你**多少分、哪个省**的？我帮你分析。";
  }

  // 1. 分数+省份+志愿（最高优先级）
  let volAnswer=handleVolunteerRequest(raw);
  if(volAnswer)return volAnswer;

  // 2. 先查专业数据库（模糊匹配）
  let bestMatch=null, bestScore=0;
  for(let name in DB){
    let score=0;
    if(msg.includes(name.toLowerCase()))score=100;
    else{
      const words=name.split(/[（()）/·\-]/);
      for(let w of words){if(w.length>=2&&msg.includes(w.toLowerCase()))score=Math.max(score,60+w.length)}
      const short=name.slice(0,2);
      if(short.length>=2&&msg.includes(short.toLowerCase()))score=Math.max(score,40);
    }
    if(score>bestScore){bestScore=score;bestMatch=name}
  }
  if(bestScore>=40){
    let d=DB[bestMatch];
    return `**${bestMatch}**（${d.cat}）\n\n就业率${d.rate}，月薪中位数${d.salary}，考研比例${d.kaoyan}，对口率${d.match}。\n\n推荐城市：${d.city}\n\n${d.tip}`;
  }

  // 3. 关键词匹配（高频话题）
  const topics={
    "天坑|生化环材":"**生化环材四天王，没读博士别逞强。** 生物工程、化学工程、环境工程、材料科学——本科出来基本找不到对口工作，薪资低，工作环境差。考研是标配，读博才有出路。\n\n你家里有矿可以追求热爱，没矿的话，我劝你善良。",
    "计算机|编程|码农|程序员":"**计算机**是普通家庭孩子逆袭黄金赛道，但已经卷成麻花了。就业率93.9%，月薪7500+，但分化严重——985硕士进大厂年薪30万+，双非出来可能在做外包。\n\n千万别别说'学计算机=财务自由'，那都是十年前的故事了。",
    "金融|银行|证券|投资":"**家里没矿别碰金融。** 你看到的是年薪百万基金经理，看不到的是90%的人在银行网点卖理财产品。就业率85.8%，月薪中位数才6000，考研比例50%。\n\n除非你家里是搞金融的，否则——我劝你善良。",
    "考研.*985|985.*考研|双非.*考研|考研.*双非":"**双非考研985，值不值？** 看你是什么专业。\n\n理工科：必须考。本科出来天花板太低。\n文科：看学校不看专业。985的牌子能帮你过大部分企业的筛子。\n\n但记住：**最多考两次。** 考不上就工作，别死磕。",
    "考公|公务员|体制内|编制":"**考公还是考研？** 你家是哪的？\n\n如果老家是三四线城市：考公性价比极高。\n如果想在一线城市发展：考研优先。\n\n**考公和考研不冲突**——很多人一边考研一边考公，哪个先上岸去哪个。",
    "临床|医学|医生|学医|医院":"**学医是最苦的路，没有之一。** 本科5年+规培3年+专培2-3年。但越老越值钱——AI再牛，你敢让机器人给你开刀吗？\n\n口腔医学是性价比最高的方向，就业率92.8%。",
    "ai|人工智能|chatgpt|大模型":"**AI会取代哪些专业？** 不会被取代的：临床医学、牙医、电气工程。\n\n被冲击的：基础程序员、基础翻译、基础设计。\n\n**AI替代的是低端岗位，不是专业本身。** 关键是你站在被取代的那边，还是站在用AI的那边。",
    "文科|语文|历史":"**文科生选什么专业？**\n\n1. **法学** — 法考过了就有铁饭碗\n2. **汉语言文学** — 考公考编万金油\n3. **会计学** — 就业率89.1%\n\n千万别选新闻学——80%学新闻的人没从事本行业。",
    "土木|建筑|基建":"**土木和建筑，慎重。** 基建狂魔时代过去，房地产下行。除非你真的热爱——但热爱能当饭吃吗？",
    "电气|国网|电网":"**电气工程**——进国网的敲门砖。就业率92.1%，稳定但涨幅有限。想高薪得去新能源/芯片方向。",
    "考研|读研|研究生":"**考研值不值？** 取决于专业。\n\n必须考：临床医学、生化环材、物理。\n可以不考：计算机（能力强直接工作）、护理（就业率95%）。",
    "工资|薪资|收入|年薪|月薪":"**各专业薪资：**\n\n- 集成电路：12000/月（最高）\n- 软件工程：8000/月\n- 临床医学：6000规培期\n- 金融：6000/月（别被平均数骗了）\n- 师范/教育：4500/月\n\n**中位数比平均数靠谱。**",
    "选专业|怎么选|志愿|填志愿|报考":"**选专业的底层逻辑：**\n\n1. **先看家庭条件**——有矿追热爱，没矿看就业\n2. **再看擅长科目**——数理强选理工，文科强选法/师\n3. **然后看城市**——优先发达城市\n4. **最后看学校**——理工科选专业，文科选学校\n\n你多少分？哪个省的？我帮你具体定位。",
    "就业|找工作|毕业":"**就业率排名：**\n\n- 护理学 95%\n- 软件工程 94.5%\n- 集成电路 93.5%\n- 信息安全 92.3%\n- 电气工程 92.1%\n\n就业率低的：法学 72.3%、新闻学 78.5%",
    "转行|跨专业":"**转行成本：**\n\n最高：临床医学、法学\n最低：计算机、数学、物理\n\n我自己给排水毕业的，现在做教育做投资。**专业让我先活下来，选择让我走得更远。**",
    "985|211|双一流|一本|二本|专科":"**学校层次分析：**\n\n- **985**：39所，全国前2%的大学，考研/就业都是敲门砖\n- **211**：112所，覆盖大部分央企国企的学历门槛\n- **双一流**：新概念，部分非211学校也有优势学科\n- **普通一本**：看专业不看学校\n- **二本/专科**：专业为王，城市优先\n\n你多少分？我帮你定位能上什么层次的学校。",
    "高考":"**高考志愿填报的核心原则：**\n\n1. 分数决定你能去哪（别高估也别低估）\n2. 省份决定竞争烈度（河南、江苏是地狱模式）\n3. 城市决定未来资源（实习、就业、人脉）\n4. 专业决定饭碗（理工科选专业，文科选学校）\n\n你哪个省的？多少分？我帮你分析。",
  };
  for(let[keywords,answer]of Object.entries(topics)){
    const re=new RegExp(keywords);
    if(re.test(msg))return answer;
  }

  // 4. 兜底
  return `这个问题我目前没法精准回答。但我能帮你的是：\n\n**查专业** — 输入专业名（如计算机、金融、临床医学），36个专业数据随查\n**填志愿** — 告诉我你的省份和分数（如"江苏600分"），我帮你定位\n**做测评** — 切到"职业测评"标签，8道题找到适合你的方向`;
}

// ── State ───────────────────────────────────────
let aRound=0,aTotal=8,aAnswers=[],dbLoaded=false;

// ── UI ──────────────────────────────────────────
function switchTab(t){
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
  document.getElementById('t'+t.charAt(0).toUpperCase()+t.slice(1)).classList.add('active');
  document.getElementById('p'+t.charAt(0).toUpperCase()+t.slice(1)).classList.add('active');
  if(t==='data'&&!dbLoaded)renderDB();
}
function enterApp(){
  document.getElementById('splash').classList.add('hide');
  document.getElementById('shareBtn').style.display='block';
}
function shareIt(){
  const u=location.href;
  if(navigator.clipboard)navigator.clipboard.writeText(u).then(()=>toast('链接已复制'));
  else{const i=document.createElement('input');i.value=u;document.body.appendChild(i);i.select();document.execCommand('copy');document.body.removeChild(i);toast('链接已复制')}
}
function toast(m){const o=document.querySelector('.toast');if(o)o.remove();const t=document.createElement('div');t.className='toast';t.textContent=m;document.body.appendChild(t);setTimeout(()=>t.remove(),3000)}

// ── Chat ────────────────────────────────────────
let chatHist=[],hasBackend=null,aiProvider='claude';

function switchProvider(){
  aiProvider=document.getElementById('aiProvider').value;
}

// 页面加载时检测可用AI提供商
fetch('/api/providers')
  .then(r=>r.json())
  .then(data=>{
    if(data.success&&data.providers){
      const sel=document.getElementById('aiProvider');
      sel.innerHTML='';
      data.providers.forEach(p=>{
        const opt=document.createElement('option');
        opt.value=p.id;
        opt.textContent=p.name+(p.available?'':'（未配置）');
        opt.disabled=!p.available;
        sel.appendChild(opt);
      });
      const firstAvail=data.providers.find(p=>p.available);
      if(firstAvail){sel.value=firstAvail.id;aiProvider=firstAvail.id;}
    }
  })
  .catch(()=>{});

function send(){
  const inp=document.getElementById('chatIn'),msg=inp.value.trim();if(!msg)return;
  inp.value='';
  const ch=document.getElementById('chips');if(ch)ch.style.display='none';
  addMsg(msg,'user','chatBox');
  const ld=addLd('chatBox');

  // 尝试后端API
  if(hasBackend!==false){
    fetch('/api/search',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:msg,history:chatHist,provider:aiProvider})
    })
    .then(r=>r.json())
    .then(data=>{
      ld.remove();
      if(data.success){
        hasBackend=true;
        chatHist=data.history||[];
        const answer=data.reply;
        addMsg(answer,'ai','chatBox');
      }else{throw new Error('api fail')}
    })
    .catch(()=>{
      ld.remove();
      hasBackend=false;
      const answer=findAnswer(msg);
      addMsg(answer,'ai','chatBox');
    });
  }else{
    setTimeout(()=>{
      ld.remove();
      const answer=findAnswer(msg);
      addMsg(answer,'ai','chatBox');
    },400+Math.random()*400);
  }
}
function ask(el){document.getElementById('chatIn').value=el.textContent;send()}
function addMsg(text,who,boxId){
  const box=document.getElementById(boxId),d=document.createElement('div');
  d.className='msg'+(who==='user'?' user':'');
  const av=who==='user'?'<div class="av me">YOU</div>':'<div class="av ai">峰</div>';
  const fmt=who==='user'?text:text.replace(/\*\*(.*?)\*\*/g,'<strong>$1</strong>').replace(/\n/g,'<br>');
  d.innerHTML=av+'<div class="bub">'+fmt+'</div>';
  box.appendChild(d);d.scrollIntoView({behavior:'smooth',block:'end'});
}
function addLd(boxId){
  const box=document.getElementById(boxId),d=document.createElement('div');
  d.className='ld';
  d.innerHTML='<div class="av ai" style="width:28px;height:28px;font-size:.6rem">峰</div><div class="bars"><span></span><span></span><span></span><span></span></div> 分析中';
  box.appendChild(d);d.scrollIntoView({behavior:'smooth',block:'end'});return d;
}

// ── Assessment ──────────────────────────────────
function startAss(){
  aRound=0;aAnswers=[];
  document.getElementById('aStats').style.display='flex';
  const p=document.getElementById('aDots');p.innerHTML='';p.style.display='flex';
  for(let i=0;i<aTotal;i++){const d=document.createElement('div');d.className='pd'+(i===0?' on':'');p.appendChild(d)}
  showQ();
}
function showQ(){
  aRound++;updateUI();
  const q=QS[aRound-1],box=document.getElementById('assChat');
  // 点评
  if(aRound>1){
    const c=COMMENTS[Math.floor(Math.random()*COMMENTS.length)];
    addMsg(c,'ai','assChat');
  }
  // 题目
  const card=document.createElement('div');card.className='quiz-card';card.id='qCard';
  let opts='';q.o.forEach((o,i)=>{opts+=`<button class="opt" onclick="pick(this,${i})" data-i="${i}"><span class="ltr">${'ABCD'[i]}</span><span>${o}</span></button>`});
  card.innerHTML=`<h3>QUESTION ${aRound} / ${aTotal}</h3><div class="opts">${opts}</div>`;
  box.appendChild(card);card.scrollIntoView({behavior:'smooth',block:'end'});
}
function pick(btn,idx){
  if(btn.classList.contains('disabled'))return;
  btn.classList.add('sel');
  document.querySelectorAll('#qCard .opt').forEach(b=>b.classList.add('disabled'));
  const dots=document.querySelectorAll('.pd');
  if(dots[aRound-1])dots[aRound-1].classList.add('done');
  aAnswers.push('ABCD'[idx]);
  if(aRound>=aTotal){
    setTimeout(()=>{
      const recs=recommend(aAnswers);
      let html='<div class="msg"><div class="av ai">峰</div><div class="bub">行，8道题做完了。我给你<strong>捋清楚</strong>——</div></div>';
      html+='<div class="rec-grid">';
      recs.forEach(r=>{
        html+=`<div class="rec-card"><div class="rs">${r.score}/10</div><div class="rm">${r.major}</div><div class="rr">${r.reason}</div></div>`;
      });
      html+='</div><br><button class="send-btn" onclick="location.reload()" style="width:100%">RESTART</button>';
      const wrap=document.createElement('div');wrap.style.animation='up .5s ease-out';wrap.innerHTML=html;
      document.getElementById('assChat').appendChild(wrap);
      wrap.scrollIntoView({behavior:'smooth',block:'end'});
      document.querySelectorAll('.pd').forEach(d=>d.classList.add('done'));
    },800);
  }else{
    setTimeout(()=>showQ(),800);
  }
}
function updateUI(){
  document.getElementById('aR').textContent=aRound+'/'+aTotal;
  document.getElementById('aP').textContent=Math.round(aRound/aTotal*100)+'%';
  document.querySelectorAll('.pd').forEach((d,i)=>{d.classList.remove('on');if(i===aRound)d.classList.add('on')});
}

// ── Data ────────────────────────────────────────
function renderDB(filter){
  const el=document.getElementById('dCon');
  // 按分类组织
  let cats={};
  for(let[name,info]of Object.entries(DB)){
    let c=info.cat;
    if(filter&&!name.includes(filter)&&!c.includes(filter))continue;
    if(!cats[c])cats[c]=[];
    cats[c].push({name,...info});
  }
  let html='';
  // 天坑
  if(!filter){
    html+='<div class="wbox"><h4>WARNING — 天坑专业</h4><p><strong>生物工程 / 化学工程 / 环境工程 / 材料科学</strong><br>生化环材四天王，没读博士别逞强。本科出来基本找不到对口工作。</p></div>';
  }
  for(let[cat,items]of Object.entries(cats)){
    html+=`<div class="dsec open"><div class="dh" onclick="this.parentElement.classList.toggle('open')"><h4>${cat}</h4><span class="ar">▼</span></div><div class="dbody" style="display:block"><table class="dtbl"><tr><th>专业</th><th>就业率</th><th>月薪</th><th>考研</th><th>对口率</th><th>点评</th></tr>`;
    items.forEach(x=>{
      const r=parseFloat(x.rate)||0,rc=r>=90?'good':r<80?'warn':'';
      html+=`<tr><td class="mn">${x.name}</td><td class="${rc}">${x.rate}</td><td>${x.salary}</td><td>${x.kaoyan}</td><td>${x.match}</td><td class="note">${x.tip}</td></tr>`;
    });
    html+='</table></div></div>';
  }
  if(filter&&Object.keys(cats).length===0)html=`<div style="padding:28px;text-align:center;color:var(--text-dim);font-family:var(--mono)">NO RESULTS FOR "${filter}"</div><div class="chips"><span class="chip" onclick="document.getElementById('dIn').value='';searchD()">清除搜索</span></div>`;
  el.innerHTML=html;dbLoaded=true;
}
function searchD(){renderDB(document.getElementById('dIn').value.trim())}

// ── Init ────────────────────────────────────────
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return EMBEDDED_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


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
        queries = _build_search_queries(user_message)
        for q in queries[:2]:
            search_results += f"\n[搜索: {q}]\n{web_search(q, max_results=4)}\n"

    messages = [{"role": m["role"], "content": m["content"]} for m in history]
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 55)
    print("  张雪峰智能志愿百科 v2")
    print("=" * 55)
    print(f"  Claude模型: {MODEL} ({'已配置' if ANTHROPIC_AVAILABLE else '未配置'})")
    print(f"  百度AI模型: {BAIDU_MODEL} ({'已配置' if QIANFAN_AVAILABLE else '未配置'})")
    print(f"  端口: {port}")
    print(f"  功能: 百科问答 + 联网搜索 + 职业倾向测评")
    print("=" * 55)
    app.run(host="0.0.0.0", port=port, debug=False)
