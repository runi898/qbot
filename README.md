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
- 撤回消息（单条、批量、全部）
- 数据库管理（查询、清理、导出）
- 定时任务

### 返利模块
- 淘宝链接自动转换
- 京东链接自动转换

## 🔧 添加新模块

1. 在 `modules/` 下创建新目录
2. 创建 `module.py` 实现模块逻辑
3. 创建 `config.json` 配置文件
4. 重启机器人，自动加载

## 📝 更新日志

### v2.0.0 (2026-02-08)
- ✅ 模块化架构重构
- ✅ 核心框架实现
- ✅ 指令模块
- ✅ 返利模块

## 📞 支持

如有问题，请查看项目文档或提交 Issue。

## 📄 许可证

本项目仅供学习和研究使用。
