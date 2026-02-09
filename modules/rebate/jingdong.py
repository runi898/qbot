"""
京东链接转换器
"""

import aiohttp
import json
import sys
import os
from typing import Optional, Set
from urllib.parse import quote
from config import DEBUG_MODE

# 导入京东短链转换器
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'news_jd'))
from dwz import JDShortUrlConverter


class JingdongConverter:
    """京东链接转换器"""
    
    def __init__(self, config: dict):
        # 折京客配置
        self.appkey = config.get('appkey', '')
        self.union_id = config.get('union_id', '')
        self.position_id = config.get('position_id', '')
        
        # 京推推配置（使用 jtt_ 前缀）
        self.jtt_appid = config.get('jtt_appid', '')
        self.jtt_appkey = config.get('jtt_appkey', '')
        
        self.base_url = "http://api.zhetaoke.com:20000/api/open_jing_union_open_promotion_byunionid_get.ashx"
        self.command_url = "http://japi.jingtuitui.com/api/get_goods_command"
        
        # 初始化短链转换器
        self.dwz_converter = JDShortUrlConverter(sign_url="http://192.168.8.2:3001/sign")
    
    async def convert(self, material_url: str, processed_titles: Set[str], show_commission: bool = True) -> Optional[str]:
        """
        转换京东链接
        
        Args:
            material_url: 京东链接
            processed_titles: 已处理的标题集合
            show_commission: 是否显示佣金（默认True）
            
        Returns:
            转换结果字符串
        """
        try:
            # 特殊处理：优惠券链接不需要转链，直接返回原链接
            if "coupon.m.jd" in material_url:
                 if DEBUG_MODE:
                     print(f"[京东转换器] 检测到优惠券链接，跳过转链: {material_url}")
                 return f"优惠券: {material_url}"
            
            # 新增：如果是 item.m.jd.com 链接，先转换为短链接
            if material_url.startswith("https://item.m.jd.com"):
                if DEBUG_MODE:
                    print(f"[京东转换器] 检测到 item.m.jd.com 链接，先转换为短链接")
                try:
                    dwz_result = self.dwz_converter.convert(material_url, verbose=False)
                    if dwz_result['success']:
                        material_url = dwz_result['short_url']
                        if DEBUG_MODE:
                            print(f"[京东转换器] 短链转换成功: {material_url}")
                    else:
                        if DEBUG_MODE:
                            print(f"[京东转换器] 短链转换失败，使用原链接: {dwz_result.get('error', '未知错误')}")
                except Exception as e:
                    if DEBUG_MODE:
                        print(f"[京东转换器] 短链转换异常，使用原链接: {str(e)}")

            async with aiohttp.ClientSession() as session:
                # 第一步：获取商品信息和短链接
                params = {
                    "appkey": self.appkey,
                    "materialId": material_url,
                    "unionId": self.union_id,
                    "positionId": self.position_id,
                    "chainType": 3,  # 使用chainType=3
                    "signurl": 5  # 添加signurl=5以获取完整商品信息
                }
                
                if DEBUG_MODE:
                    print(f"[京东转换器] 调用折京客API: {self.base_url} with materialId={material_url}")
                
                # 第一次API调用：获取商品详情和短链接
                async with session.get(self.base_url, params=params, timeout=10) as response:
                    response.raise_for_status()
                    result = await response.json(content_type=None)
                    
                    # 检查第一次API调用是否成功并提取信息
                    if result.get("status") == 200 and "content" in result:
                        content = result["content"][0]
                        jianjie = content.get('jianjie', '未知')
                        
                        pict_url = content.get('pict_url', content.get('pic_url', ''))
                        image_cq = f"[CQ:image,file={pict_url}]" if pict_url else ""
                        short_url_from_zhetaoke = content.get('shorturl', '')  # 从第一次API获取短链接
                        
                        # --- 京推推口令 API 调用 ---
                        # 只有在成功获取到折京客短链接后才调用京推推生成口令
                        jd_command_text = ""
                        if short_url_from_zhetaoke and self.jtt_appid and self.jtt_appkey:
                            # 使用折京客返回的短链接作为 gid 参数
                            command_api_url = f"http://japi.jingtuitui.com/api/get_goods_command?appid={self.jtt_appid}&appkey={self.jtt_appkey}&unionid={self.union_id}&gid={quote(short_url_from_zhetaoke)}"
                            if self.position_id:
                                command_api_url += f"&positionid={self.position_id}"
                            
                            if DEBUG_MODE:
                                print(f"[京东转换器] 调用京推推口令API: {command_api_url}")
                            
                            try:
                                async with session.post(command_api_url, timeout=5) as cmd_response:
                                    cmd_response.raise_for_status()
                                    cmd_result = await cmd_response.json(content_type=None)
                                    if "return" in cmd_result and cmd_result.get("msg", "").startswith("ok"):
                                        jd_command_text_raw = cmd_result["return"].get("jd_short_kl", "")
                                        if jd_command_text_raw:
                                            jd_command_text = f"【口令】{jd_command_text_raw}"
                                        if DEBUG_MODE:
                                            print(f"[京东转换器] 京推推口令转换成功: {jd_command_text_raw}")
                                    else:
                                        if DEBUG_MODE:
                                            print(f"[京东转换器] 京推推口令转换失败: {cmd_result.get('msg', '未知错误')}")
                            except Exception as cmd_e:
                                if DEBUG_MODE:
                                    print(f"[京东转换器] 京推推口令API请求异常: {str(cmd_e)}")
                        else:
                            if DEBUG_MODE:
                                print("[京东转换器] 未获取到有效的short_url或未配置京推推，跳过口令生成。")

                        # 组合两个API的结果
                        return_string = f"【商品】: {jianjie}\n\n"
                        return_string += f"【券后】: {content.get('quanhou_jiage', '未知')}\n"
                        
                        # 根据 show_commission 决定是否显示佣金
                        if show_commission:
                            return_string += f"【佣金】: {content.get('tkfee3', '未知')}\n"
                        
                        return_string += f"【领券买】: {short_url_from_zhetaoke}\n"
                        
                        # 如果口令存在，添加口令行
                        if jd_command_text:
                            command_only = jd_command_text.replace("【口令】", "").strip()
                            return_string += f"【领券口令】: {command_only}\n"
                        
                        if image_cq:  # 如果图片CQ码存在，则添加
                            return_string += f"{image_cq}"
                        
                        if DEBUG_MODE:
                            print(f"[京东转换器] 转换成功")
                        return return_string
                    
                    # 错误处理：第一次JD API调用
                    if "jd_union_open_promotion_byunionid_get_response" in result:
                        jd_response = result["jd_union_open_promotion_byunionid_get_response"]
                        if "result" in jd_response:
                            try:
                                jd_result = json.loads(jd_response["result"])
                                error_message = jd_result.get("message", "未知错误")
                                if jd_result.get("data") and jd_result["data"].get("shortURL"):
                                    short_url = jd_result["data"]["shortURL"]
                                    
                                    # --- 在错误处理分支也调用京推推生成口令 ---
                                    jd_command_text = ""
                                    if short_url and self.jtt_appid and self.jtt_appkey:
                                        command_api_url = f"http://japi.jingtuitui.com/api/get_goods_command?appid={self.jtt_appid}&appkey={self.jtt_appkey}&unionid={self.union_id}&gid={quote(short_url)}"
                                        if self.position_id:
                                            command_api_url += f"&positionid={self.position_id}"
                                        
                                        if DEBUG_MODE:
                                            print(f"[京东转换器] 调用京推推口令API(错误处理分支): {command_api_url}")
                                        
                                        try:
                                            async with session.post(command_api_url, timeout=5) as cmd_response:
                                                cmd_response.raise_for_status()
                                                cmd_result = await cmd_response.json(content_type=None)
                                                if "return" in cmd_result and cmd_result.get("msg", "").startswith("ok"):
                                                    jd_command_text_raw = cmd_result["return"].get("jd_short_kl", "")
                                                    if jd_command_text_raw:
                                                        jd_command_text = f"\n【口令】{jd_command_text_raw}"
                                                    if DEBUG_MODE:
                                                        print(f"[京东转换器] 京推推口令转换成功(错误处理分支): {jd_command_text_raw}")
                                                else:
                                                    if DEBUG_MODE:
                                                        print(f"[京东转换器] 京推推口令转换失败(错误处理分支): {cmd_result.get('msg', '未知错误')}")
                                        except Exception as cmd_e:
                                            if DEBUG_MODE:
                                                print(f"[京东转换器] 京推推口令API请求异常(错误处理分支): {str(cmd_e)}")
                                    
                                    return f"优惠: {short_url}{jd_command_text}"
                                
                                # 错误处理增强：如果是因为优惠券非联盟渠道，则返回原链接
                                if "优惠券" in error_message or "非联盟" in error_message:
                                     if DEBUG_MODE:
                                         print(f"[京东转换器] API返回非联盟优惠券错误，降级为原链接: {error_message}")
                                     return f"优惠券: {material_url}"

                                return f"JD转换失败: {error_message}"
                            except json.JSONDecodeError:
                                return "JD转换失败: 返回数据解析错误"
                    return "JD转换失败: 未知错误"
                    
        except Exception as e:
            if DEBUG_MODE:
                print(f"[京东转换器] 异常: {str(e)}")
            return f"京东转换失败: {str(e)}"
    
    async def _get_command(self, session: aiohttp.ClientSession, short_url: str) -> str:
        """生成京东口令"""
        try:
            url = f"{self.command_url}?appid={self.jtt_appid}&appkey={self.jtt_appkey}&unionid={self.union_id}&gid={quote(short_url)}"
            if self.position_id:
                url += f"&positionid={self.position_id}"
            
            if DEBUG_MODE:
                print(f"[京东转换器] 调用京推推口令API: {url}")
            
            async with session.post(url, timeout=5) as response:
                response.raise_for_status()
                result = await response.json(content_type=None)
                
                if DEBUG_MODE:
                    print(f"[京东转换器] 京推推返回: {result}")
                
                if "return" in result and result.get("msg", "").startswith("ok"):
                    command = result["return"].get("jd_short_kl", "")
                    if DEBUG_MODE:
                        print(f"[京东转换器] 口令生成成功: {command}")
                    return command
                else:
                    if DEBUG_MODE:
                        print(f"[京东转换器] 口令生成失败: {result.get('msg', '未知错误')}")
                    return ""
        except Exception as e:
            if DEBUG_MODE:
                print(f"[京东转换器] 口令生成异常: {str(e)}")
            return ""
