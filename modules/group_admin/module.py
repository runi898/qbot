"""
群管理模块 - QQ群管理功能

支持的功能：
- 群消息撤回（多种方式）
"""

import re
import json
import sys
import os
from typing import Optional
from core.base_module import BaseModule, ModuleContext, ModuleResponse
import main
from config import get_bot_qq_list, BOT_PRIORITY, DEBUG_MODE

# 导入京东短链转换器
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'news_jd'))
from dwz import JDShortUrlConverter



class GroupAdminModule(BaseModule):
    """群管理模块"""
    
    def __init__(self):
        super().__init__()
        self.priority = 15  # 高优先级
        
    @property
    def name(self) -> str:
        return "群管理模块"
    
    @property
    def version(self) -> str:
        return "1.0.0"
    
    @property
    def description(self) -> str:
        return "QQ群管理功能，支持消息撤回等操作"
    
    @property
    def author(self) -> str:
        return "QBot Team"
    
    async def on_load(self, config: dict) -> None:
        """模块加载时初始化"""
        await super().on_load(config)
        
        # 保存配置
        self.config = config
        settings = config.get('settings', {})
        
        # 获取机器人列表（必须）
        self.bot_qq_list = get_bot_qq_list()
        
        # 监听群列表
        self.watched_groups = settings.get('watched_groups', [])
        
        # 机器人优先级列表
        self.bot_priority = BOT_PRIORITY
        if DEBUG_MODE:
            print(f"[{self.name}] 机器人优先级配置: {self.bot_priority}")
        
        # 管理员QQ列表
        self.admin_qq_list = settings.get('admin_qq_list', [])
        
        # 编译正则表达式
        # 匹配: 撤回 123456 或 撤回 5 或 撤回全部
        self.recall_pattern = re.compile(r'^撤回\s*(.+)$', re.IGNORECASE)
        # 匹配引用撤回: [CQ:reply,id=123456]...撤回（中间可以有@、空格等其他内容）
        self.reply_recall_pattern = re.compile(r'\[CQ:reply,id=(\d+)\].*?撤回', re.IGNORECASE)
        # 匹配@撤回: [CQ:at,qq=123456]...撤回 或 [CQ:at,qq=123456]...撤回 5
        self.at_recall_pattern = re.compile(r'\[CQ:at,qq=(\d+).*?\].*?撤回(?:\s+(\d+))?', re.IGNORECASE)
        # 匹配 dwz 指令: dwz 京东链接
        self.dwz_pattern = re.compile(r'^dwz\s+(https?://[^\s]+)', re.IGNORECASE)
        
        # 初始化京东短链转换器
        self.jd_converter = JDShortUrlConverter(sign_url="http://192.168.8.2:3001/sign")
        
        print(f"[{self.name}] 模块已加载 (v{self.version})")
        print(f"[{self.name}] 监听群: {self.watched_groups}")
        print(f"[{self.name}] 管理员: {self.admin_qq_list}")
        print(f"[{self.name}] 机器人列表: {self.bot_qq_list}")
    
    async def get_bot_role_in_group(self, context: ModuleContext) -> Optional[str]:
        """
        查询当前机器人在群中的角色
        
        Args:
            context: 消息上下文
            
        Returns:
            角色字符串：'owner'(群主), 'admin'(管理员), 'member'(普通成员), None(查询失败)
        """
        try:
            # 调用OneBot API查询群成员信息
            payload = {
                "action": "get_group_member_info",
                "params": {
                    "group_id": context.group_id,
                    "user_id": context.self_id,
                    "no_cache": True  # 不使用缓存，获取实时信息
                },
                "echo": f"check_bot_role_{context.self_id}_{context.group_id}"
            }
            
            await context.ws.send_text(json.dumps(payload))
            
            if DEBUG_MODE:
                print(f"[{self.name}] 已发送群成员信息查询请求: bot={context.self_id}, group={context.group_id}")
            
            # 注意：这里只是发送请求，实际响应需要在WebSocket事件处理中接收
            # 由于是异步操作，我们无法在这里直接等待响应
            # 简化方案：假设配置的机器人都有权限
            return None
            
        except Exception as e:
            print(f"[{self.name}] ❌ 查询群成员信息失败: {e}")
            return None
    
    def get_bot_priority(self, bot_id: int) -> int:
        """
        获取机器人的优先级（数字越小优先级越高）
        
        Args:
            bot_id: 机器人QQ号
            
        Returns:
            优先级（0表示最高优先级，-1表示不在列表中）
        """
        try:
            return self.bot_qq_list.index(bot_id)
        except ValueError:
            return -1  # 不在列表中
    
    def should_respond_by_priority(self, context: ModuleContext) -> bool:
        """
        判断当前机器人是否应该响应(基于优先级和在线状态)
        只有优先级最高的在线机器人才响应
        
        Args:
            context: 消息上下文
            
        Returns:
            是否应该响应
        """
        # 如果没有配置优先级列表，默认只有列表中的第一个管理员响应
        if not self.bot_priority:
            return True
            
        current_bot = context.self_id
        
        # 总是输出基本信息用于调试
        if DEBUG_MODE:
            print(f"[{self.name}] === 优先级检查开始 ===")
            print(f"[{self.name}] 当前机器人: {current_bot}")
            print(f"[{self.name}] 优先级列表: {self.bot_priority}")
        
        # 如果当前机器人不在优先级列表中,默认不响应（除非列表为空）
        if current_bot not in self.bot_priority:
            if DEBUG_MODE:
                print(f"[{self.name}] 当前机器人({current_bot})不在优先级列表中,不响应")
            return False
        
        # 获取在线机器人列表
        from core import bot_manager
        online_bots = bot_manager.get_online_bots()
        if DEBUG_MODE:
            print(f"[{self.name}] 当前在线机器人: {sorted(online_bots)}")
        
        # 找出在线且在当前群中的优先级机器人
        target_bot = None
        for bot_id in self.bot_priority:
            # 1. 检查是否在线
            if bot_id not in online_bots:
                continue
                
            # 2. 检查是否在当前群中
            # 如果是私聊（context.group_id is None），只需要在线即可（或者是第一个在线的优先级机器人）
            if context.group_id:
                if bot_manager.is_bot_in_group(bot_id, context.group_id):
                    target_bot = bot_id
                    break
            else:
                # 私聊情况，取第一个在线的
                target_bot = bot_id
                break
        
        if target_bot is None:
            # 没有合适的机器人在线或在群中
            if DEBUG_MODE:
                print(f"[{self.name}] 没有找到合适的机器人处理（都在线但都不在群？）")
            return False
        
        should_respond = (current_bot == target_bot)
        
        if DEBUG_MODE:
            print(f"[{self.name}] 本群({context.group_id}) 应响应机器人: {target_bot}")
            print(f"[{self.name}] 当前机器人({current_bot}) {'应该' if should_respond else '不应该'}响应")
            print(f"[{self.name}] === 优先级检查结束 ===")
        
        return should_respond

    async def can_handle(self, message: str, context: ModuleContext) -> bool:
        """判断是否能处理该消息"""
        
        # 1. 过滤机器人消息（必须）
        if context.user_id in self.bot_qq_list:
            if DEBUG_MODE:
                print(f"[{self.name}] 跳过机器人消息: {context.user_id}")
            return False
        
        # 2. 只处理群消息
        if context.group_id is None:
            return False
        
        # 3. 群组过滤
        if context.group_id not in self.watched_groups:
            return False
        
        # 4. 权限检查：只有管理员可以使用
        if context.user_id not in self.admin_qq_list:
            if DEBUG_MODE:
                print(f"[{self.name}] 用户 {context.user_id} 不是管理员，无权使用群管理功能")
            return False
        
        # 5. 机器人优先级检查：只有优先级最高的在线机器人才尝试
        if not self.should_respond_by_priority(context):
            return False
        
        # 6. 内容匹配：检查是否是撤回指令（包括引用撤回和@撤回）或 dwz 指令
        # 增加对新指令的匹配
        msg = message.strip()
        if msg in ["数据库统计", "清理数据库", "清理全部已撤回", "导出数据库"] or \
           (msg.startswith("清理") and "天" in msg) or \
           msg.startswith("定时"):
            return True

        return bool(self.recall_pattern.search(message) or 
                   self.reply_recall_pattern.search(message) or 
                   self.at_recall_pattern.search(message) or
                   self.dwz_pattern.search(message))
    
        
    # ===== 扩展功能：数据库管理与定时任务 =====

    async def get_db_stats(self) -> str:
        """获取数据库统计信息"""
        try:
            import sqlite3
            conn = sqlite3.connect('messages.db')
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM messages")
            total_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM messages WHERE recalled=1")
            recalled_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT MIN(created_at), MAX(created_at) FROM messages")
            min_time, max_time = cursor.fetchone()
            
            # 获取最近24小时的消息量
            cursor.execute("SELECT COUNT(*) FROM messages WHERE created_at > datetime('now', '-1 day')")
            today_count = cursor.fetchone()[0]
            
            conn.close()
            
            return (f"📊 数据库统计:\n"
                    f"- 总消息数: {total_count}\n"
                    f"- 已撤回数: {recalled_count}\n"
                    f"- 今日新增: {today_count}\n"
                    f"- 最早记录: {min_time}\n"
                    f"- 最新记录: {max_time}")
        except Exception as e:
            return f"❌ 获取统计失败: {e}"

    async def clean_db(self, days: int = 7) -> str:
        """清理指定天数前的已撤回消息"""
        try:
            import sqlite3
            conn = sqlite3.connect('messages.db')
            cursor = conn.cursor()
            
            # 仅清理已标记为撤回且超过指定天数的消息
            cursor.execute(f"DELETE FROM messages WHERE recalled=1 AND created_at < datetime('now', '-{days} days')")
            deleted_count = cursor.rowcount
            
            conn.commit()
            conn.close()
            
            return f"✅ 已清理 {days} 天前的已撤回消息，共删除 {deleted_count} 条记录。"
        except Exception as e:
            return f"❌ 清理失败: {e}"
            
    async def clean_all_recalled(self) -> str:
        """清理所有已撤回消息"""
        try:
            import sqlite3
            conn = sqlite3.connect('messages.db')
            cursor = conn.cursor()
            
            cursor.execute("DELETE FROM messages WHERE recalled=1")
            deleted_count = cursor.rowcount
            
            conn.commit()
            conn.close()
            
            return f"✅ 已清理所有已撤回消息，共删除 {deleted_count} 条记录。"
        except Exception as e:
            return f"❌ 清理失败: {e}"

    async def start_scheduled_recall(self, context: ModuleContext, interval_minutes: int) -> str:
        """启动定时撤回任务"""
        import asyncio
        group_id = context.group_id
        if not group_id:
            return "❌ 只能在群聊中使用定时任务"

        # 如果已有任务，先取消
        if hasattr(self, 'scheduled_tasks') and group_id in self.scheduled_tasks:
            self.scheduled_tasks[group_id].cancel()
        
        if not hasattr(self, 'scheduled_tasks'):
            self.scheduled_tasks = {}

        # 定义任务函数
        async def recall_task():
            try:
                while True:
                    await asyncio.sleep(interval_minutes * 60)
                    if DEBUG_MODE:
                        print(f"[{self.name}] 执行定时撤回任务: 群 {group_id}")
                    # 每次撤回最近 10 条
                    await self.recall_recent_messages(context, 10)
            except asyncio.CancelledError:
                print(f"[{self.name}] 定时撤回任务已停止: 群 {group_id}")

        # 启动任务
        task = asyncio.create_task(recall_task())
        self.scheduled_tasks[group_id] = task
        
        return f"✅ 已启动定时撤回任务: 群 {group_id}\n⏱️ 间隔: {interval_minutes} 分钟\n🔄 每次撤回: 最近 10 条"

    async def stop_scheduled_recall(self, context: ModuleContext) -> str:
        """停止定时撤回任务"""
        group_id = context.group_id
        if hasattr(self, 'scheduled_tasks') and group_id in self.scheduled_tasks:
            self.scheduled_tasks[group_id].cancel()
            del self.scheduled_tasks[group_id]
            return f"✅ 已停止本群的定时撤回任务"
        else:
            return "⚠️ 本群当前没有运行中的定时撤回任务"

    # ===== 修改 handle 方法分发新指令 =====

    async def handle(self, message: str, context: ModuleContext) -> Optional[ModuleResponse]:
        """处理消息并返回响应"""
        try:
            msg = message.strip()
            
            # --- 数据库指令 ---
            if msg == "数据库统计":
                return ModuleResponse(content=await self.get_db_stats())
            
            elif msg == "清理数据库":
                # 默认清理7天前
                return ModuleResponse(content=await self.clean_db(7))
            
            elif msg == "清理全部已撤回":
                return ModuleResponse(content=await self.clean_all_recalled())
                
            elif msg.startswith("清理") and "天" in msg:
                # 提取天数: "清理3天" -> 3
                try:
                    days = int(re.search(r'\d+', msg).group())
                    return ModuleResponse(content=await self.clean_db(days))
                except:
                    return ModuleResponse(content="❌ 清理指令格式错误，请使用 '清理N天' (N为数字)")
            
            # --- 定时任务指令 ---
            elif msg.startswith("定时"):
                # "定时 5" -> 每5分钟撤回
                try:
                    parts = msg.split()
                    if len(parts) > 1 and parts[1].isdigit():
                        interval = int(parts[1])
                        return ModuleResponse(content=await self.start_scheduled_recall(context, interval))
                    elif "停止" in msg or "取消" in msg:
                        return ModuleResponse(content=await self.stop_scheduled_recall(context))
                    else:
                        return ModuleResponse(content="❌ 定时任务指令格式错误，请使用 '定时 N' 或 '定时 停止'")
                except Exception as e:
                    return ModuleResponse(content=f"❌ 定时任务启动失败: {e}")

            # --- 原有撤回/dwz指令 --- (保持原有逻辑)
            
            # 0. 检查 dwz 指令
            dwz_match = self.dwz_pattern.search(message)
            if dwz_match:
                jd_url = dwz_match.group(1)
                if DEBUG_MODE:
                    print(f"[{self.name}] 检测到 dwz 指令，目标链接: {jd_url}")
                
                try:
                    # 调用转换器（静默模式）
                    result = self.jd_converter.convert(jd_url, verbose=False)
                    
                    if result['success']:
                        short_url = result['short_url']
                        response_text = f"✅ 短链接转换成功\n{short_url}"
                    else:
                        error_msg = result.get('error', '未知错误')
                        response_text = f"❌ 转换失败: {error_msg}"
                    
                    return ModuleResponse(
                        content=response_text,
                        auto_recall=False  # 不自动撤回
                    )
                except Exception as e:
                    if DEBUG_MODE:
                        print(f"[{self.name}] dwz 转换异常: {str(e)}")
                    return ModuleResponse(
                        content=f"❌ 转换异常: {str(e)}",
                        auto_recall=False  # 不自动撤回
                    )
            
            # 1. 优先检查引用撤回
            reply_match = self.reply_recall_pattern.search(message)
            if reply_match:
                # 提取被引用的消息ID
                replied_msg_id = int(reply_match.group(1))
                print(f"[{self.name}] 检测到引用撤回，目标消息ID: {replied_msg_id}")
                
                # 撤回被引用的消息
                result = await self.recall_message_by_id(context, replied_msg_id)
                
                # 注意：机器人无法撤回用户发送的指令消息（权限不足）
                # 用户需要自己手动撤回自己的消息
                
                # 返回响应，并在3秒后自动撤回机器人的响应消息
                return ModuleResponse(
                    content=result,
                    auto_recall=True,
                    recall_delay=3
                )
            
            # 2. 检查 @撤回
            at_match = self.at_recall_pattern.search(message)
            if at_match:
                target_qq = int(at_match.group(1))
                count_str = at_match.group(2)
                # 如果没有指定数量，默认为 100 (意味着撤回该用户近期所有消息)
                count = int(count_str) if count_str else 100
                
                print(f"[{self.name}] 检测到@撤回，目标QQ: {target_qq}, 数量: {count}")
                result = await self.recall_messages_by_user(context, target_qq, count)
                
                # 尝试撤回指令消息本身（忽略可能的权限错误）
                if context.message_id:
                    await self.recall_message_by_id(context, context.message_id)
                
                return ModuleResponse(
                    content=result,
                    auto_recall=True,
                    recall_delay=3
                )

            # 3. 匹配普通撤回指令
            match = self.recall_pattern.search(message)
            if not match:
                return None
            
            param = match.group(1).strip()
            
            # 判断撤回类型
            if param == "全部":
                # 撤回全部消息
                result = await self.recall_all_messages(context)
            elif param.isdigit():
                # 撤回N条消息或撤回指定message_id
                value = int(param)
                # 简单判断：如果数字较小（<100）且不是明显的消息ID长度（消息ID通常用于精确撤回，比较长）
                # 为了区分 "撤回 5" 和 "撤回 12345678"
                # 这里假设小于 50 的数字是撤回最近N条
                if value <= 50:
                    # 撤回最近N条消息
                    result = await self.recall_recent_messages(context, value)
                else:
                    # 撤回指定message_id
                    result = await self.recall_message_by_id(context, value)
            else:
                return ModuleResponse(
                    content=f"撤回指令格式错误\n支持的格式：\n- 撤回 <message_id>\n- 撤回 N（撤回最近N条消息）\n- 撤回全部\n- 引用撤回\n- @某人 撤回 [N]",
                    auto_recall=True,
                    recall_delay=3
                )
            
            # 尝试撤回指令消息本身（忽略可能的权限错误）
            if context.message_id:
                try:
                    # 对于普通撤回指令，我们也尝试撤回它
                    # 注意：如果是撤回N条，可能包含指令本身，这里为了保险再次尝试
                    await self.recall_message_by_id(context, context.message_id)
                except Exception:
                    pass
            
            # 返回响应，并在3秒后自动撤回机器人的响应消息
            return ModuleResponse(
                content=result,
                auto_recall=True,
                recall_delay=3  # 3秒后撤回
            )
            
        except Exception as e:
            print(f"[{self.name}] ❌ 处理失败: {e}")
            if DEBUG_MODE:
                import traceback
                traceback.print_exc()
            return ModuleResponse(
                content=f"撤回操作失败: {str(e)}",
                auto_recall=True,
                recall_delay=5  # 错误消息5秒后撤回
            )
    
    async def recall_message_by_id(self, context: ModuleContext, message_id: int) -> str:
        """
        撤回指定message_id的消息
        
        注意：由于OneBot API响应是异步的，我们只能发送请求，无法等待结果
        如果撤回失败（比如没有权限），OneBot会在日志中显示错误
        
        Args:
            context: 消息上下文
            message_id: 要撤回的消息ID
            
        Returns:
            操作结果
        """
        try:
            # 调用OneBot API撤回消息
            payload = {
                "action": "delete_msg",
                "params": {"message_id": message_id},
                "echo": f"recall_{message_id}"
            }
            
            await context.ws.send_text(json.dumps(payload))
            if DEBUG_MODE:
                print(f"[{self.name}] 已发送撤回请求: message_id={message_id}")
            
            return f"✅ 已发送撤回请求: 消息ID {message_id}"
            
        except Exception as e:
            print(f"[{self.name}] ❌ 撤回消息失败: {e}")
            return f"❌ 撤回失败: {str(e)}"
    
    async def recall_recent_messages(self, context: ModuleContext, count: int) -> str:
        """
        撤回最近的N条消息
        
        Args:
            context: 消息上下文
            count: 要撤回的消息数量
            
        Returns:
            操作结果
        """
        try:
            # 通过OneBot API获取群历史消息
            history_payload = {
                "action": "get_group_msg_history",
                "params": {
                    "group_id": context.group_id,
                    "count": count + 10  # 多获取一些，以防有些消息无法撤回
                },
                "echo": f"get_history_{context.group_id}"
            }
            
            await context.ws.send_text(json.dumps(history_payload))
            if DEBUG_MODE:
                print(f"[{self.name}] 已请求获取群 {context.group_id} 的历史消息")
            
            # 注意：这里只是发送请求，实际撤回需要在收到响应后进行
            # 由于是异步操作，这里返回提示信息
            return f"✅ 正在获取最近 {count} 条消息并撤回..."
            
        except Exception as e:
            print(f"[{self.name}] ❌ 获取历史消息失败: {e}")
            return f"❌ 获取历史消息失败: {str(e)}"
    
    async def recall_messages_by_user(self, context: ModuleContext, target_qq: int, count: int) -> str:
        """
        撤回指定用户的消息
        
        Args:
            context: 消息上下文
            target_qq: 目标用户QQ
            count: 要撤回的消息数量
            
        Returns:
            操作结果
        """
        try:
            # 获取历史消息，数量设为max(count * 2, 50)以确保能覆盖到该用户的消息，上限100
            fetch_count = min(max(count * 2, 50), 100)
            
            history_payload = {
                "action": "get_group_msg_history",
                "params": {
                    "group_id": context.group_id,
                    "count": fetch_count
                },
                # echo格式: get_user_history_{group_id}_{target_qq}_{limit_count}
                # 这里使用特定的前缀以便 main.py 识别并进行过滤处理
                "echo": f"get_user_history_{context.group_id}_{target_qq}_{count}"
            }

            await context.ws.send_text(json.dumps(history_payload))
            if DEBUG_MODE:
                print(f"[{self.name}] 已请求获取群 {context.group_id} 的历史消息，用于撤回用户 {target_qq} 的 {count} 条消息")

            return f"✅ 正在检索并撤回用户 {target_qq} 的最近 {count if count < 100 else '所有'} 条消息..."

        except Exception as e:
            print(f"[{self.name}] ❌ 请求失败: {e}")
            return f"❌ 请求失败: {str(e)}"

    async def recall_all_messages(self, context: ModuleContext) -> str:
        """
        撤回所有能撤回的消息
        
        Args:
            context: 消息上下文
            
        Returns:
            操作结果
        """
        try:
            # 获取大量历史消息
            history_payload = {
                "action": "get_group_msg_history",
                "params": {
                    "group_id": context.group_id,
                    "count": 100  # 一次获取100条
                },
                "echo": f"get_all_history_{context.group_id}"
            }
            
            await context.ws.send_text(json.dumps(history_payload))
            if DEBUG_MODE:
                print(f"[{self.name}] 已请求获取群 {context.group_id} 的所有历史消息")
            
            return f"✅ 正在获取所有消息并撤回..."
            
        except Exception as e:
            print(f"[{self.name}] ❌ 获取历史消息失败: {e}")
            return f"❌ 获取历史消息失败: {str(e)}"
