#!/bin/bash
# QBot Docker 快速启动脚本

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

# 检查 Docker
if ! command -v docker &> /dev/null; then
    print_warn "Docker 未安装，请先安装 Docker"
    exit 1
fi

# 检查配置文件
if [ ! -f "config.py" ]; then
    if [ -f "config.py.example" ]; then
        print_warn "config.py 不存在，从 config.py.example 复制..."
        cp config.py.example config.py
        print_info "✅ 已创建 config.py，请务必编辑填入真实配置！"
        echo "   vi config.py"
        exit 0
    else
        print_warn "config.py 和 config.py.example 都不存在！无法启动。"
        exit 1
    fi
fi

# 检查并初始化数据库文件（防止 Docker 挂载成目录）
if [ ! -f "messages.db" ]; then
    print_info "初始化 messages.db..."
    touch messages.db
    chmod 666 messages.db
fi

if [ ! -f "news.db" ]; then
    print_info "初始化 news.db..."
    touch news.db
    chmod 666 news.db
fi

# 检查并创建目录
mkdir -p logs exports
chmod 777 logs exports

# 选择启动方式
echo "请选择启动方式:"
echo "1) Docker Compose (推荐)"
echo "2) Docker 直接运行"
read -p "请输入选项 (1/2): " choice

case $choice in
    1)
        print_info "使用 Docker Compose 启动..."
        docker compose up -d
        print_info "查看日志: docker compose logs -f"
        ;;
    2)
        print_info "构建 Docker 镜像..."
        docker build -t qbot:latest .
        
        print_info "启动容器..."
        docker run -d \
            --name qbot \
            --restart unless-stopped \
            -p 5670:5670 \
            -v $(pwd)/config.py:/app/config.py:ro \
            -v $(pwd)/messages.db:/app/messages.db \
            -v $(pwd)/exports:/app/exports \
            -v $(pwd)/logs:/app/logs \
            qbot:latest
        
        print_info "查看日志: docker logs -f qbot"
        ;;
    *)
        print_warn "无效选项"
        exit 1
        ;;
esac

print_info "QBot 已启动！"
