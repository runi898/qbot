"""
淘宝链接转换器

支持两种输入：
  - 淘宝短链 / 完整链接 (https://...)  → 使用 open_gaoyongzhuanlian.ashx + url 参数
  - 淘口令 (￥xxx￥ / (xxx) 等)          → 使用 open_gaoyongzhuanlian_tkl.ashx + tkl 参数
"""

import aiohttp
from typing import Optional, Set
from urllib.parse import urlencode
from config import DEBUG_MODE


class TaobaoConverter:
    """淘宝链接转换器"""

    def __init__(self, config: dict):
        self.app_key = config.get('app_key', '')
        self.sid = config.get('sid', '')
        self.pid = config.get('pid', '')
        self.relation_id = config.get('relation_id', '')

        # 两个不同接口
        self.url_api = "https://api.zhetaoke.com:10001/api/open_gaoyongzhuanlian.ashx"
        self.tkl_api = "https://api.zhetaoke.com:10001/api/open_gaoyongzhuanlian_tkl.ashx"

    def _is_url(self, text: str) -> bool:
        """判断输入是 URL 还是口令"""
        t = text.strip()
        return t.startswith("http://") or t.startswith("https://")

    def _base_params(self) -> dict:
        p = {
            "appkey": self.app_key,
            "sid": self.sid,
            "pid": self.pid,
            "signurl": 5,
        }
        if self.relation_id:
            p["relation_id"] = self.relation_id
        return p

    async def convert(self, token: str, processed_titles: Set[str], show_commission: bool = True) -> Optional[str]:
        """
        转换淘宝链接或口令

        Args:
            token: 淘宝链接（s.tb.cn / taobao.com / tmall.com）或淘口令
            processed_titles: 已处理的标题集合（用于去重）
            show_commission: 是否显示佣金（默认True）

        Returns:
            转换结果字符串，失败返回 None
        """
        try:
            async with aiohttp.ClientSession() as session:

                if self._is_url(token):
                    # ── URL 模式：open_gaoyongzhuanlian.ashx + url 参数 ──────
                    api_endpoint = self.url_api
                    params = self._base_params()
                    params["url"] = token          # aiohttp 自动编码，无需 quote()
                    if DEBUG_MODE:
                        print(f"[TaobaoConverter] 检测到淘宝URL，使用URL接口: {token}")
                else:
                    # ── 口令模式：open_gaoyongzhuanlian_tkl.ashx + tkl 参数 ──
                    api_endpoint = self.tkl_api
                    params = self._base_params()
                    params["tkl"] = token          # aiohttp 自动编码，无需 quote()
                    if DEBUG_MODE:
                        print(f"[TaobaoConverter] 检测到淘口令，使用TKL接口: {token}")

                if DEBUG_MODE:
                    full_url = f"{api_endpoint}?{urlencode(params)}"
                    print(f"[TaobaoConverter] 返利API完整请求URL: {full_url}")

                async with session.get(api_endpoint, params=params, timeout=10) as response:
                    response.raise_for_status()
                    result = await response.json(content_type=None)

                    if DEBUG_MODE:
                        print(f"[TaobaoConverter] API响应: status={result.get('status')}, content_count={len(result.get('content', []))}")

                    if result.get("status") == 200 and "content" in result:
                        content = result["content"][0]
                        title = content.get('tao_title', content.get('title', '未知'))

                        # 去重
                        if title in processed_titles:
                            return None
                        processed_titles.add(title)

                        # 格式化返回
                        pict_url = content.get('pict_url', content.get('pic_url', ''))
                        image_cq = f"[CQ:image,file={pict_url}]" if pict_url else ""

                        result_str = (
                            f"【商品】：{title}\n\n"
                            f"【券后】: {content.get('quanhou_jiage', '未知')}\n"
                        )

                        if show_commission:
                            result_str += f"【佣金】: {content.get('tkfee3', '未知')}\n"

                        result_str += (
                            f"【领券】: {content.get('shorturl2', '未知')}\n"
                            f"【领券口令】: {content.get('tkl', '未知')}\n"
                            f"{image_cq}"
                        )

                        return result_str
                    else:
                        err = result.get('content', result.get('msg', '未知错误'))
                        if DEBUG_MODE:
                            print(f"[TaobaoConverter] API返回非200: status={result.get('status')}, err={err}")
                        return f"淘宝转换失败: {err}"

        except Exception as e:
            print(f"[TaobaoConverter] 转换失败: {e}")
            return None
