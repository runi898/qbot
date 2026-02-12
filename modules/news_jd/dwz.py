#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
京东短链转换工具 - 纯 Python 版本
直接调用 Sign API 和京东 API 实现短链转换
"""

import requests
import json
import sys
import os
from typing import Dict, Any, Optional

# 尝试从上级目录加载配置
DEFAULT_SIGN_URL = None
try:
    # 将项目根目录添加到 sys.path
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    # 尝试多种路径策略
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..')) # start dir
    
    from config import JD_SIGN_URL
    DEFAULT_SIGN_URL = JD_SIGN_URL
except ImportError:
    pass


try:
    from config import JD_COOKIE
except ImportError:
    JD_COOKIE = ""

class JDShortUrlConverter:
    """京东短链转换器"""
    
    def __init__(self, sign_url: str = None):
        """
        初始化转换器
        
        Args:
            sign_url: Sign 服务器完整地址
        """
        self.sign_url = sign_url if sign_url else DEFAULT_SIGN_URL
        if not self.sign_url:
             raise ValueError("必须提供 sign_url，或在 config.py 中配置 JD_SIGN_URL")
             
        self.jd_api_url = "https://api.m.jd.com/client.action"
        self.headers = {
            'User-Agent': 'jdapp;android;13.6.3',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Cookie': JD_COOKIE
        }
    
    def call_sign_api(self, function_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        调用 Sign 接口获取签名
        
        Args:
            function_id: 功能 ID
            body: 请求体
            
        Returns:
            Sign 接口响应
        """
        payload = {
            "functionId": function_id,
            "body": json.dumps(body, ensure_ascii=False)
        }
        
        try:
            response = requests.post(
                self.sign_url,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise Exception(f"Sign 接口调用失败: {str(e)}")
    
    def call_jd_api(self, query_string: str) -> Dict[str, Any]:
        """
        调用京东 API
        
        Args:
            query_string: 签名后的查询字符串
            
        Returns:
            京东 API 响应
        """
        url = f"{self.jd_api_url}?{query_string}"
        
        try:
            response = requests.post(
                url,
                headers=self.headers,
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise Exception(f"京东 API 调用失败: {str(e)}")
    
    def convert(self, url: str, verbose: bool = True) -> Dict[str, Any]:
        """
        转换长链接为短链接
        
        Args:
            url: 京东商品长链接
            verbose: 是否打印详细信息
            
        Returns:
            转换结果字典:
            {
                'success': bool,
                'short_url': str,
                'text': str,
                'code': str,
                'raw_response': dict
            }
        """
        if verbose:
            print(f"🚀 京东短链转换器")
            print(f"目标链接: {url}")
            print(f"Sign 服务器: {self.sign_url}\n")
        
        try:
            # 步骤 1: 调用 Sign 接口
            if verbose:
                print("📡 正在请求 Sign 接口...")
            
            sign_result = self.call_sign_api('shortUrl', {
                'originUrl': url
            })
            
            if verbose:
                print("✅ Sign 接口响应成功")
                print(json.dumps(sign_result, indent=2, ensure_ascii=False))
            
            # 检查 Sign 接口响应
            if sign_result.get('code') != 200:
                return {
                    'success': False,
                    'error': 'Sign 接口返回错误',
                    'raw_response': str(sign_result)[:500]  # 防止过长
                }
            
            # 步骤 2: 调用京东 API
            if verbose:
                print("\n📡 正在调用京东短链 API...")
            
            query_string = sign_result['body']['qs']
            jd_result = self.call_jd_api(query_string)
            
            if verbose:
                print("✅ 京东 API 响应成功")
                print(json.dumps(jd_result, indent=2, ensure_ascii=False))
            
            # 提取短链接
            short_url = jd_result.get('shortUrl')
            text = jd_result.get('text', '')
            code = jd_result.get('code', '')
            
            if short_url:
                if verbose:
                    print(f"\n🎉 短链接: {short_url}")
                
                return {
                    'success': True,
                    'short_url': short_url,
                    'text': text,
                    'code': code,
                    'raw_response': jd_result
                }
            else:
                if verbose:
                    print(f"[dwz.py] ❌ 未找到短链接字段。完整响应: {json.dumps(jd_result, ensure_ascii=False)[:500]}...")
                return {
                    'success': False,
                    'error': f'未找到短链接字段。响应码: {code}, 提示: {str(text)[:100]}',
                    'raw_response': str(jd_result)[:500]
                }
                
        except Exception as e:
            if verbose:
                print(f"\n❌ 错误: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def convert_batch(self, urls: list, verbose: bool = False) -> list:
        """
        批量转换链接
        
        Args:
            urls: 链接列表
            verbose: 是否打印详细信息
            
        Returns:
            结果列表
        """
        results = []
        total = len(urls)
        
        for i, url in enumerate(urls, 1):
            print(f"\n[{i}/{total}] 转换: {url}")
            result = self.convert(url, verbose=verbose)
            results.append({
                'url': url,
                **result
            })
        
        return results


def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description='京东短链转换工具')
    parser.add_argument('url', nargs='?', help='京东商品链接')
    
    default_help = f'Sign 服务器地址 (默认: {DEFAULT_SIGN_URL})' if DEFAULT_SIGN_URL else 'Sign 服务器地址 (必填)'
    parser.add_argument('-s', '--sign-url', default=DEFAULT_SIGN_URL, help=default_help)
    
    parser.add_argument('-q', '--quiet', action='store_true',
                       help='静默模式，只输出短链接')
    parser.add_argument('-f', '--file', help='从文件读取链接列表（每行一个）')
    
    args = parser.parse_args()
    
    # 检查是否提供了链接或文件
    if not args.url and not args.file:
        parser.print_help()
        print("\n示例:")
        print("  python dwz.py https://item.m.jd.com/product/10144010479875.html")
        print("  python dwz.py -f urls.txt")
        print("  python dwz.py -q https://item.m.jd.com/product/10144010479875.html")
        sys.exit(1)
    
    converter = JDShortUrlConverter(sign_url=args.sign_url)
    
    # 批量处理
    if args.file:
        try:
            with open(args.file, 'r', encoding='utf-8') as f:
                urls = [line.strip() for line in f if line.strip()]
            
            results = converter.convert_batch(urls, verbose=not args.quiet)
            
            # 输出汇总
            print("\n" + "="*50)
            print("转换结果汇总:")
            print("="*50)
            for result in results:
                status = "✅" if result['success'] else "❌"
                short = result.get('short_url', '失败')
                print(f"{status} {result['url']}")
                print(f"   → {short}\n")
                
        except FileNotFoundError:
            print(f"❌ 文件不存在: {args.file}")
            sys.exit(1)
    
    # 单个链接处理
    else:
        result = converter.convert(args.url, verbose=not args.quiet)
        
        if args.quiet:
            # 静默模式只输出短链接
            if result['success']:
                print(result['short_url'])
            else:
                sys.exit(1)
        else:
            # 详细模式已经在 convert 方法中打印了
            if not result['success']:
                sys.exit(1)


if __name__ == "__main__":
    main()
