#!/bin/bash
# QBot 跨平台启动脚本 (Linux/Mac)

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 打印带颜色的消息
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查 Python 版本
check_python() {
    if ! command -v python3 &> /dev/null; then
        print_error "Python 3 未安装，请先安装 Python 3.7+"
        exit 1
    fi
    
    python_version=$(python3 --version | cut -d' ' -f2)
    print_info "Python 版本: $python_version"
}

# 检查配置文件
check_config() {
    if [ ! -f "config.py" ]; then
        print_warn "config.py 不存在，从模板复制..."
        if [ -f "config.py.example" ]; then
            cp config.py.example config.py
            print_info "已创建 config.py，请编辑配置文件"
            print_warn "请编辑 config.py 填入你的配置后再运行"
            exit 0
        else
            print_error "config.py.example 不存在"
            exit 1
        fi
    fi
}

# 创建虚拟环境
setup_venv() {
    if [ ! -d "venv" ]; then
        print_info "创建虚拟环境..."
        python3 -m venv venv
    fi
    
    print_info "激活虚拟环境..."
    source venv/bin/activate
}

# 安装依赖
install_deps() {
    print_info "检查并安装依赖..."
    pip install --upgrade pip
    pip install -r requirements.txt
}

# 创建必要的目录
create_dirs() {
    mkdir -p exports logs backups
}

# 主函数
main() {
    print_info "🚀 启动 QBot..."
    
    check_python
    check_config
    setup_venv
    install_deps
    create_dirs
    
    print_info "✅ 启动机器人..."
    python main.py
}

# 捕获 Ctrl+C
trap 'print_warn "收到停止信号，正在关闭..."; exit 0' INT TERM

# 运行主函数
main
