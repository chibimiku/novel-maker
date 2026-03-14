import sys
import os
import logging
from PyQt6.QtWidgets import QApplication

# 1. 确保项目根目录在 Python 的系统路径中，防止报 "ModuleNotFoundError"
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 导入我们的主窗口类
from ui.main_window import NovelCreatorWindow
from ui.theme import get_dark_stylesheet

def setup_global_logging():
    """初始化全局日志配置，将 log 输出到控制台和 /log/app.log 文件"""
    log_dir = os.path.join(project_root, "log")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "app.log")
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logging.info("=========================================")
    logging.info("AI小说创作器启动")
    logging.info("=========================================")

def main():
    # 2. 初始化日志
    setup_global_logging()
    
    # 3. 初始化 Qt 应用程序实例
    app = QApplication(sys.argv)
    
    # 可选：设置应用程序的全局属性
    app.setApplicationName("AI Novel Creator")
    app.setApplicationVersion("1.0.0")
    
    # 应用暗色主题
    app.setStyle("Fusion")  # Fusion 风格是暗色主题的最佳基础
    app.setStyleSheet(get_dark_stylesheet())
    
    # 4. 实例化并显示主窗口
    try:
        window = NovelCreatorWindow()
        window.show()
        
        # 5. 进入主事件循环
        sys.exit(app.exec())
    except Exception as e:
        logging.critical(f"程序运行发生致命错误: {e}", exc_info=True)
        sys.exit(1)

if __name__ == '__main__':
    main()