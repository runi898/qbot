"""
QBot 核心框架包
"""

from .base_module import BaseModule, ModuleResponse, ModuleContext
from .module_loader import ModuleLoader
from .event_bus import EventBus
from .database import DatabaseManager

__all__ = [
    'BaseModule',
    'ModuleResponse',
    'ModuleContext',
    'ModuleLoader',
    'EventBus',
    'DatabaseManager'
]

__version__ = '2.0.0'
