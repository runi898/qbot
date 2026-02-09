#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试颜色输出
验证在有无 colorama 的情况下都能正常工作
"""

import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.colors import (
    green, red, yellow, blue, cyan, magenta,
    SUCCESS, ERROR, WARNING, INFO,
    success, error, warning, info,
    COLORAMA_AVAILABLE
)

print("=" * 60)
print(f"🎨 颜色输出测试")
print(f"Colorama 可用: {COLORAMA_AVAILABLE}")
print("=" * 60)
print()

print("基础颜色测试:")
print(f"  {green('绿色文本')} - 用于成功消息")
print(f"  {red('红色文本')} - 用于错误消息")
print(f"  {yellow('黄色文本')} - 用于警告消息")
print(f"  {blue('蓝色文本')} - 用于信息消息")
print(f"  {cyan('青色文本')} - 用于提示消息")
print(f"  {magenta('洋红色文本')} - 用于特殊消息")
print()

print("符号测试:")
print(f"  {SUCCESS} 成功符号")
print(f"  {ERROR} 错误符号")
print(f"  {WARNING} 警告符号")
print(f"  {INFO} 信息符号")
print()

print("便捷函数测试:")
print(f"  {success('操作成功完成')}")
print(f"  {error('操作失败')}")
print(f"  {warning('这是一个警告')}")
print(f"  {info('这是一条信息')}")
print()

print("实际应用示例:")
print(f"[系统] {SUCCESS} 成功连接到 QQ: {green('3121201314')}")
print(f"[系统] 当前在线机器人: {blue('[435438881, 3121201314]')}")
print(f"[京东转换器] {SUCCESS} 模块已加载 (v{green('1.0.0')})")
print(f"[群管理模块] {ERROR} 撤回失败: 权限不足")
print(f"[返利模块] {WARNING} API 调用超时，正在重试...")
print()

print("=" * 60)
print(f"{SUCCESS} 测试完成！")
print("=" * 60)
