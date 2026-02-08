"""
EventBus - 事件总线

用于模块间通信，实现解耦
"""

import asyncio
from typing import Dict, List, Callable, Any
from dataclasses import dataclass


@dataclass
class Event:
    """事件数据类"""
    name: str
    data: Dict[str, Any]
    source: str = "system"


class EventBus:
    """事件总线 - 单例模式"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._listeners: Dict[str, List[Callable]] = {}
        self._initialized = True
    
    def subscribe(self, event_name: str, callback: Callable) -> None:
        """
        订阅事件
        
        Args:
            event_name: 事件名称
            callback: 回调函数（可以是同步或异步）
        """
        if event_name not in self._listeners:
            self._listeners[event_name] = []
        
        self._listeners[event_name].append(callback)
        print(f"[EventBus] 订阅事件: {event_name}")
    
    def unsubscribe(self, event_name: str, callback: Callable) -> None:
        """
        取消订阅事件
        
        Args:
            event_name: 事件名称
            callback: 回调函数
        """
        if event_name in self._listeners:
            self._listeners[event_name].remove(callback)
            print(f"[EventBus] 取消订阅: {event_name}")
    
    async def publish(self, event: Event) -> None:
        """
        发布事件
        
        Args:
            event: 事件对象
        """
        if event.name not in self._listeners:
            return
        
        print(f"[EventBus] 发布事件: {event.name} (来自 {event.source})")
        
        for callback in self._listeners[event.name]:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                print(f"[EventBus] 事件处理器错误: {e}")
    
    async def emit(self, event_name: str, data: Dict[str, Any], source: str = "system") -> None:
        """
        快捷方法：发布事件
        
        Args:
            event_name: 事件名称
            data: 事件数据
            source: 事件来源
        """
        event = Event(name=event_name, data=data, source=source)
        await self.publish(event)
    
    def clear(self) -> None:
        """清空所有订阅"""
        self._listeners.clear()
        print("[EventBus] 已清空所有订阅")
    
    def get_listeners(self, event_name: str) -> List[Callable]:
        """获取指定事件的所有监听器"""
        return self._listeners.get(event_name, [])
