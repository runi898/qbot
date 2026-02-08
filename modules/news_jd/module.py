"""
京东线报收集模块

功能：
- 监听指定群的京东链接
- 调用折京客API转换
- 去重并存储（1分钟内）
- 保留原文案，只替换URL
- 可按配置转发到目标群
"""

import re
import json
import aiohttp
import asyncio
import hashlib
import time
from typing import Optional, Dict, List

from core.base_module import BaseModule, ModuleContext, ModuleResponse
from modules.news_collector.database import news_db
from config import NEWS_COLLECTOR_CONFIG, NEWS_FORWARDER_CONFIG, JINGDONG_CONFIG, DEBUG_MODE


class JDNewsCollector:
    """京东线报收集器（内部类）"""

    def __init__(self, config: Dict):
        self.config = config
        self.api_config = config.get("api", {})
        self.keywords = config.get("keywords", ["京东", "JD", "jd.com", "3.cn"])
        self.reply_groups: List[int] = config.get("reply_groups", [])

        self.jd_patterns = [
            r"https?://item\.jd\.com/\d+\.html",
            r"https?://3\.cn/\w+",
            r"https?://u\.jd\.com/\w+",
        ]

        print("[✓] 京东线报收集器初始化完成")

    def has_jd_link(self, message: str) -> bool:
        for pattern in self.jd_patterns:
            if re.search(pattern, message, re.IGNORECASE):
                return True
        return False

    def extract_jd_url(self, message: str) -> Optional[str]:
        for pattern in self.jd_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return match.group(0)
        return None

    async def convert_jd_link(self, url: str) -> Optional[Dict]:
        """京东链接转换"""
        if DEBUG_MODE:
            print(f"[{self.name}] 开始转换京东链接: {url}")
        try:
            appkey = JINGDONG_CONFIG.get("appkey") or self.api_config.get("key")
            union_id = JINGDONG_CONFIG.get("union_id")
            position_id = JINGDONG_CONFIG.get("position_id")

            if not appkey or not union_id:
                print(f"[{self.name}] 错误：京东API配置不完整(appkey/union_id)")
                return None

            api_url = "http://api.zhetaoke.com:20000/api/open_jing_union_open_promotion_byunionid_get.ashx"
            params = {
                "appkey": appkey,
                "materialId": url,
                "unionId": union_id,
                "positionId": position_id,
                "chainType": 3,
                "signurl": 5,
            }

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
                        print(f"[{self.name}] API原始响应: {resp_text[:500]}...")
                    data = json.loads(resp_text)

                    # 尝试解析新格式
                    if "jd_union_open_promotion_byunionid_get_response" in data:
                        response_body = data["jd_union_open_promotion_byunionid_get_response"]
                        result_str = response_body.get("result")
                        if result_str:
                            try:
                                result_json = json.loads(result_str)
                                if result_json.get("code") == 200 and result_json.get("data"):
                                    content = result_json["data"]
                                    short_url = content.get("shortURL") or content.get("clickURL")
                                    pict_url = (
                                        content.get("pict_url")
                                        or content.get("pic_url")
                                        or content.get("imageUrl")
                                        or content.get("imgUrl")
                                    )
                                    return {
                                        "item_id": content.get("skuId") or content.get("sku_id"),
                                        "title": content.get("title") or "京东商品",
                                        "short_url": short_url,
                                        "long_url": short_url,
                                        "price": content.get("price"),
                                        "commission": content.get("commission"),
                                        "pict_url": pict_url,
                                    }
                            except json.JSONDecodeError:
                                print(f"[{self.name}] 解析result JSON失败")

                    # 旧格式解析（保留作为备用）
                    if data.get("status") == 200 and data.get("content"):
                        content = data["content"][0]
                        short_url = content.get("shorturl") or content.get("shortUrl")
                        pict_url = (
                            content.get("pict_url")
                            or content.get("pic_url")
                            or content.get("imageUrl")
                            or content.get("imgUrl")
                        )
                        return {
                            "item_id": content.get("skuId") or content.get("sku_id") or content.get("tao_id"),
                            "title": content.get("skuName") or content.get("name") or content.get("title") or content.get("tao_title"),
                            "short_url": short_url,
                            "long_url": content.get("materialUrl") or content.get("coupon_click_url"),
                            "price": content.get("price") or content.get("finalPrice") or content.get("quanhou_jiage"),
                            "commission": content.get("commisionShare") or content.get("tkfee3"),
                            "pict_url": pict_url,
                        }

                    if DEBUG_MODE:
                        print(f"[{self.name}] API返回状态异常: {data}")

        except asyncio.TimeoutError:
            print("[JDCollector] ?????API????(>10s)")
        except Exception as e:
            print(f"[JDCollector] ?????API????: {e}")
            import traceback
            traceback.print_exc()

        return None


    async def process_message(self, message: str, context: Dict) -> Optional[Dict]:
        if not self.has_jd_link(message):
            return None

        original_url = self.extract_jd_url(message)
        if not original_url:
            return None

        converted = await self.convert_jd_link(original_url)
        if not converted:
            return None

        converted_message = message.replace(original_url, converted.get("short_url") or original_url)

        return {
            "platform": "jd",
            "item_id": converted.get("item_id"),
            "title": converted.get("title"),
            "original_url": original_url,
            "converted_url": converted.get("short_url"),
            "original_message": message,
            "converted_message": converted_message,
            "pict_url": converted.get("pict_url"),
            "source_qq": context.get("qq"),
            "source_group": context.get("group_id"),
            "price": converted.get("price"),
            "commission": converted.get("commission"),
        }


class JDNewsModule(BaseModule):
    """京东线报收集模块（继承BaseModule）"""

    def __init__(self):
        super().__init__()
        self._prefix_dedup = {}
        self._prefix_last_cleanup = 0.0
        self._prefix_dedup_window = 300
        self._url_re = re.compile(r'https?://\S+')

    @property
    def name(self) -> str:
        return "京东线报收集"

    @property
    def version(self) -> str:
        return "1.0.1"

    @property
    def description(self) -> str:
        return "监听并收集京东线报，转换为推广链接"

    @property
    def author(self) -> str:
        return "QBot Team"

    async def on_load(self, config: dict) -> None:
        await super().on_load(config)
        self.config = config  # 保存配置
        self.collector = JDNewsCollector(config)
        self.collector_groups = self._build_collector_groups()
        self.forward_targets = self._build_forward_targets()
        self._prefix_dedup_window = (
            config.get("settings", {}).get("dedup_window_seconds")
            or NEWS_COLLECTOR_CONFIG.get("settings", {}).get("dedup_window_seconds")
            or 300
        )
        print(f"[{self.name}] 模块已加载 (v{self.version})")

    async def can_handle(self, message: str, context: ModuleContext) -> bool:
        if self.config.get("debug", False):
            print(f"[{self.name}] can_handle 检查: group_id={context.group_id}, self_id={context.self_id}")
            print(f"[{self.name}] 收集器群组: {self.collector_groups}")
        
        if context.group_id is None:
            if self.config.get("debug", False):
                print(f"[{self.name}] group_id 为 None，跳过")
            return False
        if not self._is_collector_group(context.self_id, context.group_id):
            if self.config.get("debug", False):
                print(f"[{self.name}] 群 {context.group_id} 不是收集器群组")
            return False
        
        has_jd = self.collector.has_jd_link(message)
        if self.config.get("debug", False):
            print(f"[{self.name}] 消息包含京东链接: {has_jd}")
        return has_jd

    async def handle(self, message: str, context: ModuleContext) -> Optional[ModuleResponse]:
        if self._is_prefix_duplicate(message):
            print(f"[{self.name}] 前缀文案去重命中，跳过处理")
            return None

        result = await self.collector.process_message(
            message,
            {"qq": context.user_id, "group_id": context.group_id},
        )

        if not result:
            return None

        # 调试：打印完整的result数据
        if self.config.get("debug", False):
            print(f"[{self.name}] API返回数据: {result}")

        title = result.get("title") or "未知商品"
        title_display = title[:30] + "..." if len(title) > 30 else title
        if DEBUG_MODE:
            title = result.get("title") or "未知商品"
            title_display = title[:30] + "..." if len(title) > 30 else title
            print(f"[{self.name}] 收集到京东线报: {title_display}")

        item_id = result.get("item_id")
        if not item_id:
            item_id = hashlib.sha1(result.get("original_url", "").encode("utf-8")).hexdigest()
            result["item_id"] = item_id

        news_id = await news_db.insert_news(result)
        if news_id is None:
            if DEBUG_MODE:
                print(f"[{self.name}] 线报重复或保存失败，跳过")
            return None
        
        if DEBUG_MODE:
            print(f"[{self.name}] 线报已保存到数据库，ID: {news_id}")

        # 1) 当前群需要回复
        if context.group_id in self.collector.reply_groups:
            print(f"[{self.name}] 当前群 {context.group_id} 需要回复")
            await self._send_to_group(context, context.group_id, result.get("converted_message", ""))
            return None

        # 2) 转发到 targets（由当前账号发送）
        targets = self.forward_targets.get(int(context.self_id), [])
        if DEBUG_MODE:
            print(f"[{self.name}] 转发目标群: {targets}")
        for target_group in targets:
            if DEBUG_MODE:
                print(f"[{self.name}] 正在转发到群 {target_group}")
            await self._send_to_group(context, target_group, result.get("converted_message", ""))
            if DEBUG_MODE:
                print(f"[{self.name}] 已转发到群 {target_group}")

        return None

    def _build_collector_groups(self) -> Dict[int, List[int]]:
        """构建收集器群组映射（QQ号 -> 群列表）"""
        collectors = self.config.get("settings", {}).get("collectors", [])
        mapping: Dict[int, List[int]] = {}
        
        for c in collectors:
            # 新格式：type + groups（用于京东线报收集）
            if c.get("type") == "jd":
                groups = c.get("groups", [])
                # 使用当前机器人的QQ号作为key
                # 注意：这里我们需要从配置中获取self_id，或者使用一个通用的key
                # 暂时使用0作为通用key，表示所有账号都监听这些群
                mapping.setdefault(0, [])
                mapping[0].extend(groups)
            # 旧格式：qq + groups（向后兼容）
            elif c.get("qq"):
                qq = c.get("qq")
                groups = c.get("groups", [])
                mapping.setdefault(int(qq), [])
                mapping[int(qq)].extend(groups)
        
        if DEBUG_MODE:
            print(f"[{self.name}] 构建的收集器群组映射: {mapping}")
        return mapping

    def _build_forward_targets(self) -> Dict[int, List[int]]:
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
        """检查指定群是否是收集器群组"""
        # 检查通用key（0）- 所有账号都监听的群
        if group_id in self.collector_groups.get(0, []):
            return True
        # 检查特定账号的群
        if group_id in self.collector_groups.get(int(self_id), []):
            return True
        return False

    def _is_prefix_duplicate(self, message: str) -> bool:
        """
        使用第一条URL前的文案做极速去重（内存窗口）
        """
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
        # 去掉 CQ 码
        msg = re.sub(r'\[CQ:[^\]]+\]', '', message)
        m = self._url_re.search(msg)
        if not m:
            return ""
        prefix = msg[:m.start()]
        return self._normalize_prefix(prefix)

    def _normalize_prefix(self, text: str) -> str:
        # 去空白、去标点，保留中文/字母/数字
        text = text.strip().lower()
        text = re.sub(r'\s+', '', text)
        text = re.sub(r'[\W_]+', '', text)
        return text

    async def _send_to_group(self, context: ModuleContext, group_id: int, message: str) -> None:
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
            print(f"[{self.name}] 发送失败: {e}")
