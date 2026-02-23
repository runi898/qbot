"""
ModuleLoader - 模块加载器

负责扫描、加载、管理所有功能模块
"""

import os
import importlib
import importlib.util
import inspect
import json
from pathlib import Path
import asyncio
from typing import List, Optional, Dict, Any

from .base_module import BaseModule, ModuleContext, ModuleResponse
from .event_bus import EventBus


class ModuleLoader:
    """模块加载器"""
    
    def __init__(self, modules_dir: str = "modules"):
        """
        初始化模块加载器
        
        Args:
            modules_dir: 模块目录路径
        """
        self.modules_dir = Path(modules_dir)
        self.modules: List[BaseModule] = []
        self._module_map: Dict[str, BaseModule] = {}
        self.event_bus = EventBus()
        
    async def load_all_modules(self) -> None:
        """加载所有模块"""
        print(f"[ModuleLoader] 开始从 {self.modules_dir} 加载模块...")
        
        if not self.modules_dir.exists():
            print(f"[ModuleLoader] 警告: 模块目录不存在: {self.modules_dir}")
            return
        
        # 遍历模块目录
        for module_dir in self.modules_dir.iterdir():
            if not module_dir.is_dir() or module_dir.name.startswith('_'):
                continue
            
            await self._load_module_from_dir(module_dir)
        
        # 按优先级排序
        self.modules.sort(key=lambda m: m.priority)
        
        # 发布模块加载完成事件
        await self.event_bus.emit('modules_loaded', {
            'count': len(self.modules),
            'modules': [m.name for m in self.modules]
        })
        
        print(f"[ModuleLoader] 成功加载 {len(self.modules)} 个模块")
        for module in self.modules:
            status = "✅" if module.enabled else "❌"
            print(f"  {status} {module.name} (v{module.version}) - 优先级: {module.priority}")
    
    async def load_module_from_path(self, module_path: str, config: dict) -> None:
        """
        从指定路径加载单个模块
        
        Args:
            module_path: 模块路径（相对于当前目录），如 'modules/news_jd'
            config: 模块配置
        """
        module_dir = Path(module_path)
        
        if not module_dir.exists():
            raise FileNotFoundError(f"模块目录不存在: {module_dir}")
        
        await self._load_module_from_dir(module_dir, config)
        
        # 重新排序
        self.modules.sort(key=lambda m: m.priority)
    
    async def _load_module_from_dir(self, module_dir: Path, external_config: dict = None) -> None:
        """
        从目录加载模块
        
        Args:
            module_dir: 模块目录
            external_config: 外部传入的配置（优先级最高）
        """
        module_file = module_dir / "module.py"
        config_file = module_dir / "config.json"
        
        if not module_file.exists():
            print(f"[ModuleLoader] 跳过 {module_dir.name}: 缺少 module.py")
            return
        
        try:
            # 配置优先级：external_config > config.py > config.json
            config = {}
            
            if external_config is not None:
                # 使用外部传入的配置
                config = external_config
                print(f"[ModuleLoader] 使用外部配置加载 {module_dir.name}")
            else:
                # 优先从统一配置文件读取配置
                try:
                    import config as global_config
                    config = global_config.get_module_config(module_dir.name)
                    print(f"[ModuleLoader] 从 config.py 加载 {module_dir.name} 配置")
                except (ImportError, AttributeError):
                    # 如果统一配置不存在，则从 config.json 读取
                    if config_file.exists():
                        with open(config_file, 'r', encoding='utf-8') as f:
                            config = json.load(f)
                        print(f"[ModuleLoader] 从 config.json 加载 {module_dir.name} 配置")
            
            # 动态导入模块
            module_name = f"modules.{module_dir.name}.module"
            spec = importlib.util.spec_from_file_location(module_name, module_file)
            
            if spec is None or spec.loader is None:
                print(f"[ModuleLoader] 无法加载模块: {module_dir.name}")
                return
            
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # 查找 BaseModule 的子类
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, BaseModule) and obj is not BaseModule:
                    # 实例化模块
                    instance = obj()
                    
                    # 检查是否启用
                    if config.get('enabled', True) is False:
                        instance.enabled = False
                    
                    # 设置优先级
                    if 'priority' in config:
                        instance.priority = config['priority']
                    
                    # 调用加载钩子（传递完整配置）
                    await instance.on_load(config)
                    
                    # 注册模块
                    self.modules.append(instance)
                    self._module_map[instance.name] = instance
                    
                    print(f"[ModuleLoader] 加载模块: {instance.name} (v{instance.version})")
                    break
                    
        except Exception as e:
            print(f"[ModuleLoader] 加载模块 {module_dir.name} 失败: {e}")
            import traceback
            traceback.print_exc()
    
    # 独占优先级阈值：小于等于此值的模块（指令/群管理类）在有命中时阻断后续模块
    EXCLUSIVE_PRIORITY_THRESHOLD = 11

    async def process_message(self, message: str, context: ModuleContext) -> Optional[ModuleResponse]:
        """
        处理消息 —— 两阶段全并发调度

        阶段1: 对所有已启用模块并发执行 can_handle 检查（零等待）
        阶段2: 根据命中结果分批执行
            - 独占模块（priority <= EXCLUSIVE_PRIORITY_THRESHOLD，如指令/群管理）：
              按优先级从高到低串行执行，命中即返回，阻断其余模块
            - 普通模块（线报/返利/订阅等）：全部并发 handle，互不等待

        优点：线报收集、返利转链、订阅通知等高延迟 API 调用不再相互阻塞。
        """
        try:
            from config import VERBOSE_LOGGING
            if VERBOSE_LOGGING.get("enabled", False) and VERBOSE_LOGGING.get("message_received", False):
                print(f"[MESSAGE_RECEIVED] 群{context.group_id} | 用户{context.user_id}: {message[:50]}...")
        except Exception:
            pass

        enabled_modules = sorted(
            [m for m in self.modules if m.enabled],
            key=lambda m: m.priority,
        )

        if not enabled_modules:
            return None

        # ── 阶段1：并发 can_handle ──────────────────────────────────────────
        can_handle_tasks = [m.can_handle(message, context) for m in enabled_modules]
        can_handle_results = await asyncio.gather(*can_handle_tasks, return_exceptions=True)

        # 筛选出命中的模块
        exclusive_hits = []   # 独占模块（priority <= threshold）
        concurrent_hits = []  # 普通并发模块

        for module, result in zip(enabled_modules, can_handle_results):
            if isinstance(result, Exception):
                print(f"[ModuleLoader] ⚠ can_handle 异常 [{module.name}]: {result}")
                continue
            if result:
                if module.priority <= self.EXCLUSIVE_PRIORITY_THRESHOLD:
                    exclusive_hits.append(module)
                else:
                    concurrent_hits.append(module)

        # ── 阶段2a：独占模块 —— 按优先级串行，命中即返回 ──────────────────
        for module in exclusive_hits:  # 已按优先级排序
            try:
                try:
                    from config import VERBOSE_LOGGING
                    if VERBOSE_LOGGING.get("enabled", False) and VERBOSE_LOGGING.get("module_handling", False):
                        print(f"[MODULE_HANDLING][独占] 模块 '{module.name}' 正在处理消息")
                except Exception:
                    pass

                await self.event_bus.emit(
                    "before_handle", {"module": module.name, "message": message}, source=module.name
                )
                response = await module.handle(message, context)
                if response:
                    await self.event_bus.emit(
                        "after_handle", {"module": module.name, "response": response.content}, source=module.name
                    )
                    return response
            except Exception as e:
                print(f"[ModuleLoader] ❌ 独占模块 [{module.name}] handle 异常: {e}")
                import traceback
                traceback.print_exc()

        # ── 阶段2b：普通模块 —— 全部并发 handle ────────────────────────────
        if concurrent_hits:
            try:
                from config import VERBOSE_LOGGING
                if VERBOSE_LOGGING.get("enabled", False) and VERBOSE_LOGGING.get("module_handling", False):
                    names = [m.name for m in concurrent_hits]
                    print(f"[MODULE_HANDLING][并发] 并发执行模块: {names}")
            except Exception:
                pass

            handle_tasks = [self._try_handle_module_direct(m, message, context) for m in concurrent_hits]
            handle_results = await asyncio.gather(*handle_tasks, return_exceptions=True)

            # 返回优先级最高的非 None 响应
            paired = [
                (m, r)
                for m, r in zip(concurrent_hits, handle_results)
                if not isinstance(r, Exception) and r is not None
            ]
            if paired:
                # 取优先级最高（priority 数字最小）的响应返回
                best = min(paired, key=lambda x: x[0].priority)
                return best[1]

        return None

    async def _try_handle_module(self, module, message, context):
        """Helper to safely execute can_handle and handle（保留供外部兼容调用）"""
        try:
            if await module.can_handle(message, context):
                try:
                    from config import VERBOSE_LOGGING
                    if VERBOSE_LOGGING.get("enabled", False) and VERBOSE_LOGGING.get("module_handling", False):
                        print(f"[MODULE_HANDLING] 模块 '{module.name}' 正在处理消息")
                except Exception:
                    pass

                await self.event_bus.emit(
                    'before_handle', {'module': module.name, 'message': message}, source=module.name
                )
                response = await module.handle(message, context)
                if response:
                    await self.event_bus.emit(
                        'after_handle', {'module': module.name, 'response': response.content}, source=module.name
                    )
                return response
        except Exception as e:
            print(f"[ModuleLoader] 模块 {module.name} 执行出错: {e}")
            import traceback
            traceback.print_exc()
        return None

    async def _try_handle_module_direct(self, module, message, context):
        """直接执行 handle（已在阶段1通过 can_handle，无需再次检查）"""
        try:
            try:
                from config import VERBOSE_LOGGING
                if VERBOSE_LOGGING.get("enabled", False) and VERBOSE_LOGGING.get("module_handling", False):
                    print(f"[MODULE_HANDLING][并发] 模块 '{module.name}' 正在处理消息")
            except Exception:
                pass

            await self.event_bus.emit(
                'before_handle', {'module': module.name, 'message': message}, source=module.name
            )
            response = await module.handle(message, context)
            if response:
                await self.event_bus.emit(
                    'after_handle', {'module': module.name, 'response': response.content}, source=module.name
                )
            return response
        except Exception as e:
            print(f"[ModuleLoader] ❌ 并发模块 [{module.name}] handle 异常: {e}")
            import traceback
            traceback.print_exc()
        return None

    
    def get_module(self, name: str) -> Optional[BaseModule]:
        """根据名称获取模块"""
        return self._module_map.get(name)
    
    async def enable_module(self, name: str) -> bool:
        """启用模块"""
        module = self.get_module(name)
        if module:
            await module.on_enable()
            return True
        return False
    
    async def disable_module(self, name: str) -> bool:
        """禁用模块"""
        module = self.get_module(name)
        if module:
            await module.on_disable()
            return True
        return False
    
    async def reload_module(self, name: str) -> bool:
        """重新加载指定模块"""
        module = self.get_module(name)
        if not module:
            return False
        
        try:
            # 卸载旧模块
            await module.on_unload()
            
            # 移除旧模块
            self.modules.remove(module)
            del self._module_map[name]
            
            # 重新加载所有模块
            await self.load_all_modules()
            
            return True
        except Exception as e:
            print(f"[ModuleLoader] 重新加载模块 {name} 失败: {e}")
            return False
    
    def get_all_modules_info(self) -> str:
        """获取所有模块的信息"""
        info = "=== 已加载模块列表 ===\n\n"
        for module in self.modules:
            status = "✅ 已启用" if module.enabled else "❌ 已禁用"
            info += f"{status} {module.name} (v{module.version})\n"
            info += f"  描述: {module.description}\n"
            info += f"  作者: {module.author}\n"
            info += f"  优先级: {module.priority}\n\n"
        return info
    
    async def unload_all(self) -> None:
        """卸载所有模块"""
        for module in self.modules:
            try:
                await module.on_unload()
            except Exception as e:
                print(f"[ModuleLoader] 卸载模块 {module.name} 失败: {e}")
        
        self.modules.clear()
        self._module_map.clear()
        print("[ModuleLoader] 所有模块已卸载")
