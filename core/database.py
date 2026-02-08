"""
DatabaseManager - 数据库管理器

统一的数据库访问接口
"""

import sqlite3
import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager


class DatabaseManager:
    """数据库管理器 - 单例模式"""
    
    _instance = None
    
    def __new__(cls, db_file: str = "messages.db"):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, db_file: str = "messages.db"):
        if self._initialized:
            return
        
        self.db_file = db_file
        self._init_database()
        self._initialized = True
    
    def _init_database(self) -> None:
        """初始化数据库表结构"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # 创建消息表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER,
                user_id INTEGER,
                message_id INTEGER,
                raw_message TEXT,
                recalled BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(group_id, message_id) ON CONFLICT IGNORE
            )
        ''')
        
        # 检查并添加缺失的列
        cursor.execute("PRAGMA table_info(messages)")
        columns = {col[1] for col in cursor.fetchall()}
        
        if 'recalled' not in columns:
            cursor.execute("ALTER TABLE messages ADD COLUMN recalled BOOLEAN DEFAULT 0")
        
        if 'created_at' not in columns:
            cursor.execute("ALTER TABLE messages ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        
        conn.commit()
        conn.close()
        print(f"[DatabaseManager] 数据库已初始化: {self.db_file}")
    
    @contextmanager
    def get_connection(self):
        """
        获取数据库连接（上下文管理器）
        
        用法:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(...)
        """
        conn = sqlite3.connect(self.db_file)
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    # ========== 消息相关操作 ==========
    
    def save_message(self, group_id: Optional[int], user_id: int, 
                    message_id: int, raw_message: str) -> bool:
        """
        保存消息到数据库
        
        Returns:
            bool: 是否成功插入（False 表示已存在）
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO messages (group_id, user_id, message_id, raw_message) VALUES (?, ?, ?, ?)",
                (group_id, user_id, message_id, raw_message)
            )
            return cursor.rowcount > 0
    
    def mark_recalled(self, message_id: int) -> bool:
        """
        标记消息为已撤回
        
        Returns:
            bool: 是否成功更新
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE messages SET recalled = 1 WHERE message_id = ?",
                (message_id,)
            )
            
            # 如果消息不存在，插入一条已撤回记录
            if cursor.rowcount == 0:
                cursor.execute(
                    "INSERT INTO messages (message_id, raw_message, recalled) VALUES (?, ?, ?)",
                    (message_id, "[已撤回]", 1)
                )
            
            return True
    
    def get_unrecalled_messages(self, group_id: int, limit: Optional[int] = None) -> List[int]:
        """
        获取未撤回的消息 ID 列表
        
        Args:
            group_id: 群号
            limit: 限制数量
            
        Returns:
            List[int]: 消息 ID 列表
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT message_id FROM messages WHERE group_id = ? AND recalled = 0 ORDER BY id DESC"
            
            if limit:
                query += f" LIMIT {limit}"
            
            cursor.execute(query, (group_id,))
            return [row[0] for row in cursor.fetchall()]
    
    def get_user_messages(self, group_id: int, user_id: int, 
                         limit: Optional[int] = None) -> List[int]:
        """获取指定用户的未撤回消息"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT message_id FROM messages WHERE group_id = ? AND user_id = ? AND recalled = 0 ORDER BY id DESC"
            
            if limit:
                query += f" LIMIT {limit}"
            
            cursor.execute(query, (group_id, user_id))
            return [row[0] for row in cursor.fetchall()]
    
    # ========== 数据库清理 ==========
    
    def cleanup_old_messages(self, days: int = 7) -> int:
        """
        清理指定天数前的已撤回消息
        
        Returns:
            int: 删除的消息数量
        """
        cutoff_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        cutoff_str = cutoff_date.isoformat()
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM messages WHERE recalled = 1 AND created_at < ?",
                (cutoff_str,)
            )
            deleted_count = cursor.rowcount
            
            # 优化数据库
            cursor.execute("VACUUM")
            
            return deleted_count
    
    def cleanup_all_recalled(self) -> int:
        """清理所有已撤回消息"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM messages WHERE recalled = 1")
            deleted_count = cursor.rowcount
            
            cursor.execute("VACUUM")
            
            return deleted_count
    
    # ========== 统计信息 ==========
    
    def get_stats(self) -> Dict[str, Any]:
        """获取数据库统计信息"""
        import os
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # 总消息数
            cursor.execute("SELECT COUNT(*) FROM messages")
            total = cursor.fetchone()[0]
            
            # 已撤回消息数
            cursor.execute("SELECT COUNT(*) FROM messages WHERE recalled = 1")
            recalled = cursor.fetchone()[0]
            
            # 最早消息时间
            cursor.execute("SELECT MIN(created_at) FROM messages")
            oldest = cursor.fetchone()[0]
            
            # 数据库文件大小
            db_size = os.path.getsize(self.db_file) / (1024 * 1024)  # MB
            
            return {
                'total_messages': total,
                'recalled_messages': recalled,
                'active_messages': total - recalled,
                'oldest_message': oldest,
                'db_size_mb': round(db_size, 2)
            }
