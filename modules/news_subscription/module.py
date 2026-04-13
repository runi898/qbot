"""
线报订阅模块

功能：
- 管理用户订阅的关键词
- 提供命令接口（订阅、取消、列表、清空、暂停、恢复）
- 提供高效的内存匹配接口
- 智能边界匹配（防止数字误判）
- 推送去重（同一内容不会重复推送给同一用户）
"""

import re
import asyncio
import hashlib
import time
from typing import Dict, List, Set, Optional, Tuple

from core.base_module import BaseModule, ModuleContext, ModuleResponse
from modules.news_collector.database import news_db
from config import get_bot_qq_list, BOT_PRIORITY, DEBUG_MODE
from core import bot_manager

class SubscriptionManager:
    """订阅管理器（单例模式）"""
    _instance = None

    PUSH_DEDUP_WINDOW = 300  # 推送去重窗口（秒）
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.subscriptions = {}  # {'keyword': {'user_ids': {123, 456}, 'regex': re_obj}}
            cls._instance.user_paused = set() # {user_id}
            cls._instance.initialized = False
            # 推送去重缓存: {content_hash: {'user_ids': set(), 'timestamp': float}}
            cls._instance._push_dedup: Dict[str, Dict] = {}
            cls._instance._push_dedup_last_cleanup = 0.0
        return cls._instance
    
    def __init__(self):
        # 属性声明以辅助 IDE
        if not hasattr(self, 'initialized'):
            self.subscriptions = {}
            self.user_paused = set()
            self.initialized = False
            self._push_dedup: Dict[str, Dict] = {}
            self._push_dedup_last_cleanup = 0.0

    def initialize(self):
        if self.initialized:
            return
            
        print("[Subscription] 初始化内存缓存...")
        valid_subs = news_db.get_all_subscriptions()
        
        # 统计并打印加载的关键词，用于调试
        loaded_keywords = [sub['keyword'] for sub in valid_subs]
        print(f"[Subscription] 正在从数据库加载订阅: {loaded_keywords}")
        
        for sub in valid_subs:
            user_id = sub['user_id']
            keyword = sub['keyword']
            is_paused = sub['is_paused']
            
            self._add_to_cache(user_id, keyword)
            
            if is_paused:
                self.user_paused.add(user_id)
                
        self.initialized = True
        print(f"[Subscription] 加载完成，共有 {len(valid_subs)} 条订阅记录")

    def _add_to_cache(self, user_id: int, keyword: str):
        if keyword not in self.subscriptions:
            # 编译正则
            regex = self._compile_regex(keyword)
            self.subscriptions[keyword] = {
                'user_ids': set(),
                'regex': regex
            }
        self.subscriptions[keyword]['user_ids'].add(user_id)

    def _compile_regex(self, keyword: str):
        """编译匹配正则"""
        # 1. 显式正则模式 re:pattern
        if keyword.startswith("re:"):
            try:
                pattern = keyword[3:]
                return re.compile(pattern, re.IGNORECASE)
            except Exception as e:
                print(f"[Subscription] 正则编译失败 '{keyword}': {e}")
                # 降级为普通包含了
                return None
        
        # 2. 数字智能边界模式 (全数字或数字+单位)
        # 匹配: 0元, 1.5元, 100
        # 排除: 2023年
        if re.match(r'^\d+(\.\d+)?(元|金币|豆|积分)?$', keyword):
            # 前后不能有数字
            pattern = re.escape(keyword)
            return re.compile(f'(?<!\\d){pattern}(?!\\d)', re.IGNORECASE)
            
        # 3. 普通文本模式
        return None  # 使用 in 判断

    def add_subscription(self, user_id: int, keyword: str) -> bool:
        """添加订阅"""
        if news_db.add_subscription(user_id, keyword):
            self._add_to_cache(user_id, keyword)
            return True
        return False

    def remove_subscription(self, user_id: int, keyword: str) -> bool:
        """取消订阅"""
        if news_db.remove_subscription(user_id, keyword):
            if keyword in self.subscriptions:
                self.subscriptions[keyword]['user_ids'].discard(user_id)
                if not self.subscriptions[keyword]['user_ids']:
                    del self.subscriptions[keyword]
            return True
        return False

    def clear_subscriptions(self, user_id: int) -> int:
        """清空订阅"""
        count = news_db.clear_user_subscriptions(user_id)
        if count > 0:
            # 重建缓存比较麻烦，简单地遍历清理
            empty_keywords = []
            for kw, data in self.subscriptions.items():
                data['user_ids'].discard(user_id)
                if not data['user_ids']:
                    empty_keywords.append(kw)
            for kw in empty_keywords:
                del self.subscriptions[kw]
        return count

    def set_pause(self, user_id: int, pause: bool) -> bool:
        """设置暂停"""
        if news_db.set_subscription_pause(user_id, pause) > 0:
            if pause:
                self.user_paused.add(user_id)
            else:
                self.user_paused.discard(user_id)
            return True
        # 如果数据库没有记录（可能用户从未订阅），插入或忽略
        # 这里简化逻辑：只对订阅过的用户生效
        return False

    def _normalize_content_for_dedup(self, content: str) -> str:
        """
        标准化内容用于去重哈希计算。
        去掉 URL、CQ码、空白符和标点，只保留核心文案部分，
        保证同一条线报即使链接不同也能被识别为重复。
        """
        import re as _re
        text = content
        # 去掉 CQ 码
        text = _re.sub(r'\[CQ:[^\]]+\]', '', text)
        # 去掉所有 URL
        text = _re.sub(r'https?://\S+', '', text)
        # 去掉各种淘口令符号
        text = _re.sub(r'[￥$€₤【】]', '', text)
        # 去空白和标点
        text = _re.sub(r'\s+', '', text)
        text = text.strip().lower()
        return text

    def _compute_content_hash(self, content: str) -> str:
        """计算内容的去重哈希"""
        normalized = self._normalize_content_for_dedup(content)
        return hashlib.sha1(normalized.encode('utf-8')).hexdigest()

    def _prune_push_dedup_cache(self, now: float) -> None:
        """定期清理过期的推送去重缓存"""
        if now - self._push_dedup_last_cleanup < 30:
            return
        self._push_dedup_last_cleanup = now
        expired_keys = [
            k for k, v in self._push_dedup.items()
            if now - v['timestamp'] > self.PUSH_DEDUP_WINDOW
        ]
        for k in expired_keys:
            self._push_dedup.pop(k, None)

    def _is_push_duplicate(self, content_hash: str, user_id: int) -> bool:
        """
        检查某条内容是否已经推送过给指定用户。
        如果是新的，则标记为已推送并返回 False；
        如果是重复的，返回 True。
        """
        now = time.time()
        self._prune_push_dedup_cache(now)

        entry = self._push_dedup.get(content_hash)
        if entry and (now - entry['timestamp']) <= self.PUSH_DEDUP_WINDOW:
            if user_id in entry['user_ids']:
                return True  # 已推送过
            # 同一条内容，新用户
            entry['user_ids'].add(user_id)
            return False
        
        # 新内容
        self._push_dedup[content_hash] = {
            'user_ids': {user_id},
            'timestamp': now,
        }
        return False

    async def push_to_subscribers(self, content: str, bot_id: int, exclude_user: Optional[int] = None):
        """
        匹配内容并推送给订阅用户（带去重）
        """
        # 确保已初始化
        self.initialize()
        
        matched_users = self.get_matches(content)
        if not matched_users:
            return
            
        if exclude_user and exclude_user in matched_users:
            matched_users.discard(exclude_user)
            
        if not matched_users:
            return

        # 计算内容哈希，用于推送去重
        content_hash = self._compute_content_hash(content)

        # 过滤掉已经推送过相同内容的用户
        new_users = set()
        for uid in matched_users:
            if not self._is_push_duplicate(content_hash, uid):
                new_users.add(uid)

        if not new_users:
            if DEBUG_MODE:
                print(f"[Subscription] 所有匹配用户均已推送过相同内容，跳过 (hash={content_hash[:12]}...)")
            return

        skipped = len(matched_users) - len(new_users)
        if skipped > 0:
            print(f"[Subscription] 去重过滤: {skipped} 个用户已推送过，剩余 {len(new_users)} 个新推送")
        
        print(f"[Subscription] 匹配到 {len(new_users)} 个用户，由机器人 {bot_id} 准备推送")
        
        # 获取 WebSocket 连接
        ws = bot_manager.get_bot_connection(bot_id)
        if not ws:
            print(f"[Subscription] 无法获取机器人 {bot_id} 的 WebSocket 连接，推送失败")
            return

        import json
        from datetime import datetime
        
        # 批量发送私聊通知
        for target_uid in new_users:
            try:
                # 构造简单的通知消息
                notify_msg = f"【线报推送】\n{content}"
                payload = {
                    "action": "send_private_msg",
                    "params": {
                        "user_id": target_uid,
                        "message": notify_msg
                    },
                    "echo": f"push_notify_{target_uid}_{int(datetime.now().timestamp())}"
                }
                await ws.send_text(json.dumps(payload))
            except Exception as e:
                print(f"[Subscription] 推送给 {target_uid} 失败: {e}")

    def get_matches(self, content: str) -> Set[int]:
        """
        获取所有匹配该内容的用户ID集合 (严格匹配版本)
        """
        matched_users = set()
        
        for keyword, data in self.subscriptions.items():
            regex = data['regex']
            is_match = False
            
            if regex:
                # 正则/边界模式
                if regex.search(content):
                    is_match = True
            else:
                # 普通包含模式
                if keyword in content:
                    is_match = True
            
            if is_match:
                # 打印匹配成功的关键词
                print(f"[Subscription] 关键词 '{keyword}' 匹配成功!")
                # 过滤已暂停的用户
                users = data['user_ids']
                active_users = {uid for uid in users if uid not in self.user_paused}
                matched_users.update(active_users)
                
        return matched_users



class NewsSubscriptionModule(BaseModule):
    """线报订阅模块"""
    
    @property
    def name(self) -> str:
        return "线报订阅"
    
    @property
    def version(self) -> str:
        return "1.0.0"
    
    @property
    def description(self) -> str:
        return "关键词订阅与通知（私聊）"

    async def on_load(self, config: dict) -> None:
        await super().on_load(config)
        self.manager = SubscriptionManager()
        self.manager.initialize()
        self.max_subs = config.get("settings", {}).get("max_subscriptions", 20)
        
        # 初始化优先级配置
        self.bot_priority = BOT_PRIORITY
        # 调试模式
        self.debug = config.get("debug", False)
        
        print(f"[{self.name}] 模块已加载，每个用户最多订阅 {self.max_subs} 个词")
        print(f"[{self.name}] 机器人优先级顺序: {self.bot_priority}")

    def should_respond_by_priority(self, context: ModuleContext) -> bool:
        """
        判断当前机器人是否应该响应(基于优先级和在线状态)
        只有优先级最高的在线机器人才响应
        """
        current_bot = context.self_id
        debug = self.debug or DEBUG_MODE
        
        # 如果当前机器人不在优先级列表中,默认响应
        if current_bot not in self.bot_priority:
            return True
        
        # 获取在线机器人列表
        online_bots = bot_manager.get_online_bots()
        
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
            return False
            
        should_respond = (current_bot == target_bot)
        
        if debug and context.group_id:
            print(f"[{self.name}] 本群({context.group_id}) 应响应机器人: {target_bot}")
            print(f"[{self.name}] 当前机器人({current_bot}) {'应该' if should_respond else '不应该'}响应")
        
        return should_respond

    async def can_handle(self, message: str, context: ModuleContext) -> bool:
        msg = message.strip()
        
        # 全局优先级检查 (针对群聊)
        if context.group_id:
            if not self.should_respond_by_priority(context):
                return False

        # 只处理订阅指令，不再匹配普通群消息
        # 订阅推送由收集模块（news_jd/news_taobao）作为唯一入口触发
        if msg.startswith(("订阅", "取消订阅", "我的订阅", "订阅清空", "订阅暂停", "订阅恢复")):
            if msg.startswith("订阅"):
                print(f"[{self.name}] 收到订阅指令，准备处理: {msg}")
            return True
            
        return False

    async def handle(self, message: str, context: ModuleContext) -> Optional[ModuleResponse]:
        msg = message.strip()
        user_id = context.user_id
        
        # === 指令处理 ===
        # 1. 我的订阅
        if msg == "我的订阅":
            keywords = news_db.get_user_subscriptions(user_id)
            if not keywords:
                return ModuleResponse("当前没有订阅任何关键词。", auto_recall=True, recall_delay=10)
            status = " (已暂停)" if user_id in self.manager.user_paused else ""
            return ModuleResponse(f"当前订阅 ({len(keywords)}/{self.max_subs}){status}：\n" + "、".join(keywords), auto_recall=True, recall_delay=30)

        # 2. 订阅清空
        if msg == "订阅清空":
            count = self.manager.clear_subscriptions(user_id)
            return ModuleResponse(f"已清空 {count} 条订阅。", auto_recall=True, recall_delay=10)

        # 3. 订阅暂停
        if msg == "订阅暂停":
            self.manager.set_pause(user_id, True)
            return ModuleResponse("已暂停订阅，发送【订阅恢复】可重新接收。", auto_recall=True, recall_delay=10)

        # 4. 订阅恢复
        if msg == "订阅恢复":
            self.manager.set_pause(user_id, False)
            return ModuleResponse("已恢复订阅。", auto_recall=True, recall_delay=10)

        # 5. 取消订阅
        if msg.startswith("取消订阅"):
            keyword = msg.replace("取消订阅", "").strip()
            if not keyword:
                return ModuleResponse("格式错误，请使用：取消订阅 关键词", auto_recall=True, recall_delay=10)
            
            if self.manager.remove_subscription(user_id, keyword):
                return ModuleResponse(f"已取消订阅：{keyword}", auto_recall=True, recall_delay=10)
            else:
                return ModuleResponse(f"未找到订阅：{keyword}", auto_recall=True, recall_delay=10)

        # 6. 订阅 <关键词>
        if msg.startswith("订阅"):
            keyword = msg.replace("订阅", "").strip()
            if not keyword:
                help_msg = (
                    "【订阅助手】\n"
                    "发送以下指令即可操作：\n"
                    "1. 订阅 <关键词>：关注商品\n"
                    "   例：订阅 抄纸\n"
                    "   例：订阅 0元 (智能避开 60元)\n"
                    "2. 取消订阅 <关键词>：取消关注\n"
                    "3. 我的订阅：查看列表\n"
                    "4. 订阅清空：清空所有\n"
                    "5. 订阅暂停/恢复：暂停或恢复通知"
                )
                return ModuleResponse(help_msg, auto_recall=True, recall_delay=30)
            
            current_subs = news_db.get_user_subscriptions(user_id)
            if len(current_subs) >= self.max_subs:
                return ModuleResponse(f"订阅数已达上限 ({self.max_subs})，请先取消部分订阅。", auto_recall=True, recall_delay=10)
            
            if self.manager.add_subscription(user_id, keyword):
                extra_tip = ""
                if re.match(r'^\d+(\.\d+)?(元|金币|豆|积分)?$', keyword):
                    extra_tip = "\n(已启用数字智能匹配: 0元不会匹配60元)"
                elif keyword.startswith("re:"):
                    extra_tip = "\n(已启用正则匹配模式)"
                return ModuleResponse(f"已订阅：{keyword}{extra_tip}", auto_recall=True, recall_delay=10)
            else:
                return ModuleResponse(f"已存在订阅：{keyword}", auto_recall=True, recall_delay=10)
        
        return None

