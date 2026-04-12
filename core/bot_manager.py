"""
机器人管理器 - 管理在线机器人状态
用于解决循环引用问题，提供全局共享的机器人连接状态
"""
from typing import Dict, List, Any, Optional

# 存储在线机器人的连接对象 (self_id -> websocket)
# 注意：这里我们不直接依赖 WebSocket 类型以避免循环引用，或者只在函数内部使用
_connected_bots: Dict[int, Any] = {}

def add_bot(self_id: int, websocket: Any):
    """
    添加机器人连接
    
    Args:
        self_id: 机器人QQ号
        websocket: WebSocket连接对象
    """
    _connected_bots[self_id] = websocket
    # print(f"[BotManager] 机器人上线: {self_id}, 当前在线: {list(_connected_bots.keys())}")

def remove_bot(self_id: int):
    """
    移除机器人连接
    
    Args:
        self_id: 机器人QQ号
    """
    if self_id in _connected_bots:
        del _connected_bots[self_id]
        # print(f"[BotManager] 机器人下线: {self_id}")

def get_online_bots() -> List[int]:
    """
    获取在线机器人QQ列表
    
    Returns:
        机器人QQ号列表
    """
    return list(_connected_bots.keys())

def get_bot_connection(self_id: int) -> Any:
    """
    获取指定机器人的连接对象
    
    Args:
        self_id: 机器人QQ号
        
    Returns:
        WebSocket连接对象，如果不存在返回None
    """
    return _connected_bots.get(self_id)

# 存储机器人所在的群列表 (self_id -> set(group_ids))
_bot_groups: Dict[int, set] = {}

def update_bot_groups(self_id: int, groups: List[int]):
    """
    更新机器人所在的群列表
    
    Args:
        self_id: 机器人QQ号
        groups: 群号列表
    """
    _bot_groups[self_id] = set(groups)
    # print(f"[BotManager] 更新机器人 {self_id} 的群列表: {len(groups)} 个群")

def is_bot_in_group(self_id: int, group_id: int) -> bool:
    """
    判断机器人是否在指定群中
    
    Args:
        self_id: 机器人QQ号
        group_id: 群号
        
    Returns:
        bool: 是否在群中（如果未获取到群列表，默认视为在群中以避免误判，或者视为不在？
              为了安全起见，如果没有数据，默认返回False（不在群），这样会尝试下一个机器人。
              但在启动初期可能还没获取到群列表。
    """
    # 如果没有该机器人的群信息，默认返回True（假设在群，避免所有机器人都以为自己不在群而不响应）
    # 或者我们确保只有获取到群列表后才认为“在/不在”。
    # 策略：如果没数据，假设在。如有数据，按数据判断。
    if self_id not in _bot_groups:
        return True 
    return group_id in _bot_groups[self_id]

def clear_bot_groups(self_id: int):
    """
    清除指定机器人的群列表缓存
    """
    if self_id in _bot_groups:
        del _bot_groups[self_id]

# === 机器人对象包装类 (提供兼容性接口) ===
import json
import asyncio

class Bot:
    """机器人包装类，提供与 OneBot API 交互的方法"""
    def __init__(self, self_id: int, websocket: Any):
        self.self_id = self_id
        self.ws = websocket

    async def send_group_msg(self, group_id: int, message: str, auto_escape: bool = False):
        """发送群消息"""
        payload = {
            "action": "send_group_msg",
            "params": {
                "group_id": group_id,
                "message": message,
                "auto_escape": auto_escape
            },
            "echo": f"send_group_msg_{self.self_id}_{int(asyncio.get_event_loop().time())}"
        }
        await self.ws.send_text(json.dumps(payload))

    async def send_private_msg(self, user_id: int, message: str, auto_escape: bool = False):
        """发送私聊消息"""
        payload = {
            "action": "send_private_msg",
            "params": {
                "user_id": user_id,
                "message": message,
                "auto_escape": auto_escape
            },
            "echo": f"send_private_msg_{self.self_id}_{int(asyncio.get_event_loop().time())}"
        }
        await self.ws.send_text(json.dumps(payload))

def get_bot(self_id: int) -> Optional[Bot]:
    """获取指定机器人的兼容性对象实例"""
    ws = get_bot_connection(self_id)
    if ws:
        return Bot(self_id, ws)
    return None

def get_all_bots() -> List[Bot]:
    """获取所有在线机器人的兼容性对象实例列表"""
    bots = []
    for self_id, ws in _connected_bots.items():
        bots.append(Bot(self_id, ws))
    return bots
