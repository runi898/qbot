"""
返利模块 - 淘宝和京东链接转换

自动识别淘宝/京东链接并转换为推广链接
"""

import re
from typing import Optional, Set
from core.base_module import BaseModule, ModuleContext, ModuleResponse
from config import get_bot_qq_list, BOT_PRIORITY, DEBUG_MODE
import main  # 导入main模块以访问get_online_bots
from .taobao import TaobaoConverter
from .jingdong import JingdongConverter


class RebateModule(BaseModule):
    """返利模块"""
    
    def __init__(self):
        super().__init__()
        self.priority = 40  # 较高优先级，在指令模块之后
        
        self.taobao_converter: Optional[TaobaoConverter] = None
        self.jingdong_converter: Optional[JingdongConverter] = None
        
        # 淘宝正则
        # 淘宝正则（排除京东口令：后面跟 MF/CA 的是京东口令）
        self.taobao_regex = re.compile(
            r'(https?://[^\s<>]*(?:taobao\.|tb\.)[^\s<>]+)|'
            r'(?:￥|\$)([0-9A-Za-z()]*[A-Za-z][0-9A-Za-z()]{10})(?:￥|\$)?(?!\s*(?:MF|CA)[0-9]+)(?![0-9A-Za-z])|'
            r'([0-9]\$[0-9a-zA-Z]+\$:// [A-Z0-9]+)|'
            r'tk=([0-9A-Za-z]{11,12})|'
            r'\(([0-9A-Za-z]{11})\)|'
            r'₤([0-9A-Za-z]{13})₤|'
            r'[0-9]{2}₤([0-9A-Za-z]{11})£'
        )
        
        # 京东正则（添加京东口令支持：￥xxx￥ MF/CA 或 ！xxx！ MF/CA）
        self.jingdong_regex = re.compile(
            r'https?:\/\/[^\s<>]*(?:3\.cn|jd\.|jingxi)[^\s<>]+|'
            r'(?:￥|！|\$)[0-9A-Za-z()]+(?:￥|！|\$)\s*(?:MF|CA)[0-9]+|'
            r'[^一-龥0-9a-zA-Z=;&?-_.<>:\'\",{}][0-9a-zA-Z()]{16}[^一-龥0-9a-zA-Z=;&?-_.<>:\'\",{}\s]'
        )
    
    @property
    def name(self) -> str:
        return "返利模块"
    
    @property
    def version(self) -> str:
        return "1.0.0"
    
    @property
    def description(self) -> str:
        return "自动识别并转换淘宝/京东链接为推广链接"
    
    @property
    def author(self) -> str:
        return "QBot Team"
    
    async def on_load(self, config: dict) -> None:
        """加载时初始化转换器"""
        await super().on_load(config)
        
        # 获取机器人QQ列表(用于过滤机器人消息)
        self.bot_qq_list = get_bot_qq_list()
        
        # 获取机器人优先级配置
        self.bot_priority = BOT_PRIORITY
        
        # 调试:打印接收到的配置
        print(f"[{self.name}] 接收到的配置keys: {list(config.keys())}")
        print(f"[{self.name}] settings keys: {list(config.get('settings', {}).keys())}")
        print(f"[{self.name}] 机器人QQ列表: {self.bot_qq_list}")
        print(f"[{self.name}] 机器人优先级顺序: {self.bot_priority}")
        
        # 配置
        self.config = config
        settings = config.get('settings', {})
        
        # 监听群列表
        self.watched_groups = settings.get('watched_groups', [])
        
        # 管理员配置
        self.admin_qq_list = settings.get('admin_qq_list', [])
        self.admin_group_list = settings.get('admin_group_list', [])
        
        # 佣金显示配置
        commission_config = settings.get('commission_display', {})
        self.show_in_admin_group = commission_config.get('show_in_admin_group', True)
        self.show_in_private_admin = commission_config.get('show_in_private_admin', True)
        self.show_in_private_user = commission_config.get('show_in_private_user', False)
        self.show_in_other_group = commission_config.get('show_in_other_group', False)

        # 初始化淘宝转换器（从config顶层读取）
        taobao_config = config.get('淘宝API', {})
        if taobao_config.get('app_key'):
            self.taobao_converter = TaobaoConverter(taobao_config)
            print(f"[{self.name}] 淘宝转换器已启用")
        else:
            print(f"[{self.name}] 警告: 淘宝API未配置，淘宝转换功能将不可用")
        
        # 初始化京东转换器（从config顶层读取）
        jingdong_config = config.get('京东API', {})
        jingtuitui_config = config.get('京推推API', {})
        
        # 合并配置，京推推的参数使用 jtt_ 前缀
        combined_jd_config = {
            **jingdong_config,
            'jtt_appid': jingtuitui_config.get('appid', ''),
            'jtt_appkey': jingtuitui_config.get('appkey', ''),
        }
        
        if jingdong_config.get('appkey'):
            self.jingdong_converter = JingdongConverter(combined_jd_config)
            print(f"[{self.name}] 京东转换器已启用")
        else:
            print(f"[{self.name}] 警告: 京东API未配置，京东转换功能将不可用")
        
        print(f"[{self.name}] 管理员配置: {len(self.admin_qq_list)} 个管理员, {len(self.admin_group_list)} 个管理员群")
    
    def should_show_commission(self, user_qq: int, group_id: Optional[int]) -> bool:
        """
        判断是否显示佣金
        
        Args:
            user_qq: 用户QQ号
            group_id: 群号（私聊时为None）
        
        Returns:
            是否显示佣金
        """
        debug = self.config.get("debug", False) or DEBUG_MODE

        # 1. 私聊情况
        if group_id is None:
            # 管理员私聊显示，普通用户私聊不显示
            result = user_qq in self.admin_qq_list
            print(f"[{self.name}] 私聊佣金判断: user_qq={user_qq}, admin_qq_list={self.admin_qq_list}, result={result}")
            return result
        
        # 2. 群聊情况
        # 只有在管理员群才显示佣金(无论是否管理员)
        result = group_id in self.admin_group_list
        if debug:
            print(f"[{self.name}] 群聊佣金判断: group_id={group_id}, admin_group_list={self.admin_group_list}, result={result}")
        return result
    
    def should_respond_by_priority(self, context: ModuleContext) -> bool:
        """
        判断当前机器人是否应该响应(基于优先级和在线状态)
        只有优先级最高的在线机器人才响应
        
        Args:
            context: 消息上下文
            
        Returns:
            是否应该响应
        """
        current_bot = context.self_id
        debug = self.config.get("debug", False) or DEBUG_MODE  # 使用全局DEBUG_MODE
        
        # 总是输出基本信息用于调试
        if debug:
            print(f"[{self.name}] === 优先级检查开始 ===")
            print(f"[{self.name}] 当前机器人: {current_bot}")
            print(f"[{self.name}] 优先级列表: {self.bot_priority}")
        
        # 如果当前机器人不在优先级列表中,默认响应
        if current_bot not in self.bot_priority:
            print(f"[{self.name}] 当前机器人({current_bot})不在优先级列表中,默认响应")
            return True
        
        # 获取在线机器人列表
        from core import bot_manager
        online_bots = bot_manager.get_online_bots()
        if debug:
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
            if debug:
                print(f"[{self.name}] 没有找到合适的机器人处理（都在线但都不在群？）")
            return False
            
        should_respond = (current_bot == target_bot)
        
        if debug:
            print(f"[{self.name}] 本群({context.group_id}) 应响应机器人: {target_bot}")
            print(f"[{self.name}] 当前机器人({current_bot}) {'应该' if should_respond else '不应该'}响应")
            print(f"[{self.name}] === 优先级检查结束 ===")
        
        return should_respond
    
    
    async def can_handle(self, message: str, context: ModuleContext) -> bool:
        """判断消息中是否包含淘宝或京东链接"""
        # 从配置中读取 debug 标记,默认为 False
        debug = self.config.get("debug", False)
        
        if debug:
            print(f"[{self.name}] can_handle 检查: group_id={context.group_id}, user_id={context.user_id}, self_id={context.self_id}")
            print(f"[{self.name}] 监听群列表: {self.watched_groups}")
        
        # 跳过所有机器人的消息(防止机器人间互相回复造成循环)
        if context.user_id in self.bot_qq_list:
            if debug:
                print(f"[{self.name}] 跳过机器人消息: {context.user_id}")
            return False
        
        # 处理私聊消息(group_id 为 None) - 私聊不受优先级限制
        if context.group_id is None:
            if debug:
                print(f"[{self.name}] 私聊消息,检查是否包含链接")
            # 私聊消息也处理,检查是否包含淘宝或京东链接
            has_link = bool(self.taobao_regex.search(message) or self.jingdong_regex.search(message))
            if has_link and debug:
                print(f"[{self.name}] 私聊消息包含链接,将处理")
            return has_link
        
        # 只处理监听群的消息
        if context.group_id not in self.watched_groups:
            if debug:
                print(f"[{self.name}] 群 {context.group_id} 不在监听列表中")
            return False
        
        # 优先级检查:只有优先级最高的在线机器人响应
        if not self.should_respond_by_priority(context):
            return False
        
        # 检查是否包含淘宝或京东链接
        has_taobao = bool(self.taobao_regex.search(message))
        has_jingdong = bool(self.jingdong_regex.search(message))
        
        return has_taobao or has_jingdong
    
    async def handle(self, message: str, context: ModuleContext) -> Optional[ModuleResponse]:
        """处理链接转换"""
        debug = self.config.get("debug", False) or DEBUG_MODE
        results = []
        processed_titles: Set[str] = set()
        
        # 判断是否显示佣金
        show_commission = self.should_show_commission(context.user_id, context.group_id)
        
        # 处理淘宝链接
        if self.taobao_converter:
            taobao_matches = self.taobao_regex.findall(message)
            for match in taobao_matches:
                tkl = next((m for m in match if m), None)
                if tkl:
                    result = await self.taobao_converter.convert(tkl, processed_titles, show_commission)
                    if result:
                        results.append(result)
        
        # 处理京东链接
        if self.jingdong_converter:
            jingdong_matches = self.jingdong_regex.findall(message)
        if debug:
            print(f"[{self.name}] 京东正则匹配结果: {jingdong_matches}")
        for match in jingdong_matches:
            if debug:
                print(f"[{self.name}] 处理京东链接: {match}")
            if match:
                result = await self.jingdong_converter.convert(match, processed_titles, show_commission)
                if result:
                    results.append(result)
        
        if results:
            content = "\n\n".join(results)
            return ModuleResponse(
                content=content,
                auto_recall=False  # 转换结果不自动撤回
            )
        
        return None
