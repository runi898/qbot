"""
指令模块 - 处理所有机器人指令

包括撤回、数据库管理、定时任务等
"""

import re
from typing import Optional
from core.base_module import BaseModule, ModuleContext, ModuleResponse
from core.database import DatabaseManager


class CommandsModule(BaseModule):
    """指令模块"""
    
    def __init__(self):
        super().__init__()
        self.priority = 10  # 最高优先级
        self.db: Optional[DatabaseManager] = None
        self.watched_groups = []
    
    @property
    def name(self) -> str:
        return "指令模块"
    
    @property
    def version(self) -> str:
        return "1.0.0"
    
    @property
    def description(self) -> str:
        return "处理撤回、数据库管理、定时任务等指令"
    
    @property
    def author(self) -> str:
        return "QBot Team"
    
    async def on_load(self, config: dict) -> None:
        """加载时初始化"""
        await super().on_load(config)
        
        # 初始化数据库管理器
        self.db = DatabaseManager()
        
        # 加载监控群列表
        self.watched_groups = config.get('watched_groups', [])
        
        print(f"[{self.name}] 监控群聊: {self.watched_groups}")
    
    async def can_handle(self, message: str, context: ModuleContext) -> bool:
        """判断是否为指令"""
        # 指令列表
        commands = [
            "撤回", "查数据库", "数据库统计", "清理数据库",
            "清理全部已撤回", "导出数据库", "历史消息", "定时", "指令"
        ]
        
        # 检查是否包含指令关键词
        for cmd in commands:
            if cmd in message:
                return True
        
        # 检查是否为 @某人 + 指令
        if message.startswith("[CQ:at,qq=") and any(cmd in message for cmd in commands):
            return True
        
        # 检查是否为引用消息 + 撤回
        if message.startswith("[CQ:reply,id=") and "撤回" in message:
            return True
        
        return False
    
    async def handle(self, message: str, context: ModuleContext) -> Optional[ModuleResponse]:
        """处理指令"""
        # 1. 指令列表
        if message == "指令" or "指令" in message:
            return await self._handle_help()
        
        # 2. 撤回相关指令
        if "撤回" in message:
            return await self._handle_recall(message, context)
        
        # 3. 数据库相关指令
        if "数据库" in message:
            return await self._handle_database(message, context)
        
        # 4. 定时任务指令
        if "定时" in message:
            return await self._handle_timer(message, context)
        
        return None
    
    async def _handle_help(self) -> ModuleResponse:
        """显示帮助信息"""
        help_text = """
=== QBot 指令列表 ===

📌 撤回指令:
• 撤回 n - 撤回最近 n 条消息
• 撤回全部 - 撤回所有未撤回消息
• @某人 撤回 - 撤回某人的所有消息
• 引用消息 + 撤回 - 撤回被引用的消息
• 撤回id xxx - 撤回指定ID的消息

📊 数据库指令:
• 查数据库 - 查询所有消息记录
• 数据库统计 - 查看数据库使用情况
• 清理数据库 - 清理7天前的已撤回消息
• 清理3天 - 清理3天前的已撤回消息
• 清理全部已撤回 - 清理所有已撤回消息
• 导出数据库 - 导出为Excel文件

⏰ 定时任务:
• 定时 n - 每隔n分钟自动撤回
• 定时关 - 关闭定时撤回

📖 其他:
• 历史消息 - 获取最近历史消息
• 指令 - 显示此帮助信息
        """.strip()
        
        return ModuleResponse(
            content=help_text,
            auto_recall=True,
            recall_delay=10
        )
    
    async def _handle_recall(self, message: str, context: ModuleContext) -> Optional[ModuleResponse]:
        """处理撤回指令"""
        # 引用消息撤回
        reply_match = re.search(r'\[CQ:reply,id=(\d+)\]', context.raw_message)
        if reply_match and "撤回" in message:
            quoted_msg_id = int(reply_match.group(1))
            return ModuleResponse(
                content=f"好的，我将尝试撤回您引用的消息 (ID: {quoted_msg_id})。",
                auto_recall=True,
                quoted_msg_id=quoted_msg_id
            )
        
        # 撤回 n 条消息
        match = re.search(r'撤回\s*(\d+)', message)
        if match:
            count = int(match.group(1))
            if context.group_id:
                msg_ids = self.db.get_unrecalled_messages(context.group_id, count)
                # 这里需要调用撤回逻辑（在主程序中处理）
                return ModuleResponse(
                    content=f"准备撤回最近 {count} 条消息...",
                    auto_recall=True,
                    extra={'action': 'recall_messages', 'message_ids': msg_ids}
                )
        
        # 撤回全部
        if "撤回全部" in message:
            if context.group_id:
                msg_ids = self.db.get_unrecalled_messages(context.group_id)
                return ModuleResponse(
                    content=f"准备撤回所有未撤回消息（共 {len(msg_ids)} 条）...",
                    auto_recall=True,
                    extra={'action': 'recall_messages', 'message_ids': msg_ids}
                )
        
        # @某人 撤回
        at_match = re.search(r'\[CQ:at,qq=(\d+)', context.raw_message)
        if at_match and "撤回" in message:
            at_qq = int(at_match.group(1))
            if context.group_id:
                msg_ids = self.db.get_user_messages(context.group_id, at_qq)
                return ModuleResponse(
                    content=f"准备撤回用户 {at_qq} 的所有消息（共 {len(msg_ids)} 条）...",
                    auto_recall=True,
                    extra={'action': 'recall_messages', 'message_ids': msg_ids}
                )
        
        return None
    
    async def _handle_database(self, message: str, context: ModuleContext) -> Optional[ModuleResponse]:
        """处理数据库指令"""
        # 数据库统计
        if "数据库统计" in message:
            stats = self.db.get_stats()
            content = f"""
📊 数据库统计信息

总消息数: {stats['total_messages']}
已撤回消息: {stats['recalled_messages']}
未撤回消息: {stats['active_messages']}
最早消息时间: {stats['oldest_message'] or '无'}
数据库大小: {stats['db_size_mb']} MB
            """.strip()
            
            return ModuleResponse(content=content, auto_recall=True)
        
        # 清理数据库
        if "清理全部已撤回" in message:
            deleted = self.db.cleanup_all_recalled()
            return ModuleResponse(
                content=f"数据库清理完成：删除了 {deleted} 条已撤回消息",
                auto_recall=True
            )
        
        # 清理 n 天
        match = re.search(r'清理(\d+)天', message)
        if match:
            days = int(match.group(1))
            deleted = self.db.cleanup_old_messages(days)
            return ModuleResponse(
                content=f"数据库清理完成：删除了 {deleted} 条 {days} 天前的已撤回消息",
                auto_recall=True
            )
        
        # 导出数据库
        if "导出数据库" in message:
            # 这个功能需要在主程序中实现
            return ModuleResponse(
                content="数据库导出功能开发中...",
                auto_recall=True
            )
        
        return None
    
    async def _handle_timer(self, message: str, context: ModuleContext) -> Optional[ModuleResponse]:
        """处理定时任务指令"""
        if context.group_id is None:
            return ModuleResponse(
                content="私聊不支持定时撤回功能",
                auto_recall=True
            )
        
        # 定时关
        if message.endswith("关"):
            return ModuleResponse(
                content=f"群 {context.group_id} 定时撤回功能已关闭",
                auto_recall=True,
                extra={'action': 'timer_off', 'group_id': context.group_id}
            )
        
        # 定时 n
        match = re.search(r'定时\s*(\d+)', message)
        if match:
            interval = int(match.group(1))
            return ModuleResponse(
                content=f"群 {context.group_id} 定时撤回已启动：每 {interval} 分钟执行一次",
                auto_recall=True,
                extra={'action': 'timer_on', 'group_id': context.group_id, 'interval': interval}
            )
        
        return None
