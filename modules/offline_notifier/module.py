"""
离线通知模块 - 监测机器人在线/离线状态并通过外部渠道发送通知
"""

import asyncio
import json
import hmac
import hashlib
import base64
import time
from typing import Optional, Set, Dict
from datetime import datetime
from urllib.parse import quote

import aiohttp

from core.base_module import BaseModule, ModuleContext, ModuleResponse
from config import get_bot_qq_list, DEBUG_MODE, NOTIFICATION_CONFIG
from core import bot_manager


class OfflineNotifierModule(BaseModule):
    """离线通知模块 - 监测机器人状态变化并发送外部通知"""
    
    # ===== 必须实现的属性 =====
    
    @property
    def name(self) -> str:
        return "离线通知模块"
    
    @property
    def version(self) -> str:
        return "1.0.0"
    
    @property
    def description(self) -> str:
        return "监测机器人在线/离线状态,通过外部渠道发送通知"
    
    @property
    def author(self) -> str:
        return "QBot Team"
    
    # ===== 生命周期钩子 =====
    
    async def on_load(self, config: dict) -> None:
        """模块加载时初始化"""
        await super().on_load(config)
        
        # 保存配置
        self.config = config
        settings = config.get('settings', {})
        
        # 获取机器人列表
        self.bot_qq_list = get_bot_qq_list()
        
        # 监测的机器人列表(留空则监测所有)
        # 监测的机器人列表(留空则监测所有)
        self.monitored_bots = settings.get('monitored_bots', [])
        # 注意: 如果留空，self.monitored_bots 为 [], 后续逻辑会将其视为"动态监测所有连接的机器人"
        
        # 检测间隔
        self.check_interval = settings.get('check_interval', 30)
        
        # 通知开关
        self.notify_offline = settings.get('notify_offline', True)
        self.notify_online = settings.get('notify_online', True)
        
        # 通知模板
        self.templates = settings.get('templates', {
            'offline': '⚠️ QBot 告警\n机器人 {bot_qq} 已离线\n时间: {time}',
            'online': '✅ QBot 通知\n机器人 {bot_qq} 已上线\n时间: {time}'
        })
        
        # 加载全局通知配置
        self.notification_config = NOTIFICATION_CONFIG
        
        # 状态缓存(记录上一次的在线状态)
        self.last_online_bots: Set[int] = set()
        
        # 启动后台监测任务
        self.monitor_task = asyncio.create_task(self._check_status_loop())
        
        print(f"[{self.name}] 模块已加载 (v{self.version})")
        print(f"[{self.name}] 监测机器人: {self.monitored_bots}")
        print(f"[{self.name}] 检测间隔: {self.check_interval}秒")
    
    async def on_unload(self) -> None:
        """模块卸载时停止后台任务"""
        if hasattr(self, 'monitor_task'):
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        await super().on_unload()
    
    # ===== 必须实现的方法 =====
    
    async def can_handle(self, message: str, context: ModuleContext) -> bool:
        """不处理用户消息,纯后台任务"""
        return False
    
    async def handle(self, message: str, context: ModuleContext) -> Optional[ModuleResponse]:
        """不处理用户消息"""
        return None
    
    # ===== 核心功能实现 =====
    
    async def send_offline_notification(self, bot_qq: int):
        """
        发送离线通知(供外部调用)
        
        Args:
           bot_qq: 机器人QQ号 
        """
        if self.notify_offline:
            print(f"[{self.name}] 接收到外部离线事件: {bot_qq}")
            await self._send_notification('offline', bot_qq)

    def _mask_qq(self, qq: int) -> str:
        """
        QQ号脱敏处理,只显示开头2位和结尾2位,中间用*替代
        
        Args:
            qq: QQ号
            
        Returns:
            脱敏后的QQ号字符串
        """
        qq_str = str(qq)
        if len(qq_str) <= 4:
            return qq_str  # 如果QQ号太短,直接返回
        
        # 开头2位 + 中间* + 结尾2位
        masked = qq_str[:2] + '*' * (len(qq_str) - 4) + qq_str[-2:]
        return masked
    
    async def _check_status_loop(self):
        """后台循环检查机器人状态"""
        # ── 等待所有 QQ 完成初始连接 ───────────────────────────────────────
        # 策略：等待 init_wait 秒后做第一次快照（不发通知），
        #       之后每隔 check_interval 秒才正式检测上线/离线变化。
        # 这样即使多个 QQ 连接建立有先后，也不会漏掉任何一个。
        init_wait = max(self.check_interval, 45)  # 至少等 45 秒
        print(f"[{self.name}] 等待 {init_wait} 秒，让所有机器人完成初始连接...")
        await asyncio.sleep(init_wait)

        # 初始化快照（此时所有 QQ 应该都已注册到 bot_manager）
        self.last_online_bots = set(bot_manager.get_online_bots())
        print(f"[{self.name}] 基线快照完成，当前在线机器人: {sorted(self.last_online_bots)}")

        # 发送启动通知（已上线的 QQ）
        if self.notify_online:
            for bot_qq in sorted(self.last_online_bots):
                await asyncio.sleep(0.5)
                await self._send_notification('online', bot_qq)

        # ── 主循环 ─────────────────────────────────────────────────────────
        while True:
            try:
                await asyncio.sleep(self.check_interval)

                # 获取当前在线机器人
                current_online_bots = set(bot_manager.get_online_bots())

                if DEBUG_MODE:
                    print(f"[{self.name}] 状态轮询 | 当前在线: {sorted(current_online_bots)} | 上次快照: {sorted(self.last_online_bots)}")

                # 如果 monitored_bots 为空，则监测所有
                if not self.monitored_bots:
                    monitored_current = current_online_bots
                    monitored_last = self.last_online_bots
                else:
                    monitored_current = {b for b in current_online_bots if b in self.monitored_bots}
                    monitored_last = {b for b in self.last_online_bots if b in self.monitored_bots}

                # 检测离线（快照有但当前没有 → 离线）
                offline_bots = monitored_last - monitored_current
                for bot_qq in offline_bots:
                    print(f"[{self.name}] ⚠️ 检测到机器人 {bot_qq} 离线")
                    if self.notify_offline:
                        await self._send_notification('offline', bot_qq)

                # 检测上线（当前有但快照没有 → 新上线）
                online_bots = monitored_current - monitored_last
                for bot_qq in online_bots:
                    print(f"[{self.name}] ✅ 检测到机器人 {bot_qq} 上线")
                    if self.notify_online:
                        await self._send_notification('online', bot_qq)

                # 更新快照
                self.last_online_bots = current_online_bots.copy()

                if not DEBUG_MODE:
                    # 非调试模式也定期打印，便于确认所有QQ都在监测中
                    print(f"[{self.name}] 当前在线机器人({len(current_online_bots)}个): {sorted(current_online_bots)}")

            except asyncio.CancelledError:
                print(f"[{self.name}] 监测任务已停止")
                break
            except Exception as e:
                print(f"[{self.name}] ❌ 状态检查异常: {e}")
                if DEBUG_MODE:
                    import traceback
                    traceback.print_exc()

    
    async def _send_notification(self, event_type: str, bot_qq: int):
        """
        发送通知到所有启用的渠道
        
        Args:
            event_type: 事件类型 ('offline' 或 'online')
            bot_qq: 机器人QQ号
        """
        # 生成通知内容(使用脱敏的QQ号)
        masked_qq = self._mask_qq(bot_qq)
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        template = self.templates.get(event_type, '')
        message = template.format(bot_qq=masked_qq, time=current_time)
        
        # 发送到各个渠道
        tasks = []
        
        if self.notification_config.get('email', {}).get('enabled'):
            tasks.append(self._send_email(message, event_type))
        
        if self.notification_config.get('webhook', {}).get('enabled'):
            tasks.append(self._send_webhook(message, event_type, bot_qq))
        
        if self.notification_config.get('dingtalk', {}).get('enabled'):
            tasks.append(self._send_dingtalk(message, event_type))
        
        if self.notification_config.get('telegram', {}).get('enabled'):
            tasks.append(self._send_telegram(message, event_type))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _send_email(self, message: str, event_type: str):
        """发送邮件通知"""
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            
            email_config = self.notification_config['email']
            
            # 创建邮件
            msg = MIMEMultipart()
            msg['From'] = email_config['from_addr']
            msg['To'] = ', '.join(email_config['to_addrs'])
            msg['Subject'] = f"QBot {'离线' if event_type == 'offline' else '上线'}通知"
            
            msg.attach(MIMEText(message, 'plain', 'utf-8'))
            
            # 发送邮件
            with smtplib.SMTP_SSL(email_config['smtp_host'], email_config['smtp_port']) as server:
                server.login(email_config['smtp_user'], email_config['smtp_password'])
                server.send_message(msg)
            
            print(f"[{self.name}] ✅ 邮件通知发送成功")
            
        except Exception as e:
            print(f"[{self.name}] ❌ 邮件通知发送失败: {e}")
    
    async def _send_webhook(self, message: str, event_type: str, bot_qq: int):
        """发送Webhook通知"""
        try:
            webhook_config = self.notification_config['webhook']
            
            # 构造请求数据
            data = {
                'event': event_type,
                'bot_qq': bot_qq,
                'message': message,
                'timestamp': int(time.time())
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method=webhook_config.get('method', 'POST'),
                    url=webhook_config['url'],
                    headers=webhook_config.get('headers', {}),
                    json=data,
                    timeout=10
                ) as response:
                    if response.status == 200:
                        print(f"[{self.name}] ✅ Webhook通知发送成功")
                    else:
                        print(f"[{self.name}] ❌ Webhook通知失败: HTTP {response.status}")
                        
        except Exception as e:
            print(f"[{self.name}] ❌ Webhook通知发送失败: {e}")
    
    async def _send_dingtalk(self, message: str, event_type: str):
        """发送钉钉机器人通知"""
        try:
            dingtalk_config = self.notification_config['dingtalk']
            webhook_url = dingtalk_config['webhook_url']
            secret = dingtalk_config.get('secret', '')
            
            # 如果配置了加签,计算签名
            if secret:
                timestamp = str(round(time.time() * 1000))
                secret_enc = secret.encode('utf-8')
                string_to_sign = f'{timestamp}\n{secret}'
                string_to_sign_enc = string_to_sign.encode('utf-8')
                hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
                sign = quote(base64.b64encode(hmac_code))
                webhook_url = f"{webhook_url}&timestamp={timestamp}&sign={sign}"
            
            # 构造消息
            data = {
                'msgtype': 'text',
                'text': {
                    'content': message
                }
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=data, timeout=10) as response:
                    result = await response.json()
                    if result.get('errcode') == 0:
                        print(f"[{self.name}] ✅ 钉钉通知发送成功")
                    else:
                        print(f"[{self.name}] ❌ 钉钉通知失败: {result.get('errmsg')}")
                        
        except Exception as e:
            print(f"[{self.name}] ❌ 钉钉通知发送失败: {e}")
    
    async def _send_telegram(self, message: str, event_type: str):
        """发送Telegram通知"""
        try:
            telegram_config = self.notification_config['telegram']
            bot_token = telegram_config['bot_token']
            chat_id = telegram_config['chat_id']
            
            # 构造API URL
            api_base = telegram_config.get('api_base_url', 'https://api.telegram.org')
            api_url = f"{api_base}/bot{bot_token}/sendMessage"
            
            # 构造请求数据
            data = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            
            # 配置代理
            proxy_config = telegram_config.get('proxy', {})
            connector = None
            
            if proxy_config.get('enabled'):
                from aiohttp_socks import ProxyConnector
                proxy_url = proxy_config['proxy_url']
                username = proxy_config.get('username')
                password = proxy_config.get('password')
                
                if username and password:
                    # 带认证的代理
                    connector = ProxyConnector.from_url(proxy_url, username=username, password=password)
                else:
                    connector = ProxyConnector.from_url(proxy_url)
            
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(api_url, json=data, timeout=10) as response:
                    result = await response.json()
                    if result.get('ok'):
                        print(f"[{self.name}] ✅ Telegram通知发送成功")
                    else:
                        print(f"[{self.name}] ❌ Telegram通知失败: {result.get('description')}")
                        
        except Exception as e:
            print(f"[{self.name}] ❌ Telegram通知发送失败: {e}")
            if DEBUG_MODE:
                import traceback
                traceback.print_exc()
