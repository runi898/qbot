# QBot - 模块化 QQ 群聊机器人

## 📦 这是什么？

这是 QBot 的**完整部署包**，包含所有必要的文件，可以直接推送到远程仓库并运行。

## 🚀 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置
编辑 `config.py`，填入你的配置：
- 监控群号
- API 密钥（淘宝、京东）
- 其他设置

### 3. 运行
```bash
python main.py
```

## 📁 目录结构

```
start/
├── core/                   # 核心框架
│   ├── __init__.py
│   ├── base_module.py      # 模块基类
│   ├── module_loader.py    # 模块加载器
│   ├── event_bus.py        # 事件总线
│   └── database.py         # 数据库管理
│
├── modules/                # 功能模块
│   ├── commands/           # 指令模块
│   │   ├── module.py
│   │   └── config.json
│   │
│   └── rebate/             # 返利模块
│       ├── module.py
│       ├── taobao.py
│       ├── jingdong.py
│       └── config.json
│
├── utils/                  # 工具函数
├── main.py                 # 主程序
├── config.py               # 配置文件
├── requirements.txt        # 依赖列表
└── README.md               # 本文档
```

## ⚙️ 配置说明

### config.py
```python
# 监控的群聊
WATCHED_GROUP_IDS = [你的群号]

# 淘宝客 API
APP_KEY = "你的APP_KEY"
SID = "你的SID"
PID = "你的PID"

# 京东联盟 API
JD_APPKEY = "你的APPKEY"
JD_UNION_ID = "你的UNION_ID"

# 京东短链转换配置
JD_DWZ_CONFIG = {
    # 如果 Sign 服务运行在 Docker 宿主机上，请使用宿主机局域网 IP
    "sign_url": "http://192.168.8.1:3001/sign",
    
    # 京东 Cookie 配置（可选）
    # 用于短链转换 API 鉴权。如果遇到 "dwz 指令转换失败" 或 API 返回 1007 错误，请填入有效的 pt_key 和 pt_pin。
    "cookie": ""  # 例如: "pt_key=xxx; pt_pin=xxx;"
}
```

### modules/rebate/config.json
```json
{
    "enabled": true,
    "priority": 40,
    "settings": {
        "淘宝API": {
            "app_key": "从 config.py 复制"
        }
    }
}
```

### modules/commands/config.json
```json
{
    "enabled": true,
    "priority": 10,
    "settings": {
        "watched_groups": [你的群号]
    }
}
```

## 📖 支持的功能

### 指令模块
- 撤回消息（单条、批量、全部）（撤回全部， 撤回 N， @某人 撤回， @某人 撤回N）

### 京东线报模块
- 京东线报过滤、去重以后转发指定群
- 去重方法：通过url前面的文字去重，第二层通过图片id双层去重

### 返利模块
- 淘宝链接自动转换
- 京东链接自动转换
- 增加item.jd.taobao的转换，先通过dwz url指令把京东长网址转为短网址，在进行返利转链
- 自动转换 `item.m.jd.com` 开头的长链接为 `3.cn` 短链接，解决返利转链失败问题
- 支持自定义 Sign 服务器和 Cookie 配置，以应对京东 API 升级带来的 1007 错误

### 离线通知模块
- 监控机器人在线状态
- 支持邮件、Webhook、钉钉、Telegram 四种通知渠道
- 自动 QQ 号脱敏
- 上线/下线独立开关
- WebSocket 断线秒级响应

## 🔧 添加新模块

1. 在 `modules/` 下创建新目录
2. 创建 `module.py` 实现模块逻辑
3. 创建 `config.json` 配置文件
4. 重启机器人，自动加载

## 📝 更新日志

### v2.1.0 (2026-02-09)
- ✅ 新增离线通知模块 (支持 Webhook/钉钉/TG/邮件)
- ✅ 优化 config.py 全局配置结构
- ✅ 移除废弃的 COMMANDS 硬编码配置

### v2.0.0 (2026-02-08)
- ✅ 模块化架构重构
- ✅ 核心框架实现
- ✅ 指令模块
- ✅ 返利模块

## 📞 支持

如有问题，请查看项目文档或提交 Issue。

## 📄 许可证

本项目仅供学习和研究使用。
