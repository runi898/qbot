"""
线报转发模块

功能：
- 定时获取待转发线报
- 轮流使用多个账号转发
- 异步发送，不等待响应
- 记录转发日志
"""

import asyncio
from typing import List, Dict, Optional
from datetime import datetime

from core.base_module import BaseModule, ModuleContext, ModuleResponse


class NewsForwarder:
    """线报转发器（内部类）"""
    
    def __init__(self, config: Dict, bot_manager):
        """
        初始化转发器
        
        Args:
            config: 配置字典
            bot_manager: Bot管理器实例
        """
        self.config = config
        self.bot_manager = bot_manager
        self.forwarders = config.get('forwarders', [])
        self.forward_mode = config.get('forward_mode', 'round_robin')
        self.forward_interval = config.get('forward_interval', 0)
        self.batch_size = config.get('batch_size', 1)
        self.async_forward = config.get('async_forward', True)
        
        # 解析转发账号列表（支持单个QQ或多个QQ）
        self.qq_pools = []  # 每个forwarder的QQ池
        for forwarder in self.forwarders:
            qq = forwarder.get('qq')
            if isinstance(qq, list):
                # 多个QQ
                self.qq_pools.append({
                    'qqs': qq,
                    'targets': forwarder.get('targets', []),
                    'current_index': 0,
                })
            else:
                # 单个QQ
                self.qq_pools.append({
                    'qqs': [qq],
                    'targets': forwarder.get('targets', []),
                    'current_index': 0,
                })
        
        self.current_pool_index = 0  # 当前使用的QQ池索引
        
        print(f"[✓] 线报转发器初始化完成（模式：{self.forward_mode}）")
        print(f"[✓] 配置了 {len(self.qq_pools)} 个转发池")
    
    def is_bot_online(self, qq: int) -> bool:
        """
        检查Bot是否在线
        
        Args:
            qq: QQ号
        
        Returns:
            是否在线
        """
        try:
            bot = self.bot_manager.get_bot(qq)
            return bot is not None
        except:
            return False
    
    def get_next_online_qq(self, qq_pool: Dict) -> Optional[int]:
        """
        从QQ池中获取下一个在线的QQ
        
        Args:
            qq_pool: QQ池配置
        
        Returns:
            在线的QQ号，如果都离线则返回None
        """
        qqs = qq_pool['qqs']
        start_index = qq_pool['current_index']
        
        # 尝试所有QQ，找到第一个在线的
        for i in range(len(qqs)):
            index = (start_index + i) % len(qqs)
            qq = qqs[index]
            
            if self.is_bot_online(qq):
                # 更新索引，下次从下一个开始
                qq_pool['current_index'] = (index + 1) % len(qqs)
                return qq
        
        # 所有QQ都离线
        return None
    
    def get_next_forwarder(self) -> Optional[Dict]:
        """
        获取下一个转发账号（支持在线检测）
        
        Returns:
            转发账号配置 {'qq': QQ号, 'targets': [群列表]}
            如果所有QQ都离线则返回None
        """
        if not self.qq_pools:
            return None
        
        # 尝试所有QQ池，找到第一个有在线QQ的池
        for i in range(len(self.qq_pools)):
            pool_index = (self.current_pool_index + i) % len(self.qq_pools)
            pool = self.qq_pools[pool_index]
            
            # 从这个池中获取在线的QQ
            online_qq = self.get_next_online_qq(pool)
            
            if online_qq:
                # 更新池索引
                self.current_pool_index = (pool_index + 1) % len(self.qq_pools)
                
                return {
                    'qq': online_qq,
                    'targets': pool['targets'],
                }
        
        # 所有QQ都离线
        print("[警告] 所有转发QQ都离线，跳过本次转发")
        return None
    
    async def send_to_group(self, qq: int, group_id: int, message: str) -> bool:
        """
        发送消息到群
        
        Args:
            qq: 发送账号
            group_id: 目标群号
            message: 消息内容
        
        Returns:
            是否成功
        """
        try:
            # 获取Bot实例
            bot = self.bot_manager.get_bot(qq)
            if not bot:
                print(f"[错误] 找不到Bot实例: {qq}")
                return False
            
            # 异步发送消息
            if self.async_forward:
                # 不等待响应，立即返回
                asyncio.create_task(bot.send_group_msg(group_id=group_id, message=message))
                return True
            else:
                # 等待响应
                await bot.send_group_msg(group_id=group_id, message=message)
                return True
        except Exception as e:
            print(f"[错误] 发送消息失败: {e}")
            return False
    
    async def forward_news(self, news: Dict) -> bool:
        """
        转发单条线报
        
        Args:
            news: 线报数据
        
        Returns:
            是否成功
        """
        # 获取转发账号
        forwarder = self.get_next_forwarder()
        if not forwarder:
            print("[错误] 没有可用的转发账号")
            return False
        
        qq = forwarder.get('qq')
        targets = forwarder.get('targets', [])
        
        if not targets:
            print(f"[错误] 账号 {qq} 没有配置目标群")
            return False
        
        # 转发到所有目标群
        success_count = 0
        for target_group in targets:
            success = await self.send_to_group(qq, target_group, news.get('converted_message'))
            
            if success:
                success_count += 1
                print(f"[转发] QQ{qq} -> 群{target_group}: {news.get('title', '未知')[:20]}...")
        
        return success_count > 0


class NewsForwarderModule(BaseModule):
    """线报转发模块（继承BaseModule）"""
    
    @property
    def name(self) -> str:
        return "线报转发"
    
    @property
    def version(self) -> str:
        return "1.0.0"
    
    @property
    def description(self) -> str:
        return "自动转发收集到的线报"
    
    @property
    def author(self) -> str:
        return "QBot Team"
    
    async def on_load(self, config: dict) -> None:
        """加载时初始化"""
        await super().on_load(config)
        
        self.forwarder_config = config
        self.forward_interval = config.get('forward_interval', 5)  # 转发间隔（秒）
        self.batch_size = config.get('batch_size', 1)  # 每次转发数量
        
        print(f"[{self.name}] 模块已加载 (v{self.version})")
        print(f"[{self.name}] 转发间隔: {self.forward_interval}秒")
        
        # 启动转发任务
        import asyncio
        asyncio.create_task(self.start_forward_task())
    
    async def can_handle(self, message: str, context: ModuleContext) -> bool:
        """转发模块不处理消息"""
        return False
    
    async def handle(self, message: str, context: ModuleContext) -> Optional[ModuleResponse]:
        """转发模块不处理消息"""
        return None
    
    async def start_forward_task(self):
        """启动转发任务（持续运行）"""
        print(f"[{self.name}] 转发任务已启动")
        
        # 等待一下，确保bot_manager已初始化
        await asyncio.sleep(5)
        
        while True:
            try:
                from modules.news_database import news_db
                
                # 获取待转发线报
                pending_news = news_db.get_pending_news(limit=self.batch_size)
                
                if pending_news:
                    for news in pending_news:
                        # 转发线报
                        success = await self.forward_news(news)
                        
                        if success:
                            # 标记为已转发
                            news_db.mark_as_forwarded(news.get('id'))
                        
                        # 转发间隔
                        if self.forward_interval > 0:
                            await asyncio.sleep(self.forward_interval)
                
                # 等待下一轮检查
                await asyncio.sleep(1)  # 每秒检查一次
            except Exception as e:
                print(f"[{self.name}] 转发任务异常: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)  # 出错后等待5秒
    
    async def forward_news(self, news: Dict) -> bool:
        """
        转发单条线报
        
        Args:
            news: 线报数据
        
        Returns:
            是否成功
        """
        try:
            # 获取转发配置
            forwarders = self.forwarder_config.get('forwarders', [])
            
            if not forwarders:
                print(f"[{self.name}] 错误: 没有配置转发账号")
                return False
            
            # 使用第一个转发配置（TODO: 实现轮询）
            forwarder = forwarders[0]
            qq_list = forwarder.get('qq', [])
            if not isinstance(qq_list, list):
                qq_list = [qq_list]
            
            targets = forwarder.get('targets', [])
            
            if not targets:
                print(f"[{self.name}] 错误: 没有配置目标群")
                return False
            
            # 获取bot_manager
            from main import bot_manager
            
            # 找到第一个在线的QQ
            online_qq = None
            for qq in qq_list:
                bot = bot_manager.get_bot(qq)
                if bot:
                    online_qq = qq
                    break
            
            if not online_qq:
                print(f"[{self.name}] 错误: 没有在线的转发账号")
                return False
            
            # 转发到所有目标群
            bot = bot_manager.get_bot(online_qq)
            success_count = 0
            
            for target_group in targets:
                try:
                    await bot.send_group_msg(
                        group_id=target_group,
                        message=news.get('converted_message', '')
                    )
                    success_count += 1
                    print(f"[{self.name}] 已转发到群{target_group}: {news.get('title', '')[:20]}...")
                except Exception as e:
                    print(f"[{self.name}] 转发到群{target_group}失败: {e}")
            
            return success_count > 0
            
        except Exception as e:
            print(f"[{self.name}] 转发线报失败: {e}")
            import traceback
            traceback.print_exc()
            return False
