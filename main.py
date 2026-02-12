import asyncio
import html
import json
import re
import sqlite3
import datetime
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional, Set, Dict, List
from urllib.parse import quote

import aiohttp
from fastapi import FastAPI, WebSocket
from fastbot.bot import FastBot
from fastbot.plugin import PluginManager
from starlette.websockets import WebSocketState

import pandas as pd  # NEW: Import pandas for Excel export
import os            # NEW: Import os for path operations

# 导入模块加载器
from core.module_loader import ModuleLoader

from config import (
    DEBUG_MODE,
    VERBOSE_LOGGING,
    TAOBAO_CONFIG,
    JINGDONG_CONFIG,
    JINGTUITUI_CONFIG,
    AUTO_CLEANUP_ENABLED,
    CLEANUP_DAYS,
    CLEANUP_HOUR,
    DATABASE_FILE,
    ONEBOT_HOST,
    ONEBOT_PORT,
    get_bot_qq_list  # NEW: Import to get all bot QQs
)

# 导入颜色工具
from utils.colors import green, red, yellow, blue, SUCCESS, ERROR, WARNING, INFO

# 从配置字典中提取具体的 API 参数（向后兼容）
APP_KEY = TAOBAO_CONFIG.get("app_key", "")
SID = TAOBAO_CONFIG.get("sid", "")
PID = TAOBAO_CONFIG.get("pid", "")
RELATION_ID = TAOBAO_CONFIG.get("relation_id", "")

JD_APPKEY = JINGDONG_CONFIG.get("appkey", "")
JD_UNION_ID = JINGDONG_CONFIG.get("union_id", "")
JD_POSITION_ID = JINGDONG_CONFIG.get("position_id", "")

JTT_APPID = JINGTUITUI_CONFIG.get("appid", "")
JTT_APPKEY = JINGTUITUI_CONFIG.get("appkey", "")

# 调试日志函数
def debug_log(message: str):
    if DEBUG_MODE:
        print(f"[DEBUG] {message}")

# 详细日志函数
def verbose_log(category: str, message: str):
    """打印详细日志"""
    if VERBOSE_LOGGING.get("enabled", False) and VERBOSE_LOGGING.get(category, False):
        print(f"[{category.upper()}] {message}")

# DB_FILE declaration
DB_FILE = "messages.db"


# 全局变量
module_loader: Optional[ModuleLoader] = None  # 模块加载器实例
group_timers: Dict[int, Dict] = {}  # {group_id: {'enabled': bool, 'interval': int, 'task': asyncio.Task}}
pending_recall_messages: Set[int] = set()  # 待重试撤回的消息ID集合
pending_requests: Dict[str, int] = {}  # {echo: message_id} 用于匹配 OneBot 响应,特别是为重试和自动撤回保留 (老的同步逻辑)
pending_futures: Dict[str, asyncio.Future] = {}  # {echo: Future} 用于 `force_recall_message` 非阻塞等待响应 (新的异步逻辑)
retry_counts: Dict[int, int] = {}  # 记录每个消息ID的重试次数
MAX_RETRY_ATTEMPTS = 3  # 最大重试3次
has_printed_watched_groups = False

# 导入机器人管理器（解决循环引用）
from core import bot_manager

# 机器人在线状态跟踪
# online_bots: Set[int] = set()  # 当前在线的机器人QQ号集合 # This is now managed by bot_manager

def get_online_bots() -> Set[int]:
    """
    获取当前在线的机器人QQ号集合
    
    Returns:
        当前在线的机器人QQ号集合
    """
    return bot_manager.get_online_bots()

# SQLite数据库初始化
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER,
            user_id INTEGER,
            message_id INTEGER,
            raw_message TEXT,
            recalled BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(group_id, message_id) ON CONFLICT IGNORE
        )
    ''')
    
    cursor.execute("PRAGMA table_info(messages)")
    columns = {col[1] for col in cursor.fetchall()}
    
    if 'recalled' not in columns:
        cursor.execute("ALTER TABLE messages ADD COLUMN recalled BOOLEAN DEFAULT 0")
        debug_log("已添加 recalled 列到 messages 表")
    
    if 'created_at' not in columns:
        cursor.execute("ALTER TABLE messages ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        debug_log("已添加 created_at 列到 messages 表")

    conn.commit()
    conn.close()

init_db()

def get_online_bots() -> Set[int]:
    """
    获取当前在线的机器人QQ号集合
    供模块调用以判断机器人在线状态
    
    Returns:
        在线机器人QQ号集合
    """
    return online_bots.copy()

# 数据库清理功能

async def cleanup_old_messages(days_to_keep: int = 7) -> str:
    """
    清理指定天数前的已撤回消息
    
    Args:
        days_to_keep: 保留多少天的数据，默认7天
    
    Returns:
        清理结果字符串
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        # 计算清理的时间点 (使用UTC时间)
        cutoff_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_to_keep)
        cutoff_str = cutoff_date.isoformat()
        
        # 查询要删除的消息数量
        cursor.execute("""
            SELECT COUNT(*) FROM messages 
            WHERE recalled = 1 AND created_at < ?
        """, (cutoff_str,))
        count_to_delete = cursor.fetchone()[0]
        
        if count_to_delete == 0:
            return f"没有需要清理的已撤回消息（{days_to_keep}天前）"
        
        # 删除旧的已撤回消息
        cursor.execute("""
            DELETE FROM messages 
            WHERE recalled = 1 AND created_at < ?
        """, (cutoff_str,))
        
        deleted_count = cursor.rowcount
        conn.commit()
        
        # 优化数据库
        cursor.execute("VACUUM")
        
        debug_log(f"数据库清理完成：删除了 {deleted_count} 条已撤回消息")
        return f"数据库清理完成：删除了 {deleted_count} 条已撤回消息（{days_to_keep}天前）"
        
    except Exception as e:
        conn.rollback()
        debug_log(f"数据库清理失败: {str(e)}")
        return f"数据库清理失败: {str(e)}"
    finally:
        conn.close()

async def cleanup_all_recalled_messages() -> str:
    """
    清理所有已撤回的消息
    
    Returns:
        清理结果字符串
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        # 查询要删除的消息数量
        cursor.execute("SELECT COUNT(*) FROM messages WHERE recalled = 1")
        count_to_delete = cursor.fetchone()[0]
        debug_log(f"准备清理已撤回消息，当前已撤回消息数量: {count_to_delete}")
        
        if count_to_delete == 0:
            return "没有需要清理的已撤回消息"
        
        # 删除所有已撤回消息
        cursor.execute("DELETE FROM messages WHERE recalled = 1")
        deleted_count = cursor.rowcount
        conn.commit()
        
        # 验证删除结果
        cursor.execute("SELECT COUNT(*) FROM messages WHERE recalled = 1")
        remaining_count = cursor.fetchone()[0]
        debug_log(f"删除后剩余已撤回消息数量: {remaining_count}")
        
        # 优化数据库
        cursor.execute("VACUUM")
        
        debug_log(f"数据库清理完成：删除了 {deleted_count} 条已撤回消息")
        return f"数据库清理完成：删除了 {deleted_count} 条已撤回消息"
        
    except Exception as e:
        conn.rollback()
        debug_log(f"数据库清理失败: {str(e)}")
        return f"数据库清理失败: {str(e)}"
    finally:
        conn.close()

async def get_database_stats() -> str:
    """
    获取数据库统计信息
    
    Returns:
        数据库统计信息字符串
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        # 总消息数
        cursor.execute("SELECT COUNT(*) FROM messages")
        total_messages = cursor.fetchone()[0]
        
        # 已撤回消息数
        cursor.execute("SELECT COUNT(*) FROM messages WHERE recalled = 1")
        recalled_messages = cursor.fetchone()[0]
        
        # 未撤回消息数
        cursor.execute("SELECT COUNT(*) FROM messages WHERE recalled = 0")
        active_messages = cursor.fetchone()[0]
        
        # 最老的消息时间
        cursor.execute("SELECT MIN(created_at) FROM messages")
        oldest_message = cursor.fetchone()[0]
        
        # 转换最早消息时间到本地时区（CST = UTC+8）
        oldest_message_display = "无"
        if oldest_message:
            try:
                # 假设数据库存储的是ISO格式的UTC时间字符串
                dt_utc = datetime.datetime.fromisoformat(oldest_message).replace(tzinfo=datetime.timezone.utc)
                # 定义CST时区（UTC+8）
                cst_tz = datetime.timezone(datetime.timedelta(hours=8))
                oldest_message_cst = dt_utc.astimezone(cst_tz)
                oldest_message_display = oldest_message_cst.strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                debug_log(f"获取数据库统计：无法解析最早消息时间字符串: {oldest_message}")
                oldest_message_display = oldest_message # 解析失败时显示原始字符串

        # 数据库文件大小（需要os模块）
        import os
        db_size = os.path.getsize(DB_FILE) / (1024 * 1024)  # MB
        
        return (
            f"数据库统计信息:\n"
            f"总消息数: {total_messages}\n"
            f"已撤回消息: {recalled_messages}\n"
            f"未撤回消息: {active_messages}\n"
            f"最早消息时间: {oldest_message_display}\n"
            f"数据库大小: {db_size:.2f} MB"
        )
        
    except Exception as e:
        debug_log(f"获取数据库统计失败: {str(e)}")
        return f"获取数据库统计失败: {str(e)}"
    finally:
        conn.close()

# 定时清理任务
async def scheduled_cleanup_task():
    """
    定时清理任务，每天指定小时清理指定天数前的已撤回消息
    """
    while True:
        now = datetime.datetime.now()
        # 计算到下一个CLEANUP_HOUR点的时间
        next_run = now.replace(hour=CLEANUP_HOUR, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += datetime.timedelta(days=1)
        # 等待到指定时间
        wait_seconds = (next_run - now).total_seconds()
        debug_log(f"定时清理任务将在 {next_run} 执行")
        await asyncio.sleep(wait_seconds)
        # 执行清理
        result = await cleanup_old_messages(days_to_keep=CLEANUP_DAYS)
        debug_log(f"定时清理结果: {result}")

# 在process_message函数中添加清理相关指令
async def add_cleanup_commands_to_process_message(msg: str):
    """
    处理各种清理数据库的指令.
    返回 (reply_content, needs_auto_recall_reply) 或 None.
    """
    # 清理指令
    if msg == "清理数据库" or (msg.startswith("@") and "清理数据库" in msg):
        return (await cleanup_old_messages(days_to_keep=7), True)
    
    if msg == "清理全部已撤回" or (msg.startswith("@") and "清理全部已撤回" in msg):
        return (await cleanup_all_recalled_messages(), True)
    
    if msg == "数据库统计" or (msg.startswith("@") and "数据库统计" in msg):
        return (await get_database_stats(), False) # 统计指令不应自动撤回自身
    
    if msg.startswith("清理") and "天" in msg:
        try:
            import re
            match = re.search(r'清理(\d+)天', msg)
            if match:
                days = int(match.group(1))
                return (await cleanup_old_messages(days_to_keep=days), True)
        except:
            return ("清理指令格式错误，正确格式：清理3天", False)
    return None # 如果没有匹配任何清理指令，返回 None

# 在应用启动时添加定时任务
async def start_cleanup_scheduler():
    """
    启动定时清理任务（根据AUTO_CLEANUP_ENABLED）
    """
    if AUTO_CLEANUP_ENABLED:
        asyncio.create_task(scheduled_cleanup_task())
        debug_log("定时清理任务已启动（已启用自动清理）")
    else:
        debug_log("未启用自动清理，定时清理任务未启动")

async def get_onebot_history_messages(ws: WebSocket, group_id: Optional[int], count: int = 20) -> List[Dict]:
    """
    通过OneBot WebSocket API获取历史消息。
    根据Lagrange.OneBot API文档，使用 'get_group_msg_history' 动作。
    Args:
        ws: WebSocket连接对象。
        group_id: 群聊ID。
        count: 获取消息的数量。
    Returns:
        历史消息列表，每个元素是一个字典。
    """
    if group_id is None:
        debug_log("获取群历史消息需要指定群聊ID")
        return []
    
    debug_log(f"尝试从OneBot获取群 {group_id} 的历史消息 (action: get_group_msg_history): count={count}")
    
    payload = {
        "action": "get_group_msg_history",
        "params": {
            "group_id": group_id,
            "count": count
        },
        "echo": f"get_group_msg_history_echo_{datetime.datetime.now().timestamp()}"
    }
    
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_futures[payload["echo"]] = future

    try:
        if ws.client_state != WebSocketState.CONNECTED:
            debug_log("WebSocket连接断开，无法获取历史消息")
            return []

        await ws.send_text(json.dumps(payload))
        debug_log(f"已发送获取历史消息命令: {payload['echo']}")

        response = await asyncio.wait_for(future, timeout=15.0) # Increased timeout
        
        if response.get("status") == "ok" and "data" in response:
            messages_data = response["data"].get("messages", [])
            debug_log(f"成功从OneBot获取 {len(messages_data)} 条群 {group_id} 的历史消息。")
            return messages_data
        else:
            error_msg = response.get("wording") or response.get("message", "未知错误")
            debug_log(f"获取历史消息失败: {error_msg}, 完整响应: {json.dumps(response)}")
            return []
    except asyncio.TimeoutError:
        debug_log(f"获取历史消息超时")
        return []
    except Exception as e:
        debug_log(f"获取历史消息异常: {str(e)}")
        return []
    finally:
        pending_futures.pop(payload["echo"], None)

async def export_database_to_excel() -> str:
    """
    导出 messages 数据库表到 Excel 文件。
    """
    export_dir = "exports"
    if not os.path.exists(export_dir):
        os.makedirs(export_dir) # 创建 exports 目录如果不存在

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"messages_export_{timestamp}.xlsx"
    file_path = os.path.join(export_dir, file_name)

    conn = None
    try:
        conn = sqlite3.connect(DB_FILE)
        # 读取 messages 表的所有数据到 pandas DataFrame
        df = pd.read_sql_query("SELECT * FROM messages", conn)
        
        # 将 DataFrame 写入 Excel 文件
        df.to_excel(file_path, index=False) # index=False 不写入 DataFrame 的索引
        debug_log(f"数据库已成功导出到: {file_path}")
        return f"数据库已成功导出到文件：`{file_path}`"
    except Exception as e:
        debug_log(f"导出数据库到Excel失败: {str(e)}")
        return f"导出数据库到Excel失败: {str(e)}"
    finally:
        if conn:
            conn.close()

# 正则表达式
TAOBAO_REGEX = re.compile(
    r'(https?://[^\s<>]*(?:taobao\.|tb\.)[^\s<>]+)|'
    r'(?:￥|\$)([0-9A-Za-z()]*[A-Za-z][0-9A-Za-z()]{10})(?:￥|\$)?(?![0-9A-Za-z])|'
    r'([0-9]\$[0-9a-zA-Z]+\$:// [A-Z0-9]+)|'
    r'tk=([0-9A-Za-z]{11,12})|'
    r'\(([0-9A-Za-z]{11})\)|'
    r'₤([0-9A-Za-z]{13})₤|'
    r'[0-9]{2}₤([0-9A-Za-z]{11})£'
)

JD_REGEX = re.compile(
    r'https?:\/\/[^\s<>]*(3\.cn|jd\.|jingxi)[^\s<>]+|[^一-龥0-9a-zA-Z=;&?-_.<>:\'",{}][0-9a-zA-Z()]{16}[^一-龥0-9a-zA-Z=;&?-_.<>:\'",{}\s]'
)

def remove_cq_codes(text: str) -> str:
    # 稍微改进一下，确保所有CQ码都被移除
    return re.sub(r'\[CQ:[^\]]+\]', '', text)

def extract_from_cq_json(text: str) -> str:
    pattern = re.compile(r'\[CQ:json,data=(\{.*?\})\]')
    matches = pattern.findall(text)
    extracted = ""
    for m in matches:
        m_unescaped = html.unescape(m)
        debug_log(f"提取的CQ:json数据: {m_unescaped}")
        try:
            data_obj = json.loads(m_unescaped)
            if "data" in data_obj and isinstance(data_obj["data"], str):
                try:
                    inner = json.loads(data_obj["data"])
                    news = inner.get("meta", {}).get("news", {})
                    jump_url = news.get("jumpUrl", "")
                    if jump_url:
                        extracted += " " + jump_url
                except Exception as e:
                    debug_log(f"内部CQ:json数据解析错误: {str(e)}")
            else:
                news = data_obj.get("meta", {}).get("news", {})
                jump_url = news.get("jumpUrl", "")
                if jump_url:
                    extracted += " " + jump_url
        except Exception as e:
            debug_log(f"CQ:json解析错误: {str(e)}")
    return extracted.strip()

async def convert_tkl(tkl: str, processed_titles: Set[str]) -> Optional[str]:
    try:
        async with aiohttp.ClientSession() as session:
            base_url = "https://api.zhetaoke.com:10001/api/open_gaoyongzhuanlian_tkl.ashx"
            params = {
                "appkey": APP_KEY,
                "sid": SID,
                "pid": PID,
                "relation_id": RELATION_ID,
                "tkl": quote(tkl),
                "signurl": 5
            }
            debug_log_full_api(base_url, params)
            async with session.get(base_url, params=params, timeout=10) as response:
                response.raise_for_status()
                result = await response.json(content_type=None)
                if result.get("status") == 200 and "content" in result:
                    content = result["content"][0]
                    title = content.get('tao_title', content.get('title', '未知'))
                    if title in processed_titles:
                        debug_log(f"跳过重复的淘宝标题: {title}")
                        return None
                    processed_titles.add(title)
                    pict_url = content.get('pict_url', content.get('pic_url', ''))
                    image_cq = f"[CQ:image,file={pict_url}]" if pict_url else ""
                    return (
                        f"商品：{title}\n\n"
                        f"券后: {content.get('quanhou_jiage', '未知')}\n"
                        f"佣金: {content.get('tkfee3', '未知')}\n"
                        f"链接: {content.get('shorturl2', '未知')}\n"
                        f"淘口令: {content.get('tkl', '未知')}\n"
                        f"{image_cq}"
                    )
                else:
                    content = result.get("content", "未知错误")
                    if isinstance(content, list) and content:
                        content = content[0]
                        title = content.get('tao_title', content.get('title', '未知'))
                        if title in processed_titles:
                            debug_log(f"跳过重复的淘宝标题（错误情况）: {title}")
                            return None
                        processed_titles.add(title)
                        return f"TB错误: {json.dumps(content, ensure_ascii=False)}"
                    elif isinstance(content, dict):
                        title = content.get('tao_title', content.get('title', '未知'))
                        if title in processed_titles:
                            debug_log(f"跳过重复的淘宝标题（错误情况）: {title}")
                            return None
                        processed_titles.add(title)
                        return f"TB错误: {json.dumps(content, ensure_ascii=False)}"
                    else:
                        return f"TB错误: {content}"
    except Exception as e:
        debug_log(f"TB错误: {str(e)}")
        return "淘宝转链失败: 请求异常"

async def convert_jd_link(material_url: str, processed_titles: Set[str]) -> Optional[str]:
    # --- Existing JD API call (to get product info and short URL) ---
    base_url = "http://api.zhetaoke.com:20000/api/open_jing_union_open_promotion_byunionid_get.ashx"
    params = {
        "appkey": JD_APPKEY,
        "materialId": material_url, 
        "unionId": JD_UNION_ID,
        "positionId": JD_POSITION_ID,
        "chainType": 3,
        "signurl": 5
    }
    debug_log(f"调用主要京东转链API: {base_url} with materialId={material_url}")
    jd_command_text = "" # Initialize JD command
    
    try:
        async with aiohttp.ClientSession() as session:
            # First API call to get product details and short URL
            async with session.get(base_url, params=params, timeout=10) as response:
                response.raise_for_status()
                result = await response.json(content_type=None)
                
                # Check for success of the first API call and extract info
                if result.get("status") == 200 and "content" in result:
                    content = result["content"][0]
                    jianjie = content.get('jianjie', '未知')
                    if jianjie in processed_titles:
                        debug_log(f"跳过重复的京东标题: {jianjie}")
                        return None
                    processed_titles.add(jianjie)
                    pict_url = content.get('pict_url', content.get('pic_url', ''))
                    image_cq = f"[CQ:image,file={pict_url}]" if pict_url else ""
                    short_url_from_zhetaoke = content.get('shorturl', '') # Get the short URL from the first API
                    
                    # --- 京推推口令 API 调用 ---
                    # 只有在成功获取到折京客短链接后才调用京推推生成口令
                    if short_url_from_zhetaoke:
                        # 使用折京客返回的短链接作为 gid 参数
                        command_api_url = f"http://japi.jingtuitui.com/api/get_goods_command?appid={JTT_APPID}&appkey={JTT_APPKEY}&unionid={JD_UNION_ID}&gid={quote(short_url_from_zhetaoke)}"
                        if JD_POSITION_ID:
                            command_api_url += f"&positionid={JD_POSITION_ID}"
                        
                        debug_log(f"调用京推推口令API: {command_api_url}")
                        
                        try:
                            async with session.post(command_api_url, timeout=5) as cmd_response:
                                cmd_response.raise_for_status()
                                cmd_result = await cmd_response.json(content_type=None)
                                if "return" in cmd_result and cmd_result.get("msg", "").startswith("ok"):
                                    jd_command_text_raw = cmd_result["return"].get("jd_short_kl", "")
                                    if jd_command_text_raw:
                                        jd_command_text = f"【口令】{jd_command_text_raw}"
                                    debug_log(f"京推推口令转换成功: {jd_command_text_raw}")
                                else:
                                    debug_log(f"京推推口令转换失败: {cmd_result.get('msg', '未知错误')}")
                        except Exception as cmd_e:
                            debug_log(f"京推推口令API请求异常: {str(cmd_e)}")
                    else:
                        debug_log("未获取到有效的short_url，跳过京推推口令生成。")


                    # Combine results from both APIs
                    # MODIFIED: 调整返回字符串的换行符,确保精确格式
                    return_string = (
                        f"【商品】: {jianjie}\n\n"
                        f"【券后】: {content.get('quanhou_jiage', '未知')}\n"
                        f"【佣金】: {content.get('tkfee3', '未知')}\n"
                        f"【领券买】: {short_url_from_zhetaoke}\n"
                    )
                    # 如果口令存在,添加口令行
                    if jd_command_text:
                        command_only = jd_command_text.replace("【口令】", "").strip()
                        return_string += f"【领券口令】: {command_only}\n"
                    
                    if image_cq: # 如果图片CQ码存在,则添加
                        return_string += f"{image_cq}"
                    
                    return return_string


                
                
                # Error handling for the first JD API call
                if "jd_union_open_promotion_byunionid_get_response" in result:
                    jd_response = result["jd_union_open_promotion_byunionid_get_response"]
                    if "result" in jd_response:
                        try:
                            jd_result = json.loads(jd_response["result"])
                            error_message = jd_result.get("message", "未知错误")
                            if jd_result.get("data") and jd_result["data"].get("shortURL"):
                                short_url = jd_result["data"]["shortURL"]
                                
                                # --- 在错误处理分支也调用京推推生成口令 ---
                                jd_command_text = ""
                                if short_url:
                                    command_api_url = f"http://japi.jingtuitui.com/api/get_goods_command?appid={JTT_APPID}&appkey={JTT_APPKEY}&unionid={JD_UNION_ID}&gid={quote(short_url)}"
                                    if JD_POSITION_ID:
                                        command_api_url += f"&positionid={JD_POSITION_ID}"
                                    
                                    debug_log(f"调用京推推口令API(错误处理分支): {command_api_url}")
                                    
                                    try:
                                        async with session.post(command_api_url, timeout=5) as cmd_response:
                                            cmd_response.raise_for_status()
                                            cmd_result = await cmd_response.json(content_type=None)
                                            if "return" in cmd_result and cmd_result.get("msg", "").startswith("ok"):
                                                jd_command_text_raw = cmd_result["return"].get("jd_short_kl", "")
                                                if jd_command_text_raw:
                                                    jd_command_text = f"\n【口令】{jd_command_text_raw}"
                                                debug_log(f"京推推口令转换成功(错误处理分支): {jd_command_text_raw}")
                                            else:
                                                debug_log(f"京推推口令转换失败(错误处理分支): {cmd_result.get('msg', '未知错误')}")
                                    except Exception as cmd_e:
                                        debug_log(f"京推推口令API请求异常(错误处理分支): {str(cmd_e)}")
                                
                                return f"优惠: {short_url}{jd_command_text}"
                            return f"JD转换失败: {error_message}"
                        except json.JSONDecodeError:
                            return "JD转换失败: 返回数据解析错误"
                return "JD转换失败: 未知错误"
    except Exception as e:
        debug_log(f"JD错误: {str(e)}")
        return "JD请求失败: 请求异常"

async def force_recall_message(ws: WebSocket, message_id: int) -> str:
    """
    发送撤回请求并使用 Future 非阻塞地等待 OneBot 响应。
    """
    if not isinstance(message_id, int):
        return f"无效的消息ID: {message_id}"
    debug_log(f"尝试强制撤回消息ID: {message_id}")
    if ws.client_state != WebSocketState.CONNECTED:
        debug_log("WebSocket连接断开，无法撤回消息")
        return "撤回失败: WebSocket连接断开"

    # 使用时间戳确保echo的唯一性，防止和旧的pending_requests冲突
    unique_echo = f"force_recall_{message_id}_{datetime.datetime.now().timestamp()}"
    payload = {
        "action": "delete_msg",
        "params": {"message_id": message_id},
        "echo": unique_echo
    }
    
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_futures[unique_echo] = future # 将 Future 存储起来，等待 main loop 填充结果

    try:
        await ws.send_text(json.dumps(payload))
        debug_log(f"已发送撤回命令: {message_id}, echo: {unique_echo}")

        # 等待 OneBot 的响应，设置超时
        response = await asyncio.wait_for(future, timeout=5.0)
        
        if response.get("status") == "ok":
            # 成功，更新数据库
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute("UPDATE messages SET recalled = 1 WHERE message_id = ?", (message_id,))
            # 如果是OneBot已经撤回但我们数据库没有记录的情况，也插入为已撤回
            if cursor.rowcount == 0:
                debug_log(f"消息 {message_id} 未在数据库中，插入已撤回记录")
                cursor.execute(
                    "INSERT INTO messages (message_id, raw_message, recalled) VALUES (?, ?, ?)",
                    (message_id, "[已撤回]", 1)
                )
            conn.commit()
            conn.close()
            # 从待重试列表中移除，因为已经成功了
            pending_recall_messages.discard(message_id)
            retry_counts.pop(message_id, None) # 成功后清除重试计数
            debug_log(f"撤回成功: 消息ID {message_id}")
            return f"消息ID {message_id} 撤回成功"
        else:
            # 撤回失败
            error_msg = response.get("wording") or response.get("message", "未知错误")
            debug_log(f"撤回失败: 消息ID {message_id}, 原因: {error_msg}, 完整响应: {json.dumps(response)}")
            pending_recall_messages.add(message_id) # 添加到待重试列表
            return f"消息ID {message_id} 撤回失败: {error_msg}"
    
    except asyncio.TimeoutError:
        debug_log(f"撤回超时: 消息ID {message_id}")
        pending_recall_messages.add(message_id) # 添加到待重试列表
        # 这里不移除 future，因为 OneBot 可能只是响应慢，稍后会发送 echo。
        # 让 main loop 接收到 echo 后去处理。
        return f"消息ID {message_id} 撤回超时"
    
    except Exception as e:
        debug_log(f"force_recall_message异常: {e}")
        pending_recall_messages.add(message_id) # 添加到待重试列表
        return f"消息ID {message_id} 撤回错误: {str(e)}"
    
    finally:
        # 无论成功、失败还是超时，都应该从 pending_futures 字典中清理对应的 Future
        # 但是，对于 TimeoutError，我们希望 OneBot 延迟返回的 echo 依然能被处理，
        # 所以只有当 Future 已经被 set_result 后才安全地移除。
        pending_futures.pop(unique_echo, None)


async def recall_messages(ws: WebSocket, group_id: Optional[int], count: int) -> str:
    if group_id is None:
        debug_log("私聊不支持撤回")
        return "私聊不支持撤回功能"
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    query = "SELECT message_id FROM messages WHERE group_id = ? AND recalled = 0 ORDER BY id DESC LIMIT ?"
    cursor.execute(query, (group_id, count))
    messages_to_recall_rows = cursor.fetchall()
    conn.close()

    if not messages_to_recall_rows:
        debug_log(f"群 {group_id} 中没有未撤回的消息")
        return "群内没有可供撤回的消息"

    # 提取 message_id 列表并反转，以便从最旧的消息开始撤回
    message_ids = [row[0] for row in messages_to_recall_rows]
    
    results = []
    # 并发执行撤回操作，并收集结果
    tasks = [force_recall_message(ws, msg_id) for msg_id in reversed(message_ids) if isinstance(msg_id, int)]
    if tasks:
        results = await asyncio.gather(*tasks)
    
    return "\n".join(results) if results else "没有有效的消息ID可以撤回"

async def recall_group_messages(ws: WebSocket, group_id: int) -> str:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    query = "SELECT message_id FROM messages WHERE group_id = ? AND recalled = 0 ORDER BY id DESC"
    cursor.execute(query, (group_id,))
    messages_to_recall_rows = cursor.fetchall()
    conn.close()

    if not messages_to_recall_rows:
        debug_log(f"群 {group_id} 中没有未撤回的消息")
        return "群内没有未撤回的消息"

    message_ids = [row[0] for row in messages_to_recall_rows]

    results = []
    tasks = [force_recall_message(ws, msg_id) for msg_id in reversed(message_ids) if isinstance(msg_id, int)]
    if tasks:
        results = await asyncio.gather(*tasks)
    
    return "\n".join(results) if results else "没有有效的消息ID可以撤回"


async def retry_failed_recalls(ws: WebSocket):
    while True:
        await asyncio.sleep(60)  # 每60秒重试一次
        if not pending_recall_messages:
            continue
        
        # 只查询仍存在于 pending_recall_messages 中的消息
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        placeholders = ','.join('?' for _ in pending_recall_messages)
        query = f"SELECT message_id, group_id, user_id, raw_message, created_at FROM messages WHERE message_id IN ({placeholders}) AND recalled = 0"
        messages_to_retry_list = list(pending_recall_messages) # 转换为列表以便传参
        cursor.execute(query, messages_to_retry_list)
        failed_messages = cursor.fetchall() # (message_id, group_id, user_id, raw_message, created_at)
        conn.close()

        debug_log(f"开始重试 {len(failed_messages)} 条失败的消息...")
        # 遍历这些消息，尝试撤回
        for message_id, group_id, user_id, raw_message, created_at in failed_messages:
            if ws.client_state != WebSocketState.CONNECTED:
                debug_log("WebSocket已断开，无法重试撤回")
                continue # 跳过当前循环，等待下次重试

            # 检查重试次数
            retry_counts[message_id] = retry_counts.get(message_id, 0) + 1
            if retry_counts[message_id] > MAX_RETRY_ATTEMPTS:
                debug_log(f"消息 {message_id} 已达最大重试次数 {MAX_RETRY_ATTEMPTS}，移除")
                pending_recall_messages.discard(message_id) # 从待重试集合中移除
                retry_counts.pop(message_id, None) # 清除重试计数
                continue # 跳过当前消息

            # 调用 force_recall_message 来处理重试，它会负责等待响应和更新状态
            debug_log(f"重试撤回消息 {message_id} (群 {group_id}, 用户 {user_id}, 第 {retry_counts[message_id]} 次)")
            await force_recall_message(ws, message_id)
            # await asyncio.sleep(0.5) # 适当延迟，避免洪水


async def timer_recall_group(ws: WebSocket, group_id: int):
    while group_timers.get(group_id, {}).get('enabled', False):
        debug_log(f"群 {group_id} 定时任务执行：每 {group_timers[group_id]['interval']} 分钟撤回消息")
        result = await recall_group_messages(ws, group_id)
        if result:
            debug_log(f"定时撤回结果: {result}")
        await asyncio.sleep(group_timers[group_id]['interval'] * 60)

async def query_database() -> str:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        cursor.execute("PRAGMA table_info(messages)")
        columns = {col[1] for col in cursor.fetchall()}
        
        select_columns = ["group_id", "user_id", "message_id", "raw_message"]
        if "recalled" in columns:
            select_columns.append("recalled")
        if "created_at" in columns:
            select_columns.append("created_at")
        
        # MODIFIED: Changed ORDER BY to created_at DESC for more logical "latest" view
        query = f"SELECT {', '.join(select_columns)} FROM messages ORDER BY created_at DESC"
        cursor.execute(query)
        messages = cursor.fetchall()
        
        if not messages:
            debug_log("数据库中无消息记录")
            return "数据库中没有消息记录"

        total_count = len(messages)
        result = [f"数据库消息总数: {total_count}"]
        
        # 只显示最近的20条消息，防止消息过多导致群聊内容过长
        cst_tz = datetime.timezone(datetime.timedelta(hours=8)) # 定义CST时区（UTC+8）
        for i, message_data in enumerate(messages[:20], 1): 
            group_id = message_data[0]
            user_id = message_data[1]
            message_id = message_data[2]
            raw_message = message_data[3]
            recalled = message_data[4] if "recalled" in columns else 0
            created_at = message_data[5] if "created_at" in columns else "未知"
            
            created_at_display = "未知"
            if created_at != "未知":
                try:
                    # 假设数据库存储的是ISO格式的UTC时间字符串
                    dt_utc = datetime.datetime.fromisoformat(created_at).replace(tzinfo=datetime.timezone.utc)
                    created_at_cst = dt_utc.astimezone(cst_tz)
                    created_at_display = created_at_cst.strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    debug_log(f"查询数据库：无法解析时间字符串: {created_at}")
                    oldest_message_display = created_at # 解析失败时显示原始字符串
            
            group_str = f"群号: {group_id}" if group_id else "私聊"
            status = "已撤回" if recalled else "未撤回"
            result.append(
                f"消息 {i}:\n"
                f"{group_str}\n"
                f"用户ID: {user_id}\n"
                f"消息ID: {message_id}\n"
                f"状态: {status}\n"
                f"创建时间: {created_at_display}\n"
                f"内容: {raw_message[:50]}{'...' if len(raw_message) > 50 else ''}"
            )
        
        return "\n\n".join(result)
    
    except Exception as e:
        debug_log(f"查询数据库错误: {str(e)}")
        return f"查询数据库失败: {str(e)}"
    
    finally:
        conn.close()

async def query_commands() -> str:
    # 临时硬编码指令列表，因为动态指令加载正在重构中
    commands_help = {
        "查数据库": "查询数据库统计信息",
        "撤回 [数量]": "撤回指定数量的消息（默认20条）",
        "定时 [开启/关闭] [间隔]": "配置定时撤回任务",
        "指令": "显示帮助信息",
        "清理数据库 [天数]": "清理过期的已撤回消息（默认7天）",
        "清理全部已撤回": "清理所有已撤回消息",
        "数据库统计": "显示数据库详细统计",
        "历史消息 [数量]": "获取群历史消息（默认20条）",
        "导出数据库": "导出数据库为Excel文件"
    }
    
    result = ["支持的指令："]
    for cmd, desc in commands_help.items():
        result.append(f"{cmd}: {desc}")
    return "\n".join(result)

async def auto_recall_message(ws: WebSocket, message_id: int, delay: int = 30):
    await asyncio.sleep(delay)
    # 调用 force_recall_message 来执行自动撤回，它会处理所有的响应和数据库更新逻辑
    debug_log(f"执行自动撤回 (延迟 {delay}s): 消息ID {message_id}")
    await force_recall_message(ws, message_id)


async def process_message(raw_message_input: str, group_id: Optional[int], user_id: int, message_id: Optional[int], ws: WebSocket, self_id: int) -> tuple[str, bool, Optional[int]]:
    debug_log(f"process_message called: raw_message_input='{raw_message_input.strip()}', group_id={group_id}, user_id={user_id}, message_id={message_id}")
    global group_timers

    # 统一获取一个纯净的消息文本，用于所有基于文本的命令匹配和解析
    cleaned_msg = remove_cq_codes(raw_message_input).strip()
    debug_log(f"process_message cleaned_msg for parsing: '{cleaned_msg}'")

    if user_id == self_id:
        debug_log(f"跳过机器人自身消息的链接转换处理: {cleaned_msg}")
        return "", False, None 

    # is_command 的判断逻辑
    is_command = (
        cleaned_msg == "查数据库" or 
        cleaned_msg.startswith("撤回") or 
        cleaned_msg.startswith("定时") or 
        cleaned_msg == "指令" or 
        cleaned_msg == "清理数据库" or
        cleaned_msg == "清理全部已撤回" or
        (cleaned_msg.startswith("清理") and "天" in cleaned_msg) or
        cleaned_msg == "数据库统计" or
        cleaned_msg == "历史消息" or 
        cleaned_msg == "导出数据库" or 
        # 对于包含 CQ 码的命令，需要检查 raw_message_input，同时也要看 cleaned_msg 的文本内容
        (raw_message_input.startswith("[CQ:at,qq=") and (
            "撤回" in cleaned_msg or 
            "查数据库" in cleaned_msg or 
            "指令" in cleaned_msg or 
            "清理数据库" in cleaned_msg or 
            "清理全部已撤回" in cleaned_msg or 
            "数据库统计" in cleaned_msg or 
            ("清理" in cleaned_msg and "天" in cleaned_msg) or 
            "历史消息" in cleaned_msg or
            "导出数据库" in cleaned_msg 
        )) or 
        (raw_message_input.startswith("[CQ:reply,id=") and "撤回" in cleaned_msg) 
    )
    debug_log(f"is_command判断 (cleaned_msg='{cleaned_msg}'): {is_command}")

    # 初始化返回值
    reply_content = ""
    needs_auto_recall_reply = False 
    quoted_msg_id_to_recall: Optional[int] = None 

    # 先处理清理相关指令
    cleanup_result = await add_cleanup_commands_to_process_message(cleaned_msg)
    if cleanup_result is not None:
        reply_content, needs_auto_recall_reply = cleanup_result
        return reply_content, needs_auto_recall_reply, None 
    
    # 处理引用撤回指令
    reply_match = re.search(r'\[CQ:reply,id=(\d+)\]', raw_message_input)
    if reply_match and "撤回" in cleaned_msg: 
        quoted_msg_id_to_recall = int(reply_match.group(1))
        reply_content = f"好的，我将尝试撤回您引用的消息 (ID: {quoted_msg_id_to_recall})。"
        needs_auto_recall_reply = True 
        return reply_content, needs_auto_recall_reply, quoted_msg_id_to_recall 
    
    # 其他指令处理逻辑
    if is_command: 
        
        # 新增：撤回id xxx 指令
        recall_id_match = re.search(r'撤回id\s*(\d+)', cleaned_msg)
        if recall_id_match:
            try:
                target_message_id = int(recall_id_match.group(1))
                debug_log(f"收到撤回id指令：尝试撤回消息ID {target_message_id}")
                reply_content = await force_recall_message(ws, target_message_id)
                needs_auto_recall_reply = True
                return reply_content, needs_auto_recall_reply, None
            except ValueError:
                reply_content = "撤回id指令格式错误，正确格式：撤回id <消息ID>"
                needs_auto_recall_reply = True
                return reply_content, needs_auto_recall_reply, None

        # @某人 撤回 指令
        at_match = re.search(r'\[CQ:at,qq=(\d+)', raw_message_input) 
        if at_match and ("撤回" in cleaned_msg): 
            at_qq = int(at_match.group(1))
            
            command_part_after_at_raw = raw_message_input[at_match.end():]
            command_part_after_at_cleaned = remove_cq_codes(command_part_after_at_raw).strip()
            
            recall_count = None
            if "撤回全部" in command_part_after_at_cleaned:
                recall_count = None 
            else:
                n_match = re.search(r'撤回\s*(\d+)', command_part_after_at_cleaned)
                if n_match:
                    recall_count = int(n_match.group(1))
                elif "撤回" in command_part_after_at_cleaned: 
                    recall_count = None 

            if (recall_count is None and "撤回全部" not in command_part_after_at_cleaned and 
                "撤回" not in command_part_after_at_cleaned):
                 return "", False, None 

            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            
            query_messages = "SELECT message_id FROM messages WHERE group_id = ? AND user_id = ? AND recalled = 0 ORDER BY id DESC"
            params = (group_id, at_qq)
            if recall_count is not None:
                query_messages += " LIMIT ?"
                params += (recall_count,)

            cursor.execute(query_messages, params)
            messages_to_recall_rows = cursor.fetchall()
            conn.close()

            if not messages_to_recall_rows:
                reply_content = f"未找到用户 {at_qq} 的未撤回消息"
            else:
                message_ids = [row[0] for row in messages_to_recall_rows]
                tasks = [force_recall_message(ws, mid) for mid in reversed(message_ids) if isinstance(mid, int)]
                results = await asyncio.gather(*tasks)
                reply_content = f"尝试撤回用户 {at_qq} 的消息:\n" + "\n".join(results)
            needs_auto_recall_reply = True 
            return reply_content, needs_auto_recall_reply, None

        if cleaned_msg == "查数据库" or (raw_message_input.startswith("[CQ:at,qq=") and "查数据库" in cleaned_msg):
            reply_content = await query_database()
            needs_auto_recall_reply = True
            return reply_content, needs_auto_recall_reply, None

        # NEW: 导出数据库命令处理
        if cleaned_msg == "导出数据库" or (raw_message_input.startswith("[CQ:at,qq=") and "导出数据库" in cleaned_msg):
            reply_content = await export_database_to_excel()
            needs_auto_recall_reply = True 
            return reply_content, needs_auto_recall_reply, None

        # NEW: 历史消息命令处理
        if cleaned_msg == "历史消息" or (raw_message_input.startswith("[CQ:at,qq=") and "历史消息" in cleaned_msg):
            if group_id is None:
                reply_content = "私聊不支持获取历史消息功能"
                needs_auto_recall_reply = True
                return reply_content, needs_auto_recall_reply, None
            
            history_messages = await get_onebot_history_messages(ws, group_id=group_id, count=2000)
            
            if not history_messages:
                reply_content = f"未能获取到群 {group_id} 的历史消息，或没有新的消息。"
                needs_auto_recall_reply = True
                return reply_content, needs_auto_recall_reply, None
            
            # Store fetched messages in the database
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            # MODIFIED: Track inserted and ignored counts separately for better debugging message
            actual_inserted_count = 0 
            ignored_count = 0
            
            for msg_data in history_messages:
                msg_id_from_onebot = msg_data.get("message_id")
                msg_group_id = msg_data.get("group_id") 
                msg_user_id = msg_data.get("user_id")
                msg_raw_message = msg_data.get("raw_message") 
                
                if not msg_raw_message and "message" in msg_data and isinstance(msg_data["message"], list):
                    msg_raw_message = "".join([seg.get("data", {}).get("text", "") for seg in msg_data["message"] if seg.get("type") == "text"])
                    if not msg_raw_message: 
                         msg_raw_message = json.dumps(msg_data["message"], ensure_ascii=False)

                msg_time = msg_data.get("time") 
                
                if msg_id_from_onebot is not None and msg_group_id == group_id and msg_user_id is not None and msg_raw_message is not None:
                    created_at_iso = datetime.datetime.fromtimestamp(msg_time, tz=datetime.timezone.utc).isoformat() if msg_time else datetime.datetime.now(datetime.timezone.utc).isoformat()
                    
                    try:
                        cursor.execute(
                            "INSERT INTO messages (group_id, user_id, message_id, raw_message, recalled, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                            (msg_group_id, msg_user_id, msg_id_from_onebot, msg_raw_message, 0, created_at_iso) 
                        )
                        if cursor.rowcount > 0:
                            actual_inserted_count += cursor.rowcount 
                            debug_log(f"成功插入历史消息: group={msg_group_id}, msg_id={msg_id_from_onebot}, user={msg_user_id}, content='{msg_raw_message[:30]}...'")
                        else:
                            ignored_count += 1
                            debug_log(f"历史消息 {msg_id_from_onebot} (群 {msg_group_id}) 已存在，跳过插入。")

                    except sqlite3.IntegrityError as e:
                        # This block is technically redundant with ON CONFLICT IGNORE but kept for robustness
                        debug_log(f"插入历史消息 {msg_id_from_onebot} 冲突 (可能已存在): {e}")
                        ignored_count += 1
                    except Exception as e:
                        debug_log(f"插入历史消息 {msg_id_from_onebot} 失败: {str(e)}")
            
            conn.commit()
            conn.close()
            
            # MODIFIED: Refined the reply_content based on actual inserted count and ignored count
            if actual_inserted_count > 0:
                reply_content = f"已成功从OneBot获取并存储 {actual_inserted_count} 条群 {group_id} 的历史消息到数据库。"
                if ignored_count > 0:
                    reply_content += f" (其中 {ignored_count} 条已存在并被忽略)。"
            else:
                reply_content = f"未能获取到新的群 {group_id} 历史消息，或所有 {len(history_messages)} 条消息已存在。"
            
            needs_auto_recall_reply = True 
            return reply_content, needs_auto_recall_reply, None

        if cleaned_msg.startswith("定时"):
            if group_id is None:
                return "私聊不支持定时撤回功能", True, None
            
            if cleaned_msg.endswith("关"):
                if group_id in group_timers and group_timers[group_id].get('enabled'):
                    group_timers[group_id]['enabled'] = False
                    if group_timers[group_id].get('task'):
                        group_timers[group_id]['task'].cancel()
                        group_timers[group_id]['task'] = None
                    debug_log(f"群 {group_id} 定时撤回已禁用")
                    reply_content = f"群 {group_id} 定时撤回功能已关闭"
                else:
                    debug_log(f"群 {group_id} 定时撤回已是关闭状态")
                    reply_content = f"群 {group_id} 定时撤回功能已是关闭状态"
                needs_auto_recall_reply = True
                return reply_content, needs_auto_recall_reply, None
            else:
                parts = cleaned_msg.split() 
                if len(parts) != 2:
                    return "定时指令格式错误，正确格式：定时 5 或 定时关", True, None
                try:
                    interval = int(parts[1])
                    if interval <= 0:
                        return "定时间隔必须大于 0", True, None
                    
                    if group_id not in group_timers:
                        group_timers[group_id] = {'enabled': True, 'interval': interval, 'task': None}
                    else:
                        group_timers[group_id]['enabled'] = True
                        group_timers[group_id]['interval'] = interval
                        if group_timers[group_id].get('task'):
                            group_timers[group_id]['task'].cancel()
                            group_timers[group_id]['task'] = None 

                    group_timers[group_id]['task'] = asyncio.create_task(timer_recall_group(ws, group_id))
                    debug_log(f"群 {group_id} 定时撤回已启用，每 {interval} 分钟撤回")
                    reply_content = f"群 {group_id} 定时撤回功能已开启，每 {interval} 分钟撤回群内消息"
                    needs_auto_recall_reply = True
                    return reply_content, needs_auto_recall_reply, None
                except ValueError:
                    return "定时指令格式错误，正确格式：定时 5 或 定时关", True, None

        if cleaned_msg == "指令" or (raw_message_input.startswith("[CQ:at,qq=") and "指令" in cleaned_msg):
            reply_content = await query_commands()
            needs_auto_recall_reply = True
            return reply_content, needs_auto_recall_reply, None

        if cleaned_msg == "撤回全部" or (raw_message_input.startswith("[CQ:at,qq=") and "撤回全部" in cleaned_msg):
            reply_content = await recall_group_messages(ws, group_id)
            needs_auto_recall_reply = True
            return reply_content, needs_auto_recall_reply, None

        if cleaned_msg.startswith("撤回") and "撤回全部" not in cleaned_msg: 
            parts = cleaned_msg.split()
            count = 0
            if len(parts) == 1: 
                count = None 
            else: 
                try:
                    count = int(parts[1])
                except (IndexError, ValueError):
                    reply_content = "撤回指令格式错误，正确格式：撤回 5 或 撤回全部"
                    needs_auto_recall_reply = True
                    return reply_content, needs_auto_recall_reply, None
            
            if count is None: 
                reply_content = await recall_group_messages(ws, group_id)
            else:
                reply_content = await recall_messages(ws, group_id, count)
            
            needs_auto_recall_reply = True
            return reply_content, needs_auto_recall_reply, None
    
    else:
        text_for_conversion = cleaned_msg 
        cq_extracted_urls = extract_from_cq_json(raw_message_input) 
        combined_text = text_for_conversion + " " + cq_extracted_urls
        
        results = []
        processed_titles = set()
        
        for match in TAOBAO_REGEX.finditer(combined_text):
            token = match.group(0)
            if match.group(1): token = match.group(1)
            elif match.group(2): token = match.group(2)
            elif match.group(3): token = match.group(3)
            elif match.group(4): token = match.group(4)
            elif match.group(5): token = match = match.group(5)
            elif match.group(6): token = match.group(6)
            elif match.group(7): token = match.group(7)
            
            result = await convert_tkl(token, processed_titles)
            if result: results.append(result)
        
        for match in JD_REGEX.finditer(combined_text):
            token = match.group(0)
            result = await convert_jd_link(token, processed_titles)
            if result: results.append(result)
        
        if results:
            reply_content = "\n".join(results)
            needs_auto_recall_reply = False 
            return reply_content, needs_auto_recall_reply, None
    
    return "", False, None

async def _handle_message_event(event: Dict, websocket: WebSocket):
    """
    处理 OneBot 的 'message' 事件。
    此函数封装了原 custom_ws_adapter 中处理消息的全部逻辑，
    并在一个独立的 asyncio 任务中运行，以避免阻塞主 WebSocket 接收循环。
    """
    self_id = event.get("self_id")
    user_id = event.get("user_id")
    group_id = event.get("group_id")
    raw_message = event.get("raw_message", "")
    message_id = event.get("message_id") # 原始消息的 message_id

    # 2. 调用 process_message 处理消息（返利转换）- 已禁用，改用返利模块
    # reply_content = ""
    # try:
    #     # process_message 现在会在一开始就过滤机器人自身消息的链接转换
    #     reply_content, _, _ = await process_message(raw_message, group_id, user_id, message_id, websocket, self_id)
    # except Exception as e:
    #     debug_log(f"处理消息 {message_id} 错误: {str(e)}")
    #     reply_content = f"处理消息失败: {str(e)}"

    
    # 3. 调用 ModuleLoader 处理消息（线报收集等功能）
    if module_loader:
        try:
            from core.base_module import ModuleContext
            context = ModuleContext(
                user_id=user_id,
                group_id=group_id,
                message_id=message_id,
                self_id=self_id,
                ws=websocket,
                raw_message=raw_message,
            )

            
            # 让ModuleLoader处理消息
            module_response = await module_loader.process_message(raw_message, context)
            
            # 如果模块返回了响应，发送到群
            if module_response and module_response.content:
                verbose_log("module_handling", f"模块返回响应，准备发送")
                
                # 发送模块响应
                try:
                    import json
                    
                    # 根据 group_id 判断是群聊还是私聊
                    if group_id is None:
                        # 私聊消息
                        echo_prefix = "module_response_recall_" if (getattr(module_response, 'auto_recall', False)) else "module_response_"
                        reply_action = {
                            "action": "send_private_msg",
                            "params": {
                                "user_id": user_id,
                                "message": module_response.content
                            },
                            "echo": f"{echo_prefix}{message_id}"  # 添加echo以获取响应消息ID
                        }
                        verbose_log("module_handling", f"准备发送私聊消息到用户{user_id}")
                    else:
                        # 群聊消息
                        echo_prefix = "module_response_recall_" if (getattr(module_response, 'auto_recall', False)) else "module_response_"
                        if echo_prefix == "module_response_recall_":
                             verbose_log("module_handling", f"消息需自动撤回，使用echo前缀: {echo_prefix}")
                        
                        reply_action = {
                            "action": "send_group_msg",
                            "params": {
                                "group_id": group_id,
                                "message": module_response.content
                            },
                            "echo": f"{echo_prefix}{message_id}"  # 添加echo以获取响应消息ID
                        }
                        verbose_log("module_handling", f"准备发送群消息到群{group_id} (Echo: {echo_prefix}{message_id})")
                    
                    if websocket.client_state == WebSocketState.CONNECTED:
                        msg_content = reply_action['params']['message']
                        verbose_log("module_handling", f"发送消息长度: {len(msg_content)}")
                        if len(msg_content) > 4000:
                            print(f"[警告] 消息过长 ({len(msg_content)} chars)，可能导致发送失败")
                            
                        await websocket.send_text(json.dumps(reply_action))
                        
                        if group_id is None:
                            verbose_log("module_handling", f"已发送模块响应到用户{user_id}")
                        else:
                            verbose_log("module_handling", f"已发送模块响应到群{group_id}")
                        
                        # 检查是否需要自动撤回
                        if hasattr(module_response, 'auto_recall') and module_response.auto_recall:
                            recall_delay = getattr(module_response, 'recall_delay', 3)  # 默认3秒
                            print(f"[ModuleLoader] 将在 {recall_delay} 秒后自动撤回响应消息")
                            
                            # 创建异步任务来延迟撤回
                            async def auto_recall_task():
                                await asyncio.sleep(recall_delay)
                                # 这里需要等待获取响应消息ID，然后撤回
                                # 由于OneBot响应是异步的，我们需要在echo响应中处理
                                pass
                            
                            asyncio.create_task(auto_recall_task())
                    else:
                        print(f"[ModuleLoader] WebSocket未连接，无法发送响应")
                except Exception as send_error:
                    print(f"[ModuleLoader] 发送模块响应失败: {send_error}")
        except Exception as e:
            print(f"[错误] ModuleLoader处理消息失败: {e}")
            import traceback
            traceback.print_exc()


async def custom_ws_adapter(websocket: WebSocket):

    global has_printed_watched_groups
    await websocket.accept()
    debug_log("WebSocket连接已创建")
    
    # 保存连接的 QQ 号，用于断开时显示
    connected_qq = None

    # 启动重试失败撤回的后台任务
    retry_task = asyncio.create_task(retry_failed_recalls(websocket))

    try:
        while True:
            data = await websocket.receive_text()
            debug_log(f"收到消息: {data}")
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                debug_log("消息解析失败: 非JSON格式")
                continue

            # --- 主要事件分发器 ---

            # Notice 事件 (例如消息撤回通知)
            if event.get("post_type") == "notice":
                notice_type = event.get("notice_type")
                if notice_type in ["group_recall", "friend_recall"]:
                    message_id_to_recall = event.get("message_id")
                    group_id = event.get("group_id") 
                    conn = sqlite3.connect(DB_FILE)
                    cursor = conn.cursor()
                    cursor.execute("UPDATE messages SET recalled = 1 WHERE message_id = ?", (message_id_to_recall,))
                    affected_rows = cursor.rowcount
                    if affected_rows > 0:
                        debug_log(f"处理撤回通知: 标记消息 {message_id_to_recall} 为已撤回 " + (f"来自群 {group_id}" if group_id else "【私聊】"))
                        if message_id_to_recall in pending_recall_messages:
                            pending_recall_messages.remove(message_id_to_recall)
                    else:
                        debug_log(f"撤回通知: 消息 {message_id_to_recall} 未在数据库中找到，插入已撤回记录")
                        cursor.execute(
                            "INSERT OR IGNORE INTO messages (group_id, user_id, message_id, raw_message, recalled) VALUES (?, ?, ?, ?, ?)",
                            (group_id, event.get("user_id", 0), message_id_to_recall, "[已撤回]", 1)
                        )
                    conn.commit()
                    conn.close()
                continue 

            # Meta Event
            if event.get("post_type") == "meta_event":
                # 处理 lifecycle 事件,打印连接的 QQ 号
                if event.get("meta_event_type") == "lifecycle" and event.get("sub_type") == "connect":
                    self_id = event.get("self_id")
                    if self_id:
                        connected_qq = self_id
                        # online_bots.add(self_id)  # 记录在线状态
                        bot_manager.add_bot(self_id, websocket)
                        print(f"[系统] {SUCCESS} 成功连接到 QQ: {green(str(self_id))}")
                        print(f"[系统] 当前在线机器人: {blue(str(sorted(bot_manager.get_online_bots())))}")
                        
                        # 获取该机器人的群列表（用于优先级判断）
                        try:
                            payload = {
                                "action": "get_group_list",
                                "echo": f"system_get_group_list_{self_id}"
                            }
                            await websocket.send_text(json.dumps(payload))
                            print(f"[系统] 已请求获取 QQ {self_id} 的群列表")
                        except Exception as e:
                            print(f"[系统] ❌ 请求群列表失败: {e}")
                
                # 处理心跳事件，确保在线状态持续更新
                elif event.get("meta_event_type") == "heartbeat":
                    self_id = event.get("self_id")
                    current_online_bots = bot_manager.get_online_bots()
                    if self_id and self_id not in current_online_bots:
                        # online_bots.add(self_id)  # 记录在线状态
                        bot_manager.add_bot(self_id, websocket)
                        print(f"[系统] ✅ 通过心跳检测到 QQ: {self_id} 在线")
                        print(f"[系统] 当前在线机器人: {sorted(bot_manager.get_online_bots())}")
                
                continue
 

            # Message Event - 派发到新的异步任务处理
            if event.get("post_type") == "message" and event.get("message_type") in ["private", "group"]:
                asyncio.create_task(_handle_message_event(event, websocket))
                continue 

            # Echo Event (OneBot 对之前请求的响应)
            if "echo" in event:
                echo = event["echo"]
                
                # 忽略 echo 为 None 的情况
                if echo is None:
                    continue

                if echo in pending_futures:
                    future = pending_futures.pop(echo) 
                    if not future.done(): 
                        future.set_result(event)
                    if not future.done(): 
                        future.set_result(event)
                    debug_log(f"设置Future结果 for echo: {echo}")
                
                # 处理系统获取群列表的响应
                elif echo.startswith("system_get_group_list_"):
                    if event.get("status") == "ok" and "data" in event:
                        try:
                            bot_id = int(echo.split("_")[-1])
                            groups = event.get("data", [])
                            group_ids = [g.get("group_id") for g in groups]
                            bot_manager.update_bot_groups(bot_id, group_ids)
                            print(f"[系统] ✅ 更新 QQ {bot_id} 的群列表缓存: 共 {len(group_ids)} 个群")
                        except Exception as e:
                            print(f"[系统] ❌ 处理群列表响应失败: {e}")
                
                elif echo in pending_requests:
                    message_id = pending_requests.pop(echo) 
                    conn = sqlite3.connect(DB_FILE)
                    cursor = conn.cursor()
                    if event.get("status") == "ok":
                        cursor.execute("UPDATE messages SET recalled = 1 WHERE message_id = ?", (message_id,))
                        affected_rows = cursor.rowcount
                        if affected_rows == 0:
                            debug_log(f"消息 {message_id} 未在数据库中找到，插入已撤回记录")
                            cursor.execute(
                                "INSERT INTO messages (message_id, raw_message, recalled) VALUES (?, ?, ?)",
                                (message_id, "[已撤回]", 1)
                            )
                        conn.commit()
                        if message_id in pending_recall_messages:
                            pending_recall_messages.remove(message_id)
                        debug_log(f"处理延迟响应: 成功撤回并标记消息 {message_id}")
                    else:
                        debug_log(f"处理延迟响应: 撤回消息 {message_id} 失败: {event.get('message', '未知错误')}，完整响应: {json.dumps(event)}")
                        pending_recall_messages.add(message_id) 
                    conn.close()
                
                # 处理群历史消息响应（用于批量撤回）
                elif echo and (echo.startswith("get_history_") or echo.startswith("get_all_history_")):
                    if event.get("status") == "ok" and "data" in event:
                        messages = event["data"].get("messages", [])
                        group_id = echo.split("_")[-1]  # 从echo中提取群号
                        
                        if messages:
                            if DEBUG_MODE:
                                print(f"[群管理模块] 收到 {len(messages)} 条历史消息，开始批量撤回...")
                            
                            # 获取所有机器人QQ列表，避免误撤回机器人发的消息（特别是返利消息）
                            bot_qq_list = get_bot_qq_list()
                            
                            # 遍历消息列表并撤回
                            recalled_count = 0
                            for msg in messages:
                                msg_id = msg.get("message_id")
                                user_id = msg.get("user_id")
                                
                                # 跳过已删除的消息
                                if msg.get("raw_message") in ["[已删除]", "&#91;已删除&#93;"]:
                                    continue
                                
                                # 跳过机器人的消息（防止误删返利线报）
                                # if user_id in bot_qq_list:
                                #     # print(f"[群管理模块] 跳过机器人的消息: user_id={user_id}")
                                #     continue
                                
                                # 发送撤回请求
                                try:
                                    recall_payload = {
                                        "action": "delete_msg",
                                        "params": {"message_id": msg_id},
                                        "echo": f"batch_recall_{msg_id}"
                                    }
                                    await websocket.send_text(json.dumps(recall_payload))
                                    recalled_count += 1
                                    debug_log(f"已发送撤回请求: message_id={msg_id}")
                                    
                                    # 添加小延迟，避免请求过快
                                    await asyncio.sleep(0.1)
                                    
                                except Exception as e:
                                    print(f"[群管理模块] ❌ 撤回消息 {msg_id} 失败: {e}")
                            
                            print(f"[群管理模块] ✅ 已发送 {recalled_count} 条撤回请求")
                        else:
                            print(f"[群管理模块] ⚠️ 未找到可撤回的消息")
                    else:
                        print(f"[群管理模块] ❌ 获取历史消息失败: {event.get('message', '未知错误')}")
                
                # 处理模块响应的echo（用于自动撤回）
                elif echo and echo.startswith("module_response_recall_"):
                    if event.get("status") == "ok" and "data" in event:
                        response_msg_id = event["data"].get("message_id")
                        if response_msg_id:
                            verbose_log("module_handling", f"模块响应消息ID: {response_msg_id}，准备延迟撤回")
                            
                            # 创建延迟撤回任务
                            async def delayed_recall(msg_id, delay=3):
                                await asyncio.sleep(delay)
                                try:
                                    recall_payload = {
                                        "action": "delete_msg",
                                        "params": {"message_id": msg_id},
                                        "echo": f"auto_recall_{msg_id}"
                                    }
                                    await websocket.send_text(json.dumps(recall_payload))
                                    verbose_log("module_handling", f"已自动撤回响应消息: {msg_id}")
                                except Exception as e:
                                    debug_log(f"[ModuleLoader] 自动撤回失败: {e}")
                            
                            asyncio.create_task(delayed_recall(response_msg_id))

                # 处理撤回响应（检查权限错误）
                elif echo and echo.startswith("recall_"):
                    if event.get("status") == "failed":
                        retcode = event.get("retcode")
                        if retcode == 200:
                            print(f"[群管理模块] {ERROR} 撤回失败: 权限不足 (200) - 机器人无法撤回管理员/群主消息，或消息已失效")
                        elif retcode == 100:
                            print(f"[群管理模块] {ERROR} 撤回失败: 参数错误 (100)")
                
                # 处理特定用户历史消息响应（用于@撤回）
                elif echo and echo.startswith("get_user_history_"):
                    if event.get("status") == "ok" and "data" in event:
                        messages = event["data"].get("messages", [])
                        # echo格式: get_user_history_{group_id}_{target_qq}_{limit_count}
                        parts = echo.split("_")
                        # parts: ['get', 'user', 'history', group_id, target_qq, limit_count]
                        if len(parts) >= 6:
                            target_qq = int(parts[4])
                            limit_count = int(parts[5])
                            
                            if messages:
                                if DEBUG_MODE:
                                    print(f"[群管理模块] 收到 {len(messages)} 条历史消息，正在筛选用户 {target_qq} 的消息...")
                                
                                recalled_count = 0
                                for msg in messages:
                                    if recalled_count >= limit_count:
                                        break
                                        
                                    if str(msg.get("user_id")) == str(target_qq):
                                        msg_id = msg.get("message_id")
                                        
                                        # 跳过已删除的消息
                                        if msg.get("raw_message") in ["[已删除]", "&#91;已删除&#93;"]:
                                            continue
                                            
                                        # 发送撤回请求
                                        try:
                                            recall_payload = {
                                                "action": "delete_msg",
                                                "params": {"message_id": msg_id},
                                                "echo": f"user_recall_{msg_id}"
                                            }
                                            await websocket.send_text(json.dumps(recall_payload))
                                            recalled_count += 1
                                            debug_log(f"已发送撤回请求: message_id={msg_id}")
                                            await asyncio.sleep(0.1)
                                        except Exception as e:
                                            print(f"[群管理模块] ❌ 撤回消息 {msg_id} 失败: {e}")
                                            
                                if DEBUG_MODE:
                                    print(f"[群管理模块] ✅ 已发送 {recalled_count} 条针对用户 {target_qq} 的撤回请求")
                            else:
                                print(f"[群管理模块] ⚠️ 未找到消息")
                        else:
                            print(f"[群管理模块] ❌ Echo格式错误: {echo}")
                    else:
                        print(f"[群管理模块] ❌ 获取历史消息失败: {event.get('message', '未知错误')}")


                
                elif echo and echo.startswith("module_response_"):
                     if event.get("status") == "failed" and event.get("retcode") == 9057:
                         print(f"[ModuleLoader] ❌ 发送响应消息失败: 消息内容过长 (9057)。请检查模块返回内容。")
                     pass
                
                else:
                    debug_log(f"收到非预期或已处理的echo: {echo}, 事件: {event}") 
            else:
                debug_log(f"收到非消息类型事件，且不含echo: {event.get('post_type')}") 

    except Exception as e:
        debug_log(f"WebSocket错误: {str(e)}")
        error_msg = str(e)
        if error_msg.strip(): # 避免打印空错误
             print(f"[系统] ❌ QQ {connected_qq or '未知'} 连接已断开: {error_msg}")
        else:
             print(f"[系统] ❌ QQ {connected_qq or '未知'} 连接已断开")

    finally:
        retry_task.cancel()
        
        # 无论连接状态如何，只要有已连接的QQ，就进行清理和通知
        if connected_qq:
            # 尝试通过模块通知离线
            try:
                if module_loader:
                    notifier = module_loader.get_module("离线通知模块")
                    if notifier and hasattr(notifier, "send_offline_notification"):
                        await notifier.send_offline_notification(connected_qq)
            except Exception as notify_err:
                 print(f"[系统] ⚠️ 离线通知调用失败: {notify_err}")

            bot_manager.remove_bot(connected_qq)
            print(f"[系统] ❌ QQ {connected_qq} 连接已关闭")
            print(f"[系统] 当前在线机器人: {sorted(bot_manager.get_online_bots())}")

        if websocket.client_state != WebSocketState.DISCONNECTED:
            await websocket.close()
            debug_log("WebSocket连接已关闭")

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """应用生命周期管理"""
    async def run_websocket():
        while True:
            try:
                app.add_api_websocket_route("/onebot/v11/ws", custom_ws_adapter)
                debug_log("WebSocket路由已添加")
                break 
            except Exception as e:
                debug_log(f"WebSocket初始化失败: {str(e)}，2秒后重试")
                await asyncio.sleep(2)

    await start_cleanup_scheduler()
    
    # ===== 初始化 ModuleLoader 和加载模块 =====
    global module_loader
    debug_log("应用生命周期开始")
    
    # 初始化模块加载器
    module_loader = ModuleLoader()
    print("[系统] ModuleLoader 已初始化")
    
    # 从配置加载模块
    from config import (
        NEWS_COLLECTOR_CONFIG,
        NEWS_FORWARDER_CONFIG,
        REBATE_MODULE_CONFIG,
        GROUP_ADMIN_CONFIG,
        OFFLINE_NOTIFIER_CONFIG,
    )
    
    # 加载线报收集模块
    if NEWS_COLLECTOR_CONFIG.get('enabled', True):
        try:
            await module_loader.load_module_from_path('modules/news_jd', NEWS_COLLECTOR_CONFIG)
            print("[系统] 线报收集模块加载成功")
        except Exception as e:
            print(f"[系统] 线报收集模块加载失败: {e}")
            import traceback
            traceback.print_exc()
    
    # 加载线报转发模块
    if NEWS_FORWARDER_CONFIG.get('enabled', True):
        try:
            await module_loader.load_module_from_path('modules/news_forwarder', NEWS_FORWARDER_CONFIG)
            print("[系统] 线报转发模块加载成功")
        except Exception as e:
            print(f"[系统] 线报转发模块加载失败: {e}")
            import traceback
            traceback.print_exc()
    
    # 加载返利模块
    if REBATE_MODULE_CONFIG.get('enabled', True):
        try:
            await module_loader.load_module_from_path('modules/rebate', REBATE_MODULE_CONFIG)
            print("[系统] 返利模块加载成功")
        except Exception as e:
            print(f"[系统] 返利模块加载失败: {e}")
            import traceback
            traceback.print_exc()
    
    # 加载群管理模块
    if GROUP_ADMIN_CONFIG.get('enabled', True):
        try:
            await module_loader.load_module_from_path('modules/group_admin', GROUP_ADMIN_CONFIG)
            print("[系统] 群管理模块加载成功")
        except Exception as e:
            print(f"[系统] 群管理模块加载失败: {e}")
            import traceback
            traceback.print_exc()

    # 加载离线通知模块
    if OFFLINE_NOTIFIER_CONFIG.get('enabled', True):
        try:
            await module_loader.load_module_from_path('modules/offline_notifier', OFFLINE_NOTIFIER_CONFIG)
            print("[系统] 离线通知模块加载成功")
        except Exception as e:
            print(f"[系统] 离线通知模块加载失败: {e}")
            import traceback
            traceback.print_exc()
    
    # 启动数据库清理任务
    from modules.news_database import news_db
    asyncio.create_task(news_db.start_cleanup_task(interval=10, retention_seconds=40))
    print("[系统] 线报数据库清理任务已启动（每60秒清理一次）")
    
    await asyncio.gather(
        run_websocket()
    )
    
    yield 
    debug_log("应用生命周期结束")

def debug_log_full_api(base_url: str, params: dict):
    full_url = f"{base_url}?{'&'.join(f'{k}={quote(str(v))}' for k, v in params.items())}"
    debug_log(f"[API MATCHED] 完整API URL: {full_url}")

if __name__ == "__main__":
    debug_log("启动 FastBot 应用")
    FastBot.build(plugins=["./plugins"], lifespan=lifespan).run(host="0.0.0.0", port=5670)
