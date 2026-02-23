"""
淘宝线报收集模块

功能：
- 监听指定群的淘宝链接/淘口令
- 调用折淘客API转换为推广链接
- 去重并存储（窗口期内）
- 保留原文案，只替换URL/口令
- 可按配置转发到目标群
- 触发关键词订阅通知
"""

import re
import json
import aiohttp
import asyncio
import hashlib
import time
from typing import Optional, Dict, List
from urllib.parse import quote

from core.base_module import BaseModule, ModuleContext, ModuleResponse
from modules.news_collector.database import news_db
from config import NEWS_TAOBAO_CONFIG, NEWS_FORWARDER_CONFIG, TAOBAO_CONFIG, DEBUG_MODE, get_bot_qq_list


class TaobaoNewsCollector:
    """淘宝线报收集器（内部类）"""

    def __init__(self, config: Dict):
        self.name = "TBCollector"
        self.config = config
        self.api_config = config.get("api", {})
        self.keywords = config.get("keywords", ["淘宝", "天猫", "taobao", "tmall", "tb.cn"])
        self.reply_groups: List[int] = config.get("reply_groups", [])

        # 淘宝链接/口令匹配模式
        self.tb_patterns = [
            r"https?://[^\s<>]*(?:taobao\.|tmall\.)[^\s<>]+",
            r"https?://tb\.cn/\w+",
            r"https?://s\.taobao\.com/\S+",
            r"https?://detail\.tmall\.com/\S+",
            r"https?://item\.taobao\.com/\S+",
        ]
        # 淘口令匹配（带有￥符号的短代码）
        self.tkl_patterns = [
            r"(?:￥|\$)([0-9A-Za-z()]*[A-Za-z][0-9A-Za-z()]{10})(?:￥|\$)?(?![0-9A-Za-z])",
            r"tk=([0-9A-Za-z]{11,12})",
            r"\(([0-9A-Za-z]{11})\)",
            r"₤([0-9A-Za-z]{13})₤",
        ]

        print("[✓] 淘宝线报收集器初始化完成")

    def has_tb_link(self, message: str) -> bool:
        """判断消息是否包含淘宝链接或淘口令"""
        for pattern in self.tb_patterns:
            if re.search(pattern, message, re.IGNORECASE):
                return True
        for pattern in self.tkl_patterns:
            if re.search(pattern, message):
                return True
        return False

    def extract_tb_url(self, message: str) -> Optional[str]:
        """提取淘宝链接"""
        for pattern in self.tb_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return match.group(0)
        return None

    def extract_tkl(self, message: str) -> Optional[str]:
        """提取淘口令"""
        for pattern in self.tkl_patterns:
            match = re.search(pattern, message)
            if match:
                # 重新构造带有￥符号的口令，或返回原始匹配
                full_match = match.group(0)
                return full_match
        return None

    async def convert_tb_url(self, url: str) -> Optional[Dict]:
        """通过折淘客API转换淘宝链接"""
        if DEBUG_MODE:
            print(f"[{self.name}] 开始转换淘宝链接: {url}")
        try:
            app_key = TAOBAO_CONFIG.get("app_key") or self.api_config.get("app_key")
            sid = TAOBAO_CONFIG.get("sid") or self.api_config.get("sid")
            pid = TAOBAO_CONFIG.get("pid") or self.api_config.get("pid")
            relation_id = TAOBAO_CONFIG.get("relation_id") or self.api_config.get("relation_id", "")

            if not app_key or not sid or not pid:
                print(f"[{self.name}] 错误：淘宝API配置不完整(app_key/sid/pid)")
                return None

            api_url = "https://api.zhetaoke.com:10001/api/open_gaoyongzhuanlian.ashx"
            params = {
                "appkey": app_key,
                "sid": sid,
                "pid": pid,
                "url": url,
                "signurl": 5,
            }
            if relation_id:
                params["relation_id"] = relation_id

            if DEBUG_MODE:
                print(f"[{self.name}] API请求参数: {params}")

            async with aiohttp.ClientSession() as session:
                if DEBUG_MODE:
                    print(f"[{self.name}] 发送API请求到: {api_url}")
                async with session.get(api_url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if DEBUG_MODE:
                        print(f"[{self.name}] API响应状态码: {response.status}")

                    if response.status != 200:
                        print(f"[{self.name}] API请求失败，状态码: {response.status}")
                        return None

                    resp_text = await response.text()
                    if DEBUG_MODE:
                        print(f"[{self.name}] API原始响应: {resp_text[:500]}")
                    data = json.loads(resp_text)

                    if data.get("status") == 200 and data.get("content"):
                        content = data["content"][0]
                        short_url = (
                            content.get("shorturl2")
                            or content.get("shorturl")
                            or content.get("coupon_click_url")
                        )
                        pict_url = (
                            content.get("pict_url")
                            or content.get("pic_url")
                            or content.get("imageUrl")
                        )
                        tkl = content.get("tkl") or content.get("tao_token")
                        return {
                            "item_id": content.get("tao_id") or content.get("item_id"),
                            "title": (
                                content.get("tao_title")
                                or content.get("title")
                                or "淘宝商品"
                            ),
                            "short_url": short_url,
                            "long_url": url,
                            "price": content.get("quanhou_jiage") or content.get("price"),
                            "commission": content.get("tkfee3") or content.get("commission"),
                            "pict_url": pict_url,
                            "tkl": tkl,
                        }

                    if DEBUG_MODE:
                        print(f"[{self.name}] API返回状态异常: {data}")

        except asyncio.TimeoutError:
            print(f"[{self.name}] ❌ API请求超时(>10s)")
        except Exception as e:
            print(f"[{self.name}] ❌ API请求失败: {e}")
            import traceback
            traceback.print_exc()

        return None

    async def convert_tkl(self, tkl: str) -> Optional[Dict]:
        """通过折淘客API转换淘口令"""
        if DEBUG_MODE:
            print(f"[{self.name}] 开始转换淘口令: {tkl}")
        try:
            app_key = TAOBAO_CONFIG.get("app_key") or self.api_config.get("app_key")
            sid = TAOBAO_CONFIG.get("sid") or self.api_config.get("sid")
            pid = TAOBAO_CONFIG.get("pid") or self.api_config.get("pid")
            relation_id = TAOBAO_CONFIG.get("relation_id") or self.api_config.get("relation_id", "")

            if not app_key or not sid or not pid:
                print(f"[{self.name}] 错误：淘宝API配置不完整(app_key/sid/pid)")
                return None

            api_url = "https://api.zhetaoke.com:10001/api/open_gaoyongzhuanlian_tkl.ashx"
            params = {
                "appkey": app_key,
                "sid": sid,
                "pid": pid,
                "tkl": quote(tkl),
                "signurl": 5,
            }
            if relation_id:
                params["relation_id"] = relation_id

            if DEBUG_MODE:
                print(f"[{self.name}] 淘口令API请求参数: {params}")

            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        print(f"[{self.name}] 淘口令API请求失败，状态码: {response.status}")
                        return None

                    resp_text = await response.text()
                    if DEBUG_MODE:
                        print(f"[{self.name}] 淘口令API原始响应: {resp_text[:500]}")
                    data = json.loads(resp_text)

                    if data.get("status") == 200 and data.get("content"):
                        content = data["content"][0]
                        short_url = (
                            content.get("shorturl2")
                            or content.get("shorturl")
                            or content.get("coupon_click_url")
                        )
                        pict_url = content.get("pict_url") or content.get("pic_url")
                        new_tkl = content.get("tkl") or content.get("tao_token")
                        return {
                            "item_id": content.get("tao_id") or content.get("item_id"),
                            "title": (
                                content.get("tao_title")
                                or content.get("title")
                                or "淘宝商品"
                            ),
                            "short_url": short_url,
                            "long_url": short_url,
                            "price": content.get("quanhou_jiage") or content.get("price"),
                            "commission": content.get("tkfee3") or content.get("commission"),
                            "pict_url": pict_url,
                            "tkl": new_tkl or tkl,
                        }

                    if DEBUG_MODE:
                        print(f"[{self.name}] 淘口令API返回状态异常: {data}")

        except asyncio.TimeoutError:
            print(f"[{self.name}] ❌ 淘口令API请求超时(>10s)")
        except Exception as e:
            print(f"[{self.name}] ❌ 淘口令API请求失败: {e}")
            import traceback
            traceback.print_exc()

        return None

    async def process_message(self, message: str, context: Dict) -> Optional[Dict]:
        """处理消息，提取并转换淘宝链接或口令"""
        converted = None
        original_token = None

        # 优先尝试链接转换
        original_url = self.extract_tb_url(message)
        if original_url:
            converted = await self.convert_tb_url(original_url)
            original_token = original_url
        else:
            # 再尝试口令转换
            tkl = self.extract_tkl(message)
            if tkl:
                converted = await self.convert_tkl(tkl)
                original_token = tkl

        if not converted or not original_token:
            return None

        # ── 用原始 token 的 SHA1 作为 item_id ──────────────────────────────
        # 注意：折淘客 API 每次返回的 tao_id 含 session 追踪码，会因 QQ 不同而不同。
        # 直接用 API 返回的 tao_id 会导致去重失败（同一商品存两条）。
        # 用原始口令/URL 的 SHA1 保证同一商品永远是同一个 item_id。
        stable_item_id = hashlib.sha1(original_token.strip().encode("utf-8")).hexdigest()

        # 构建转换后的消息：替换原始链接/口令为新口令或短链
        new_url = converted.get("short_url", "")
        new_tkl = converted.get("tkl", "")

        if new_url:
            converted_message = message.replace(original_token, new_url)
        elif new_tkl:
            converted_message = message.replace(original_token, new_tkl)
        else:
            converted_message = message

        return {
            "platform": "taobao",
            "item_id": stable_item_id,          # 稳定的 SHA1，不依赖 API 返回值
            "title": converted.get("title"),
            "original_url": original_token,
            "converted_url": new_url or new_tkl,
            "original_message": message,
            "converted_message": converted_message,
            "pict_url": converted.get("pict_url"),
            "source_qq": context.get("qq"),
            "source_group": context.get("group_id"),
            "price": converted.get("price"),
            "commission": converted.get("commission"),
        }


class TaobaoNewsModule(BaseModule):
    """淘宝线报收集模块（继承BaseModule）"""

    def __init__(self):
        super().__init__()
        self._prefix_dedup: Dict[str, float] = {}
        self._prefix_last_cleanup = 0.0
        self._prefix_dedup_window = 300
        self._url_re = re.compile(r'https?://\S+')
        # token 级内存去重（在 API 调用之前就判断，防止多 QQ 重复并发）
        self._token_dedup: Dict[str, float] = {}
        self._token_dedup_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return "淘宝线报收集"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "监听并收集淘宝线报，转换为推广链接"

    @property
    def author(self) -> str:
        return "QBot Team"

    async def on_load(self, config: dict) -> None:
        await super().on_load(config)
        self.config = config
        self.collector = TaobaoNewsCollector(config)
        self.collector_groups = self._build_collector_groups()
        self.forward_targets = self._build_forward_targets()
        self._prefix_dedup_window = (
            config.get("settings", {}).get("dedup_window_seconds")
            or 300
        )
        self.bot_qq_list = get_bot_qq_list()
        print(f"[{self.name}] 模块已加载 (v{self.version})")
        print(f"[{self.name}] 机器人QQ列表: {self.bot_qq_list}")
        print(f"[{self.name}] 收集器群组: {self.collector_groups}")

    async def can_handle(self, message: str, context: ModuleContext) -> bool:
        # 过滤机器人消息（防止循环）
        if context.user_id in self.bot_qq_list:
            if DEBUG_MODE:
                print(f"[{self.name}] 跳过机器人消息: {context.user_id}")
            return False

        if self.config.get("debug", False):
            print(f"[{self.name}] can_handle 检查: group_id={context.group_id}, self_id={context.self_id}")

        if context.group_id is None:
            return False

        if not self._is_collector_group(context.self_id, context.group_id):
            if self.config.get("debug", False):
                print(f"[{self.name}] 群 {context.group_id} 不是淘宝线报收集群组")
            return False

        has_tb = self.collector.has_tb_link(message)
        if self.config.get("debug", False):
            print(f"[{self.name}] 消息包含淘宝链接/口令: {has_tb}")
        return has_tb

    async def handle(self, message: str, context: ModuleContext) -> Optional[ModuleResponse]:
        # ── 第1层：提取原始 token，做内存去重（最快，API调用前就拦截）──────
        original_url = self.collector.extract_tb_url(message)
        original_tkl = None if original_url else self.collector.extract_tkl(message)
        original_token = original_url or original_tkl

        if original_token:
            token_key = hashlib.sha1(original_token.strip().encode("utf-8")).hexdigest()
            now = time.time()
            async with self._token_dedup_lock:
                last = self._token_dedup.get(token_key)
                if last and (now - last) <= self._prefix_dedup_window:
                    if DEBUG_MODE:
                        print(f"[{self.name}] token去重命中，跳过: {original_token[:30]}")
                    return None
                # 立即占位，防止其他 QQ 同时进入
                self._token_dedup[token_key] = now
                # 顺便清理过期 token
                self._token_dedup = {
                    k: v for k, v in self._token_dedup.items()
                    if now - v <= self._prefix_dedup_window
                }

        # ── 第2层：前缀文案去重（URL场景补充）────────────────────────────────
        if self._is_prefix_duplicate(message):
            print(f"[{self.name}] 前缀文案去重命中，跳过处理")
            return None

        result = await self.collector.process_message(
            message,
            {"qq": context.user_id, "group_id": context.group_id},
        )

        if not result:
            return None

        if self.config.get("debug", False):
            print(f"[{self.name}] API返回数据: {result}")

        title = result.get("title") or "未知商品"
        title_display = title[:30] + "..." if len(title) > 30 else title
        if DEBUG_MODE:
            print(f"[{self.name}] ✅ 收集到淘宝线报: {title_display}")

        # item_id 已在 process_message 中设为原始 token 的 SHA1，无需再次兜底
        item_id = result.get("item_id")
        if not item_id:
            item_id = hashlib.sha1((result.get("original_url") or "").encode("utf-8")).hexdigest()
            result["item_id"] = item_id

        # ── 第3层：数据库 UNIQUE 约束去重 ────────────────────────────────────
        news_id = await news_db.insert_news(result)
        if news_id is None:
            if DEBUG_MODE:
                print(f"[{self.name}] 数据库去重命中，跳过")
            return None

        if DEBUG_MODE:
            print(f"[{self.name}] 线报已保存到数据库，ID: {news_id}")

        # 1) 当前群需要回复
        if context.group_id in self.collector.reply_groups:
            print(f"[{self.name}] 当前群 {context.group_id} 需要回复")
            await self._send_to_group(context, context.group_id, result.get("converted_message", ""))
            return None

        # 2) 转发到目标群（由当前账号发送）
        targets = self.forward_targets.get(int(context.self_id), [])
        if DEBUG_MODE:
            print(f"[{self.name}] 转发目标群: {targets}")
        for target_group in targets:
            if DEBUG_MODE:
                print(f"[{self.name}] 正在转发到群 {target_group}")
            await self._send_to_group(context, target_group, result.get("converted_message", ""))
            if DEBUG_MODE:
                print(f"[{self.name}] 已转发到群 {target_group}")

        # 3) 触发关键词订阅通知（异步，不阻塞主流程）
        try:
            from modules.news_subscription.module import SubscriptionManager
            sub_manager = SubscriptionManager()
            if sub_manager and sub_manager.initialized:
                asyncio.create_task(self._notify_subscribers(context, result, sub_manager))
        except ImportError:
            pass
        except Exception as e:
            print(f"[{self.name}] 触发订阅通知失败: {e}")

        return None

    async def _notify_subscribers(self, context: ModuleContext, news_data: dict, sub_manager):
        """检查并通知关键词订阅用户"""
        try:
            title = news_data.get("title", "")
            content_to_match = f"{title} {news_data.get('converted_message', '')}"
            matched_user_ids = sub_manager.get_matches(content_to_match)

            if not matched_user_ids:
                return

            if DEBUG_MODE:
                print(f"[{self.name}] 命中订阅用户: {matched_user_ids}")

            notify_msg = (
                f"【线报提醒】您订阅的关键词有新内容！\n"
                f"----------------\n"
                f"{news_data.get('converted_message', '')}\n"
                f"----------------\n"
                f"退订请回复：取消订阅 关键词"
            )

            from main import bot_manager
            bot = bot_manager.get_bot(context.self_id)
            if not bot:
                all_bots = bot_manager.get_all_bots()
                if all_bots:
                    bot = all_bots[0]

            if not bot:
                print(f"[{self.name}] 没有在线Bot可用于发送通知")
                return

            for user_id in matched_user_ids:
                if str(user_id) == str(context.user_id) and context.message_type == "private":
                    continue
                await bot.send_private_msg(user_id=user_id, message=notify_msg)
                if DEBUG_MODE:
                    print(f"[{self.name}] 已发送订阅通知 -> {user_id}")

        except Exception as e:
            print(f"[{self.name}] 发送订阅通知异常: {e}")

    def _build_collector_groups(self) -> Dict[int, List[int]]:
        """构建收集器群组映射（QQ号 -> 群列表）"""
        collectors = self.config.get("settings", {}).get("collectors", [])
        mapping: Dict[int, List[int]] = {}

        for c in collectors:
            if c.get("type") == "taobao":
                groups = c.get("groups", [])
                mapping.setdefault(0, [])
                mapping[0].extend(groups)
            elif c.get("qq"):
                qq = c.get("qq")
                groups = c.get("groups", [])
                mapping.setdefault(int(qq), [])
                mapping[int(qq)].extend(groups)

        if DEBUG_MODE:
            print(f"[{self.name}] 构建的淘宝收集器群组映射: {mapping}")
        return mapping

    def _build_forward_targets(self) -> Dict[int, List[int]]:
        """构建转发目标映射（与京东模块共享 NEWS_FORWARDER_CONFIG）"""
        forwarders = NEWS_FORWARDER_CONFIG.get("settings", {}).get("forwarders", [])
        mapping: Dict[int, List[int]] = {}
        for f in forwarders:
            qq = f.get("qq")
            targets = f.get("targets", [])
            if isinstance(qq, list):
                for q in qq:
                    mapping.setdefault(int(q), [])
                    mapping[int(q)].extend(targets)
            elif qq:
                mapping.setdefault(int(qq), [])
                mapping[int(qq)].extend(targets)
        return mapping

    def _is_collector_group(self, self_id: int, group_id: int) -> bool:
        """检查指定群是否是淘宝线报收集群组"""
        if group_id in self.collector_groups.get(0, []):
            return True
        if group_id in self.collector_groups.get(int(self_id), []):
            return True
        return False

    def _is_prefix_duplicate(self, message: str) -> bool:
        """使用第一条URL前的文案做极速去重（内存窗口）"""
        now = time.time()
        if now - self._prefix_last_cleanup > 5:
            self._prune_prefix_cache(now)

        prefix = self._extract_prefix_before_url(message)
        if not prefix:
            return False

        key = hashlib.sha1(prefix.encode("utf-8")).hexdigest()
        last = self._prefix_dedup.get(key)
        if last and (now - last) <= self._prefix_dedup_window:
            return True

        self._prefix_dedup[key] = now
        return False

    def _prune_prefix_cache(self, now: float) -> None:
        self._prefix_last_cleanup = now
        expire_before = now - self._prefix_dedup_window
        if expire_before <= 0:
            return
        for k, ts in list(self._prefix_dedup.items()):
            if ts < expire_before:
                self._prefix_dedup.pop(k, None)

    def _extract_prefix_before_url(self, message: str) -> str:
        """提取URL前的文案作为去重key；无URL时用整条文本（用于TKL场景）"""
        # 去掉 CQ 码
        msg = re.sub(r'\[CQ:[^\]]+\]', '', message)
        m = self._url_re.search(msg)
        if m:
            prefix = msg[:m.start()]
        else:
            # 没有 URL（典型的淘口令场景），用整条文本标准化后去重
            prefix = msg
        return self._normalize_prefix(prefix)

    def _normalize_prefix(self, text: str) -> str:
        """标准化前缀文案"""
        text = text.strip().lower()
        text = re.sub(r'\s+', '', text)
        text = re.sub(r'[\W_]+', '', text)
        return text

    async def _send_to_group(self, context: ModuleContext, group_id: int, message: str) -> None:
        """发送消息到指定群"""
        if not message:
            return
        try:
            from starlette.websockets import WebSocketState
            if context.ws and context.ws.client_state == WebSocketState.CONNECTED:
                payload = {
                    "action": "send_group_msg",
                    "params": {"group_id": group_id, "message": message},
                }
                await context.ws.send_text(__import__("json").dumps(payload))
                if DEBUG_MODE:
                    print(f"[{self.name}] 已发送到群 {group_id}")
        except Exception as e:
            print(f"[{self.name}] ❌ 发送失败: {e}")
