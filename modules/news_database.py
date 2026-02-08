"""
线报数据库管理

功能：
- 存储收集到的线报
- 去重（基于URL）
- 自动删除1分钟前的旧线报
"""

import sqlite3
from typing import Optional, List, Dict
from datetime import datetime, timedelta
import asyncio


class NewsDatabase:
    """线报数据库管理器"""
    
    def __init__(self, db_file: str = "news.db"):
        self.db_file = db_file
        self._init_database()
    
    def _init_database(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # 创建线报表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                original_url TEXT NOT NULL UNIQUE,
                converted_url TEXT,
                converted_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                forwarded BOOLEAN DEFAULT 0,
                forwarded_at TIMESTAMP
            )
        """)
        
        # 创建索引
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_original_url ON news(original_url)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_forwarded ON news(forwarded)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON news(created_at)")
        
        conn.commit()
        conn.close()
        
        print(f"[NewsDatabase] 数据库已初始化: {self.db_file}")
    
    def add_news(self, title: str, original_url: str, converted_url: str, converted_message: str) -> bool:
        """
        添加线报（带去重）
        
        Args:
            title: 商品标题
            original_url: 原始URL
            converted_url: 转换后的URL
            converted_message: 转换后的完整消息
        
        Returns:
            是否成功添加（False表示重复）
        """
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT INTO news (title, original_url, converted_url, converted_message)
                VALUES (?, ?, ?, ?)
            """, (title, original_url, converted_url, converted_message))
            
            conn.commit()
            print(f"[NewsDatabase] 新线报已保存: {title[:30]}...")
            return True
        except sqlite3.IntegrityError:
            # URL重复
            print(f"[NewsDatabase] 线报重复，跳过: {title[:30]}...")
            return False
        except Exception as e:
            print(f"[NewsDatabase] 保存线报失败: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()
    
    def get_pending_news(self, limit: int = 10) -> List[Dict]:
        """
        获取待转发的线报
        
        Args:
            limit: 最多获取数量
        
        Returns:
            线报列表
        """
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, title, converted_url, converted_message
            FROM news
            WHERE forwarded = 0
            ORDER BY created_at ASC
            LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {
                'id': row[0],
                'title': row[1],
                'converted_url': row[2],
                'converted_message': row[3],
            }
            for row in rows
        ]
    
    def mark_as_forwarded(self, news_id: int):
        """标记线报为已转发"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE news
            SET forwarded = 1, forwarded_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (news_id,))
        
        conn.commit()
        conn.close()
    
    def cleanup_old_news(self, minutes: int = 1):
        """
        删除N分钟前的旧线报
        
        Args:
            minutes: 保留时间（分钟）
        """
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # 计算截止时间
        cutoff_time = datetime.now() - timedelta(minutes=minutes)
        
        cursor.execute("""
            DELETE FROM news
            WHERE created_at < ?
        """, (cutoff_time,))
        
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        if deleted_count > 0:
            print(f"[NewsDatabase] 已删除 {deleted_count} 条旧线报（>{minutes}分钟）")
        
        return deleted_count
    
    async def start_cleanup_task(self, interval: int = 60):
        """
        启动定时清理任务
        
        Args:
            interval: 清理间隔（秒）
        """
        print(f"[NewsDatabase] 启动定时清理任务（每{interval}秒）")
        
        while True:
            try:
                await asyncio.sleep(interval)
                self.cleanup_old_news(minutes=1)
            except Exception as e:
                print(f"[NewsDatabase] 清理任务错误: {e}")


# 全局实例
news_db = NewsDatabase()
