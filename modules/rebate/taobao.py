"""
淘宝链接转换器
"""

import aiohttp
from typing import Optional, Set
from urllib.parse import quote


class TaobaoConverter:
    """淘宝链接转换器"""
    
    def __init__(self, config: dict):
        self.app_key = config.get('app_key', '')
        self.sid = config.get('sid', '')
        self.pid = config.get('pid', '')
        self.relation_id = config.get('relation_id', '')
        self.base_url = "https://api.zhetaoke.com:10001/api/open_gaoyongzhuanlian_tkl.ashx"
    
    async def convert(self, tkl: str, processed_titles: Set[str], show_commission: bool = True) -> Optional[str]:
        """
        转换淘宝口令
        
        Args:
            tkl: 淘宝口令
            processed_titles: 已处理的标题集合（用于去重）
            show_commission: 是否显示佣金（默认True）
            
        Returns:
            转换结果字符串，失败返回 None
        """
        try:
            async with aiohttp.ClientSession() as session:
                params = {
                    "appkey": self.app_key,
                    "sid": self.sid,
                    "pid": self.pid,
                    "relation_id": self.relation_id,
                    "tkl": quote(tkl),
                    "signurl": 5
                }
                
                async with session.get(self.base_url, params=params, timeout=10) as response:
                    response.raise_for_status()
                    result = await response.json(content_type=None)
                    
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
                        
                        # 只有在允许显示佣金时才添加佣金信息
                        if show_commission:
                            result_str += f"【佣金】: {content.get('tkfee3', '未知')}\n"
                        
                        result_str += (
                            f"【领券】: {content.get('shorturl2', '未知')}\n"
                            f"【领券口令】: {content.get('tkl', '未知')}\n"
                            f"{image_cq}"
                        )
                        
                        return result_str
                    else:
                        return f"淘宝转换失败: {result.get('content', '未知错误')}"
                        
        except Exception as e:
            print(f"[TaobaoConverter] 转换失败: {e}")
            return None
