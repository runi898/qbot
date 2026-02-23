"""
群管理模块 - QQ群管理功能

支持的功能：
- 群消息撤回（多种方式）
"""

import re
import json
import sys
from datetime import datetime
import os
from typing import Optional
from core.base_module import BaseModule, ModuleContext, ModuleResponse
import main
from config import get_bot_qq_list, BOT_PRIORITY, DEBUG_MODE, JD_SIGN_URL

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
        # 匹配@撤回: [CQ:at,qq=123456]...撤回 或 撤回 [CQ:at,qq=123456]
        self.at_recall_pattern = re.compile(r'(?:\[CQ:at,qq=(\d+).*?\]\s*撤回|撤回\s*\[CQ:at,qq=(\d+).*?\])(?:\s+(\d+))?', re.IGNORECASE)
        # 匹配 dwz 指令: dwz 京东链接
        self.dwz_pattern = re.compile(r'^dwz\s+(https?://[^\s]+)', re.IGNORECASE)
        
        # 初始化京东短链转换器
        self.jd_converter = JDShortUrlConverter(sign_url=JD_SIGN_URL)
        
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
        
        # time 指令 - 所有用户可用，群聊/私聊均可
        if message.strip().lower() == 'time':
            # 仍需优先级检查（避免多bot重复回复）
            if context.group_id and not self.should_respond_by_priority(context):
                return False
            return True
        
        # 2. 只处理群消息 (如果是 dwz 指令，允许私聊)
        is_dwz = bool(self.dwz_pattern.search(message))
        if context.group_id is None and not is_dwz:
            return False
            
        # 3. 群组过滤 (如果是 dwz 指令且私聊，跳过此步)
        if context.group_id and context.group_id not in self.watched_groups:
             return False
        
        # 4. 权限检查：只有管理员可以使用
        if context.user_id not in self.admin_qq_list:
            if DEBUG_MODE:
                print(f"[{self.name}] 用户 {context.user_id} 不是管理员，无权使用群管理功能")
            return False
        
        # 5. 机器人优先级检查：只有优先级最高的在线机器人才尝试
        # 私聊时 context.group_id 为 None，应该由优先级判断函数自行处理
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
    
        

    async def handle(self, message: str, context: ModuleContext) -> Optional[ModuleResponse]:
        """处理消息"""
        msg = message.strip()

        # time 指令 - 返回当前服务器时间
        if msg.lower() == 'time':
            now = datetime.now()
            time_str = now.strftime('%Y-%m-%d %H:%M:%S')
            weekdays = ['一', '二', '三', '四', '五', '六', '日']
            weekday = weekdays[now.weekday()]
            return ModuleResponse(
                content=f"🕐 当前时间\n{time_str}\n星期{weekday}",
                auto_recall=False
            )

        # 针对数据库和定时相关指令，暂时交由主程序处理
        # 避免通过 import main 导致副作用
        if msg in ["数据库统计", "清理数据库", "清理全部已撤回", "导出数据库"] or \
           (msg.startswith("清理") and "天" in msg) or \
           msg.startswith("定时"):
            return None

        try:
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
                # 兼容两种格式：
                # 1. [CQ:at,qq=123] 撤回 -> group(1)=123, group(2)=None, group(3)=count
                # 2. 撤回 [CQ:at,qq=123] -> group(1)=None, group(2)=123, group(3)=count
                target_qq = int(at_match.group(1)) if at_match.group(1) else int(at_match.group(2))
                count_str = at_match.group(3)
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
                # message_id 通常是10位大数字（如 2020896908）
                # N 条数量一般不超过 1000，用 100000 作为分界
                if value <= 100000:
                    # 撤回最近N条机器人消息
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
        撤回最近的N条机器人消息（不包括其他用户的消息）
        
        Args:
            context: 消息上下文
            count: 要撤回的机器人消息数量
            
        Returns:
            操作结果
        """
        try:
            history_payload = {
                "action": "get_group_msg_history",
                "params": {
                    "group_id": context.group_id,
                    "count": count + 20  # 多取20条，确保能凑够N条机器人消息
                },
                "echo": f"get_recent_history_{context.group_id}_{count}"
            }
            
            await context.ws.send_text(json.dumps(history_payload))
            if DEBUG_MODE:
                print(f"[{self.name}] 已请求获取群 {context.group_id} 的历史消息，撤回最近 {count} 条机器人消息")
            
            return f"✅ 正在撤回机器人最近 {count} 条消息（含本指令消息）..."
            
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
            # 动态导入配置以获取最新值
            # from config import RECALL_COUNT （已弃用，直接撤回大量消息）
            pass 
            
            # 移除 1.5秒 延迟，改回立即执行，避免阻塞消息循环
            
            # 获取大量历史消息
            history_payload = {
                "action": "get_group_msg_history",
                "params": {
                    "group_id": context.group_id,
                    "count": 200  # 直接获取200条，尽力撤回全部
                },
                "echo": f"get_all_history_{context.group_id}"
            }
            
            await context.ws.send_text(json.dumps(history_payload))
            if DEBUG_MODE:
                print(f"[{self.name}] 已请求获取群 {context.group_id} 的 200 条历史消息")
            
            return f"✅ 正在获取并撤回最近的 200 条消息..."
            
        except Exception as e:
            print(f"[{self.name}] ❌ 撤回请求失败: {e}")
            return f"❌ 撤回请求失败: {str(e)}"
