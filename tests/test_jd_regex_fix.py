#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试京东正则表达式修复
验证是否正确排除 CQ 码
"""

import re

# 修复后的正则
jingdong_regex = re.compile(
    r'https?:\/\/[^\s<>\[\"]*(?:3\.cn|jd\.|jingxi)[^\s<>\[\"]+|'
    r'(?:￥|！|\$)[0-9A-Za-z()]+(?:￥|！|\$)\s+(?:MF|CA)[0-9]+|'
    r'[^一-龥0-9a-zA-Z=;&?-_.<>:\'\",{}][0-9a-zA-Z()]{16}[^一-龥0-9a-zA-Z=;&?-_.<>:\'\",{}\s]'
)

# 测试用例
test_cases = [
    {
        "name": "正常京东短链接",
        "text": "https://u.jd.com/lOItP06",
        "expected": ["https://u.jd.com/lOItP06"]
    },
    {
        "name": "京东短链接后跟CQ图片码",
        "text": "https://u.jd.com/lOItP06[CQ:image,file=https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=xxx]",
        "expected": ["https://u.jd.com/lOItP06"]
    },
    {
        "name": "优惠券链接",
        "text": "https://coupon.m.jd.com/coupons/show.action?key=c7mdc3s7odab41f999584290f52d6caf&roleId=2126712105",
        "expected": ["https://coupon.m.jd.com/coupons/show.action?key=c7mdc3s7odab41f999584290f52d6caf&roleId=2126712105"]
    },
    {
        "name": "3.cn短链接",
        "text": "https://3.cn/2D-YdUAS",
        "expected": ["https://3.cn/2D-YdUAS"]
    },
    {
        "name": "item.m.jd.com长链接",
        "text": "https://item.m.jd.com/product/10144010479875.html",
        "expected": ["https://item.m.jd.com/product/10144010479875.html"]
    },
    {
        "name": "京东口令",
        "text": "￥FDIMWEeqJYrCTRfn￥ CZ154",
        "expected": ["￥FDIMWEeqJYrCTRfn￥ CZ154"]
    },
    {
        "name": "实际日志中的问题消息",
        "text": "https://u.jd.com/lOItP06[CQ:image,file=https://multimedia.nt.qq.com.cn/download?appid=1407&amp;fileid=EhQpOt94O0OxWW0UamFBWAAo1_pZFBih_xYg_woo8dTFwYTMkgMyBHByb2RQgL2jAVoQoGQumJbuh6J-KEA5lcGpe3oC1yuCAQJuag&amp;rkey=CAISONPsN0nSR8aLUuBJ6kJMbw1O445-xzMGkw2HpD0NRCHWqbYHd1SwjXeKQGL_BkEsxL43fqt-Krub]",
        "expected": ["https://u.jd.com/lOItP06"]
    },
    {
        "name": "JSON卡片中的京东链接（实际应匹配纯文本部分）",
        "text": '[CQ:json,data={"jumpUrl":"https://item.m.jd.com/product/100104625124.html?utm_user=plusmember"}][分享]【百亿补贴】京觅富硒鸡蛋3斤\nhttps://item.m.jd.com/product/100104625124.html?utm_user=plusmember',
        "expected": ["https://item.m.jd.com/product/100104625124.html?utm_user=plusmember"]  # 只匹配纯文本中的链接
    }
]

print("🧪 京东正则表达式测试\n")
print("=" * 80)

all_passed = True
for i, test in enumerate(test_cases, 1):
    matches = jingdong_regex.findall(test["text"])
    passed = matches == test["expected"]
    all_passed = all_passed and passed
    
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"\n测试 {i}: {test['name']}")
    print(f"状态: {status}")
    print(f"输入: {test['text'][:100]}{'...' if len(test['text']) > 100 else ''}")
    print(f"期望: {test['expected']}")
    print(f"实际: {matches}")
    
    if not passed:
        print(f"⚠️  不匹配！")

print("\n" + "=" * 80)
if all_passed:
    print("✅ 所有测试通过！")
else:
    print("❌ 部分测试失败，请检查正则表达式")
