"""
BaseModule - 模块基类

所有功能模块必须继承此类
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

# 导入颜色工具
try:
    from utils.colors import green, red, yellow, SUCCESS, ERROR, WARNING
except ImportError:
    # 如果导入失败，使用无颜色版本
    def green(text): return text
    def red(text): return text
    def yellow(text): return text
    SUCCESS = "✅"
    ERROR = "❌"
    WARNING = "⚠️"


@dataclass
class ModuleContext:
    """模块执行上下文"""
    group_id: Optional[int]
    user_id: int
    message_id: Optional[int]
    self_id: int
    ws: Any  # WebSocket 对象，测试时可以为 None
    raw_message: str
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ModuleResponse:
    """模块响应数据"""
    content: str
    auto_recall: bool = False
    recall_delay: int = 30
    quoted_msg_id: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class BaseModule(ABC):
    """
    模块基类
    
    所有功能模块必须继承此类并实现相应方法
    """
    
    def __init__(self):
        self.enabled = True
        self.priority = 50  # 优先级，数字越小优先级越高
        self.config = {}
        
    # ========== 必须实现的属性 ==========
    
    @property
    @abstractmethod
    def name(self) -> str:
        """模块名称"""
        pass
    
    @property
    @abstractmethod
    def version(self) -> str:
        """模块版本"""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """模块描述"""
        pass
    
    @property
    def author(self) -> str:
        """模块作者"""
        return "Unknown"
    
    @property
    def dependencies(self) -> List[str]:
        """
        依赖的其他模块名称列表
        
        返回:
            List[str]: 依赖模块名称列表
        """
        return []
    
    # ========== 必须实现的方法 ==========
    
    @abstractmethod
    async def can_handle(self, message: str, context: ModuleContext) -> bool:
        """
        判断是否能处理该消息
        
        Args:
            message: 清理后的消息内容（已去除 CQ 码）
            context: 消息上下文
            
        Returns:
            bool: 是否能处理
        """
        pass
    
    @abstractmethod
    async def handle(self, message: str, context: ModuleContext) -> Optional[ModuleResponse]:
        """
        处理消息
        
        Args:
            message: 清理后的消息内容
            context: 消息上下文
            
        Returns:
            ModuleResponse: 模块响应，如果不需要回复则返回 None
        """
        pass
    
    # ========== 生命周期钩子（可选实现） ==========
    
    async def on_load(self, config: Dict[str, Any]) -> None:
        """
        模块加载时调用
        
        Args:
            config: 模块配置字典
        """
        self.config = config
        print(f"[{self.name}] {SUCCESS} 模块已加载 (v{green(self.version)})")
    
    async def on_unload(self) -> None:
        """模块卸载时调用"""
        print(f"[{self.name}] {WARNING} 模块已卸载")
    
    async def on_enable(self) -> None:
        """模块启用时调用"""
        self.enabled = True
        print(f"[{self.name}] {SUCCESS} 模块已启用")
    
    async def on_disable(self) -> None:
        """模块禁用时调用"""
        self.enabled = False
        print(f"[{self.name}] 模块已禁用")
    
    # ========== 辅助方法 ==========
    
    def get_help(self) -> str:
        """
        获取模块帮助信息
        
        Returns:
            str: 帮助文本
        """
        return f"""
【{self.name}】v{self.version}
{self.description}
作者: {self.author}
状态: {'✅ 已启用' if self.enabled else '❌ 已禁用'}
        """.strip()
    
    def __repr__(self) -> str:
        return f"<Module: {self.name} v{self.version}>"
