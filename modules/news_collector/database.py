"""
线报数据库管理模块

功能:
- 创建线报数据库表
- 提供 CRUD 操作
- 异步写入数据库
- 定时清理过期数据
"""

import sqlite3
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional


class NewsDatabase:
    """线报数据库管理器"""

    DEDUP_WINDOW_SECONDS = 40  # pict_url 二次去重窗口（秒）

    def __init__(self, db_file: str = "news.db"):
        self.db_file = db_file
        # pict_url -> last_seen_timestamp
        self._pict_recent: Dict[str, float] = {}
        self.init_database()

    def init_database(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        # 创建线报表
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                item_id TEXT NOT NULL,
                title TEXT NOT NULL,
                original_url TEXT,
                converted_url TEXT,
                original_message TEXT,
                converted_message TEXT,
                pict_url TEXT,
                source_qq INTEGER,
                source_group INTEGER,
                collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                forwarded BOOLEAN DEFAULT 0,
                forwarded_at TIMESTAMP,
                UNIQUE(platform, item_id) ON CONFLICT IGNORE
            )
            """
        )

        # 创建转发记录表
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS news_forward_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                news_id INTEGER,
                target_qq INTEGER,
                target_group INTEGER,
                forwarded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                success BOOLEAN DEFAULT 1,
                FOREIGN KEY (news_id) REFERENCES news_items(id)
            )
            """
        )

        # 创建索引
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_news_platform_item 
            ON news_items(platform, item_id)
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_news_collected_at 
            ON news_items(collected_at)
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_news_forwarded 
            ON news_items(forwarded)
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_news_pict_url
            ON news_items(pict_url, collected_at)
            """
        )

        conn.commit()
        conn.close()
        print("[✓] 线报数据库初始化完成")

    async def insert_news(self, news_data: Dict) -> Optional[int]:
        """
        插入线报数据（异步）

        Args:
            news_data: 线报数据字典

        Returns:
            插入的记录ID，如果重复则返回None
        """

        def _insert():
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            now_ts = datetime.now().timestamp()
            pict_url = news_data.get("pict_url")

            if pict_url:
                # 二次去重：40 秒内相同 pict_url 直接忽略
                last = self._pict_recent.get(pict_url)
                if last and (now_ts - last) <= self.DEDUP_WINDOW_SECONDS:
                    conn.close()
                    return None
                self._prune_pict_cache(now_ts)

            try:
                cursor.execute(
                    """
                    INSERT INTO news_items 
                    (platform, item_id, title, original_url, converted_url, 
                     original_message, converted_message, pict_url, source_qq, source_group)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        news_data.get("platform"),
                        news_data.get("item_id"),
                        news_data.get("title"),
                        news_data.get("original_url"),
                        news_data.get("converted_url"),
                        news_data.get("original_message"),
                        news_data.get("converted_message"),
                        pict_url,
                        news_data.get("source_qq"),
                        news_data.get("source_group"),
                    ),
                )

                news_id = cursor.lastrowid
                conn.commit()
                if pict_url:
                    self._pict_recent[pict_url] = now_ts
                return news_id
            except sqlite3.IntegrityError:
                # 重复数据，忽略
                return None
            finally:
                conn.close()

        return await asyncio.to_thread(_insert)

    def _prune_pict_cache(self, now_ts: float) -> None:
        expire_before = now_ts - self.DEDUP_WINDOW_SECONDS
        for k, ts in list(self._pict_recent.items()):
            if ts < expire_before:
                self._pict_recent.pop(k, None)

    def get_pending_news(self, limit: int = 10) -> List[Dict]:
        """
        获取待转发的线报

        Args:
            limit: 最大获取数量

        Returns:
            线报列表
        """
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, platform, item_id, title, converted_url, converted_message
            FROM news_items
            WHERE forwarded = 0
            ORDER BY collected_at ASC
            LIMIT ?
            """,
            (limit,),
        )

        rows = cursor.fetchall()
        conn.close()

        return [
            {
                "id": row[0],
                "platform": row[1],
                "item_id": row[2],
                "title": row[3],
                "converted_url": row[4],
                "converted_message": row[5],
            }
            for row in rows
        ]

    def mark_as_forwarded(self, news_id: int):
        """标记线报为已转发"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE news_items
            SET forwarded = 1, forwarded_at = ?
            WHERE id = ?
            """,
            (datetime.now(), news_id),
        )

        conn.commit()
        conn.close()

    def log_forward(self, news_id: int, target_qq: int, target_group: int, success: bool = True):
        """记录转发日志"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO news_forward_log (news_id, target_qq, target_group, success)
            VALUES (?, ?, ?, ?)
            """,
            (news_id, target_qq, target_group, success),
        )

        conn.commit()
        conn.close()

    async def cleanup_old_news(self, retention_seconds: int = 40):
        """清理 retention_seconds 前的线报数据（异步）"""

        def _cleanup():
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()

            cutoff = datetime.now() - timedelta(seconds=retention_seconds)

            cursor.execute(
                """
                DELETE FROM news_items
                WHERE collected_at < ?
                """,
                (cutoff,),
            )

            deleted = cursor.rowcount
            conn.commit()
            conn.close()

            return deleted

        deleted = await asyncio.to_thread(_cleanup)
        if deleted > 0:
            print(f"[清理] 删除了 {deleted} 条过期线报（>{retention_seconds}秒）")

    def get_stats(self) -> Dict:
        """获取统计信息"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM news_items")
        total = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM news_items WHERE forwarded = 1")
        forwarded = cursor.fetchone()[0]

        pending = total - forwarded

        conn.close()

        return {
            "total": total,
            "forwarded": forwarded,
            "pending": pending,
        }

    # ========== 订阅功能支持 ==========

    def init_subscription_table(self):
        """初始化订阅表（如果不存在）"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # 创建订阅表
        # user_id: 订阅用户的QQ
        # keyword: 订阅关键词
        # is_paused: 是否暂停订阅 (0: 正常, 1: 暂停)
        # created_at: 创建时间
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                keyword TEXT NOT NULL,
                is_paused BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, keyword) ON CONFLICT IGNORE
            )
            """
        )
        
        # 创建索引
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_subs_user ON subscriptions(user_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_subs_keyword ON subscriptions(keyword)"
        )
        
        conn.commit()
        conn.close()

    def get_all_subscriptions(self) -> List[Dict]:
        """
        获取所有有效的订阅信息（用于启动时加载到内存）
        Returns:
            List[Dict]: [{'user_id': 123, 'keyword': '抽纸', 'is_paused': 0}, ...]
        """
        self.init_subscription_table()  # 确保表存在
        
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT user_id, keyword, is_paused FROM subscriptions"
        )
        
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {
                "user_id": row[0],
                "keyword": row[1],
                "is_paused": bool(row[2])
            }
            for row in rows
        ]

    def add_subscription(self, user_id: int, keyword: str) -> bool:
        """添加订阅"""
        self.init_subscription_table()
        
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "INSERT INTO subscriptions (user_id, keyword) VALUES (?, ?)",
                (user_id, keyword)
            )
            success = cursor.rowcount > 0
            conn.commit()
            return success
        except sqlite3.IntegrityError:
            return False  # 已存在
        finally:
            conn.close()

    def remove_subscription(self, user_id: int, keyword: str) -> bool:
        """取消订阅"""
        self.init_subscription_table()
        
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute(
            "DELETE FROM subscriptions WHERE user_id = ? AND keyword = ?",
            (user_id, keyword)
        )
        
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def clear_user_subscriptions(self, user_id: int) -> int:
        """清空用户的所有订阅"""
        self.init_subscription_table()
        
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute(
            "DELETE FROM subscriptions WHERE user_id = ?",
            (user_id,)
        )
        
        count = cursor.rowcount
        conn.commit()
        conn.close()
        return count

    def get_user_subscriptions(self, user_id: int) -> List[str]:
        """获取用户的所有订阅关键词"""
        self.init_subscription_table()
        
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT keyword FROM subscriptions WHERE user_id = ?",
            (user_id,)
        )
        
        keywords = [row[0] for row in cursor.fetchall()]
        conn.close()
        return keywords

    def set_subscription_pause(self, user_id: int, pause: bool) -> int:
        """设置用户订阅暂停状态"""
        self.init_subscription_table()
        
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute(
            "UPDATE subscriptions SET is_paused = ? WHERE user_id = ?",
            (1 if pause else 0, user_id)
        )
        
        count = cursor.rowcount
        conn.commit()
        conn.close()
        return count


# 全局数据库实例
news_db = NewsDatabase()


async def start_cleanup_task(retention_seconds: int = 40, interval_seconds: int = 10):
    """启动定时清理任务"""
    while True:
        await asyncio.sleep(interval_seconds)
        await news_db.cleanup_old_news(retention_seconds=retention_seconds)
