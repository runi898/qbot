"""
控制台颜色输出工具
支持跨平台的彩色终端输出
即使没有 colorama 也能在 Linux/Docker 中正常显示颜色
"""

try:
    from colorama import init, Fore, Style
    # 初始化 colorama（Windows 需要）
    init(autoreset=True)
    COLORAMA_AVAILABLE = True
except ImportError:
    # colorama 未安装，使用 ANSI 颜色代码（Linux/macOS/Docker 原生支持）
    COLORAMA_AVAILABLE = False
    
    class Fore:
        GREEN = '\033[92m'
        RED = '\033[91m'
        YELLOW = '\033[93m'
        BLUE = '\033[94m'
        CYAN = '\033[96m'
        MAGENTA = '\033[95m'
        RESET = '\033[0m'
    
    class Style:
        BRIGHT = '\033[1m'
        RESET_ALL = '\033[0m'


def green(text: str) -> str:
    """绿色文本（成功）"""
    return f"{Fore.GREEN}{text}{Style.RESET_ALL if COLORAMA_AVAILABLE else Fore.RESET}"


def red(text: str) -> str:
    """红色文本（错误/失败）"""
    return f"{Fore.RED}{text}{Style.RESET_ALL if COLORAMA_AVAILABLE else Fore.RESET}"


def yellow(text: str) -> str:
    """黄色文本（警告）"""
    return f"{Fore.YELLOW}{text}{Style.RESET_ALL if COLORAMA_AVAILABLE else Fore.RESET}"


def blue(text: str) -> str:
    """蓝色文本（信息）"""
    return f"{Fore.BLUE}{text}{Style.RESET_ALL if COLORAMA_AVAILABLE else Fore.RESET}"


def cyan(text: str) -> str:
    """青色文本（提示）"""
    return f"{Fore.CYAN}{text}{Style.RESET_ALL if COLORAMA_AVAILABLE else Fore.RESET}"


def magenta(text: str) -> str:
    """洋红色文本"""
    return f"{Fore.MAGENTA}{text}{Style.RESET_ALL if COLORAMA_AVAILABLE else Fore.RESET}"


def bold(text: str) -> str:
    """粗体文本"""
    return f"{Style.BRIGHT}{text}{Style.RESET_ALL if COLORAMA_AVAILABLE else Fore.RESET}"


# 常用符号
SUCCESS = green("✅")
ERROR = red("❌")
WARNING = yellow("⚠️")
INFO = blue("ℹ️")
ARROW = cyan("→")
BULLET = "•"


# 便捷函数
def success(message: str) -> str:
    """成功消息"""
    return f"{SUCCESS} {message}"


def error(message: str) -> str:
    """错误消息"""
    return f"{ERROR} {message}"


def warning(message: str) -> str:
    """警告消息"""
    return f"{WARNING} {message}"


def info(message: str) -> str:
    """信息消息"""
    return f"{INFO} {message}"
