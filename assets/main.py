import json
import collections
import asyncio
import io
import textwrap
import tempfile
import os
import subprocess
import sys
import math
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

from aiohttp import web
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.star_handler import star_handlers_registry, StarHandlerMetadata


class DependencyInstaller:
    """依赖安装器"""
    
    @staticmethod
    async def install_html_dependencies():
        """安装HTML渲染相关依赖"""
        required_packages = ["playwright"]
        missing_packages = []
        
        for package in required_packages:
            try:
                __import__(package)
                logger.info(f"✅ {package} 已安装")
            except ImportError:
                missing_packages.append(package)
                logger.warning(f"❌ {package} 未安装")
        
        if missing_packages:
            logger.info(f"开始安装缺失的依赖: {missing_packages}")
            try:
                # 使用uv pip安装（如果可用），否则使用pip
                import shutil
                if shutil.which("uv"):
                    install_cmd = [sys.executable, "-m", "uv", "pip", "install"]
                else:
                    install_cmd = [sys.executable, "-m", "pip", "install"]
                
                install_cmd.extend(missing_packages)
                
                logger.info(f"执行安装命令: {' '.join(install_cmd)}")
                
                process = await asyncio.create_subprocess_exec(
                    *install_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                stdout, stderr = await process.communicate()
                
                if process.returncode == 0:
                    logger.info("✅ 所有依赖安装成功")
                    
                    # 安装playwright浏览器 - 使用更稳定的方法
                    try:
                        # 方法1: 使用 playwright install 命令
                        playwright_install = await asyncio.create_subprocess_exec(
                            sys.executable, "-m", "playwright", "install", "chromium",
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        stdout, stderr = await playwright_install.communicate()
                        
                        if playwright_install.returncode == 0:
                            logger.info("✅ Playwright浏览器安装成功")
                            return True
                        else:
                            # 方法2: 如果方法1失败，尝试使用 playwright install 不带参数
                            logger.warning("方法1安装浏览器失败，尝试方法2...")
                            playwright_install2 = await asyncio.create_subprocess_exec(
                                sys.executable, "-m", "playwright", "install",
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE
                            )
                            stdout2, stderr2 = await playwright_install2.communicate()
                            
                            if playwright_install2.returncode == 0:
                                logger.info("✅ Playwright浏览器安装成功（方法2）")
                                return True
                            else:
                                logger.error(f"❌ Playwright浏览器安装失败: {stderr2.decode()}")
                                return False
                                
                    except Exception as e:
                        logger.error(f"❌ Playwright浏览器安装异常: {e}")
                        return False
                else:
                    error_msg = stderr.decode() if stderr else stdout.decode()
                    logger.error(f"❌ 安装失败: {error_msg}")
                    return False
                    
            except Exception as e:
                logger.error(f"❌ 安装依赖时出错: {e}")
                return False
        
        return True


class HTMLRenderer:
    """HTML渲染器"""
    
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.initialized = False
    
    async def initialize(self):
        """初始化Playwright"""
        try:
            import playwright.async_api
            
            # 检查浏览器是否已安装
            try:
                from playwright.async_api import async_playwright
                self.playwright = await async_playwright().start()
                
                # 尝试连接已安装的浏览器
                self.browser = await self.playwright.chromium.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-dev-shm-usage']
                )
                
                self.initialized = True
                logger.info("✅ HTML渲染器初始化成功")
                return True
                
            except Exception as e:
                logger.error(f"❌ HTML渲染器初始化失败: {e}")
                # 尝试重新安装浏览器
                logger.info("🔄 尝试重新安装浏览器...")
                try:
                    # 安装浏览器
                    install_process = await asyncio.create_subprocess_exec(
                        sys.executable, "-m", "playwright", "install", "chromium",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    await install_process.communicate()
                    
                    if install_process.returncode == 0:
                        # 重新尝试初始化
                        self.playwright = await async_playwright().start()
                        self.browser = await self.playwright.chromium.launch(
                            headless=True,
                            args=['--no-sandbox', '--disable-dev-shm-usage']
                        )
                        self.initialized = True
                        logger.info("✅ HTML渲染器重新初始化成功")
                        return True
                    else:
                        logger.error("❌ 浏览器重新安装失败")
                        return False
                        
                except Exception as reinstall_error:
                    logger.error(f"❌ 浏览器重新安装异常: {reinstall_error}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ HTML渲染器初始化失败: {e}")
            return False
    
    async def render_html_to_image(self, html_content: str, width: int = 800, height: int = 1200) -> Optional[str]:
        """将HTML内容渲染为图片"""
        if not self.initialized:
            if not await self.initialize():
                return None
        
        try:
            # 创建浏览器上下文和页面
            context = await self.browser.new_context(viewport={'width': width, 'height': height})
            page = await context.new_page()
            
            # 设置HTML内容
            await page.set_content(html_content, wait_until='networkidle')
            
            # 等待页面完全加载
            await page.wait_for_timeout(1000)
            
            # 创建临时文件
            temp_dir = Path("data/plugins/command_manager/temp")
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            temp_file = tempfile.NamedTemporaryFile(
                suffix=".png", 
                prefix="help_", 
                dir=str(temp_dir),
                delete=False
            )
            image_path = temp_file.name
            temp_file.close()
            
            # 截图
            await page.screenshot(path=image_path, full_page=True)
            
            # 清理资源
            await context.close()
            
            logger.info(f"✅ HTML渲染成功: {image_path}")
            return image_path
            
        except Exception as e:
            logger.error(f"❌ HTML渲染失败: {e}")
            return None
    
    async def close(self):
        """关闭浏览器"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()


class ConfigManager:
    def __init__(self):
        self.data_dir = Path("data/plugins/command_manager")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.commands_file = self.data_dir / "custom_commands.json"
        self.custom_commands = self.load_custom_commands()
        logger.debug("ConfigManager初始化完成")

    def load_custom_commands(self) -> Dict[str, Any]:
        """加载自定义命令配置"""
        try:
            if self.commands_file.exists():
                with open(self.commands_file, 'r', encoding='utf-8') as f:
                    logger.debug(f"从 {self.commands_file} 加载配置")
                    data = json.load(f)
                    logger.debug(f"加载的配置内容: {data}")
                    return data
            else:
                logger.debug("配置文件不存在，使用默认配置")
        except Exception as e:
            logger.error(f"加载自定义命令配置失败: {e}")
            logger.exception(e)
        
        logger.debug("使用默认配置")
        return {
            "categories": [],
            "enabled": True
        }

    def save_custom_commands(self):
        """保存自定义命令配置"""
        try:
            with open(self.commands_file, 'w', encoding='utf-8') as f:
                json.dump(self.custom_commands, f, ensure_ascii=False, indent=2)
            logger.debug(f"配置已保存到 {self.commands_file}")
            logger.debug(f"保存的配置内容: {self.custom_commands}")
            return True
        except Exception as e:
            logger.error(f"保存自定义命令配置失败: {e}")
            logger.exception(e)
            return False

    def is_enabled(self) -> bool:
        """检查帮助系统是否启用"""
        return self.custom_commands.get('enabled', True)

    def set_enabled(self, enabled: bool):
        """设置帮助系统启用状态"""
        self.custom_commands['enabled'] = enabled
        self.save_custom_commands()
        logger.debug(f"帮助系统已{'启用' if enabled else '禁用'}")

    def get_categories(self) -> List[Dict]:
        """获取所有分类"""
        return self.custom_commands.get('categories', [])

    def set_categories(self, categories: List[Dict]):
        """设置分类"""
        logger.debug(f"开始设置分类，数量: {len(categories)}")
        try:
            self.custom_commands['categories'] = categories
            success = self.save_custom_commands()
            logger.debug(f"设置分类完成，保存结果: {success}")
            return success
        except Exception as e:
            logger.error(f"设置分类时发生错误: {e}")
            logger.exception(e)
            return False

    def reload_config(self):
        """重新加载配置"""
        self.custom_commands = self.load_custom_commands()
        logger.debug("配置已重新加载")


class CommandParser:
    def __init__(self, context: Context):
        self.context = context
        logger.debug("CommandParser初始化完成")

    def get_all_commands(self) -> Dict[str, List[str]]:
        """获取所有其他插件及其命令列表"""
        plugin_commands: Dict[str, List[str]] = collections.defaultdict(list)
        
        try:
            all_stars_metadata = self.context.get_all_stars()
            all_stars_metadata = [star for star in all_stars_metadata if star.activated]
            logger.debug(f"发现 {len(all_stars_metadata)} 个激活的插件")
        except Exception as e:
            logger.error(f"获取插件列表失败: {e}")
            return {}
            
        if not all_stars_metadata:
            logger.warning("没有找到任何插件")
            return {}
            
        total_commands = 0
        for star in all_stars_metadata:
            plugin_name = getattr(star, "name", "未知插件")
            module_path = getattr(star, "module_path", None)
            
            # 跳过自身
            if plugin_name == "astrbot_plugin_command_manager":
                continue
                
            if not plugin_name or not module_path:
                logger.warning(f"插件 '{plugin_name}' 的元数据无效，已跳过")
                continue

            plugin_command_count = 0
            # 遍历所有注册的处理器
            for handler in star_handlers_registry:
                if not isinstance(handler, StarHandlerMetadata):
                    continue
                    
                if handler.handler_module_path != module_path:
                    continue
                    
                command_name: Optional[str] = None
                description: Optional[str] = handler.desc
                
                # 查找命令或命令组
                for filter_ in handler.event_filters:
                    if isinstance(filter_, CommandFilter):
                        command_name = filter_.command_name
                        break
                    elif isinstance(filter_, CommandGroupFilter):
                        command_name = filter_.group_name
                        break
                
                if command_name:
                    if description:
                        formatted_command = f"{command_name}#{description}"
                    else:
                        formatted_command = command_name

                    if formatted_command not in plugin_commands[plugin_name]:
                        plugin_commands[plugin_name].append(formatted_command)
                        plugin_command_count += 1
                        total_commands += 1
            
            if plugin_command_count > 0:
                logger.debug(f"从插件 '{plugin_name}' 提取了 {plugin_command_count} 个命令")
        
        logger.info(f"总共提取了 {total_commands} 个命令，来自 {len(plugin_commands)} 个插件")
        return dict(plugin_commands)


class ImageGenerator:
    """图片生成器"""
    
    def __init__(self, html_renderer: HTMLRenderer):
        self.html_renderer = html_renderer
    
    def generate_help_html(self, categories: List[Dict]) -> str:
        """生成帮助文档HTML"""
        total_commands = sum(len(cat['commands']) for cat in categories)
        
        html_template = f"""
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>AstrBot 指令帮助系统</title>
            <style>
                body {{
                    font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    margin: 0;
                    padding: 40px;
                    color: #333;
                    min-height: 100vh;
                }}
                .container {{
                    max-width: 900px;
                    margin: 0 auto;
                    background: rgba(255, 255, 255, 0.95);
                    backdrop-filter: blur(20px);
                    border-radius: 20px;
                    box-shadow: 0 20px 40px rgba(0, 0, 0, 0.1);
                    overflow: hidden;
                    border: 1px solid rgba(255, 255, 255, 0.2);
                }}
                .header {{
                    background: linear-gradient(135deg, #4a6cf7 0%, #8b5cf6 100%);
                    color: white;
                    padding: 40px;
                    text-align: center;
                    position: relative;
                    overflow: hidden;
                }}
                .header::before {{
                    content: '';
                    position: absolute;
                    top: -50%;
                    left: -50%;
                    width: 200%;
                    height: 200%;
                    background: radial-gradient(circle, rgba(255,255,255,0.1) 0%, rgba(255,255,255,0) 70%);
                }}
                .header h1 {{
                    font-size: 2.5em;
                    margin: 0 0 10px 0;
                    font-weight: 800;
                    text-shadow: 0 2px 10px rgba(0,0,0,0.3);
                }}
                .header p {{
                    font-size: 1.2em;
                    opacity: 0.9;
                    margin: 0;
                    font-weight: 500;
                }}
                .stats {{
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    gap: 20px;
                    padding: 30px;
                    background: rgba(255, 255, 255, 0.1);
                    margin: 20px;
                    border-radius: 15px;
                    backdrop-filter: blur(10px);
                }}
                .stat-item {{
                    text-align: center;
                    color: white;
                }}
                .stat-number {{
                    font-size: 2.5em;
                    font-weight: 800;
                    display: block;
                    line-height: 1;
                }}
                .stat-label {{
                    font-size: 1em;
                    opacity: 0.9;
                    margin-top: 8px;
                }}
                .categories {{
                    padding: 30px;
                }}
                .category {{
                    background: white;
                    border-radius: 15px;
                    padding: 25px;
                    margin-bottom: 25px;
                    box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
                    border-left: 5px solid #4a6cf7;
                    transition: transform 0.3s ease;
                }}
                .category:hover {{
                    transform: translateY(-5px);
                }}
                .category-header {{
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    margin-bottom: 20px;
                    padding-bottom: 15px;
                    border-bottom: 2px solid #f1f5f9;
                }}
                .category-title {{
                    font-size: 1.4em;
                    font-weight: 700;
                    color: #2c3e50;
                    display: flex;
                    align-items: center;
                    gap: 10px;
                }}
                .category-count {{
                    background: #4a6cf7;
                    color: white;
                    padding: 5px 12px;
                    border-radius: 20px;
                    font-size: 0.9em;
                    font-weight: 600;
                }}
                .commands-grid {{
                    display: grid;
                    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
                    gap: 15px;
                }}
                .command-item {{
                    background: #f8fafc;
                    border: 1px solid #e2e8f0;
                    border-radius: 10px;
                    padding: 15px;
                    transition: all 0.3s ease;
                    border-left: 3px solid #10b981;
                }}
                .command-item:hover {{
                    background: white;
                    box-shadow: 0 5px 15px rgba(0, 0, 0, 0.1);
                    transform: translateX(5px);
                }}
                .command-name {{
                    font-weight: 700;
                    color: #1e293b;
                    font-size: 1.1em;
                    margin-bottom: 8px;
                }}
                .command-name::before {{
                    content: '/';
                    color: #64748b;
                    margin-right: 4px;
                }}
                .command-desc {{
                    color: #64748b;
                    font-size: 0.95em;
                    line-height: 1.4;
                }}
                .footer {{
                    background: #1e293b;
                    color: white;
                    padding: 25px;
                    text-align: center;
                    border-radius: 0 0 20px 20px;
                }}
                .footer-text {{
                    opacity: 0.8;
                    font-size: 0.9em;
                }}
                .icon {{
                    font-size: 1.2em;
                    margin-right: 8px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🚀 AstrBot 指令帮助系统</h1>
                    <p>现代化指令管理 • 可视化界面 • 智能分类</p>
                    
                    <div class="stats">
                        <div class="stat-item">
                            <span class="stat-number">{len(categories)}</span>
                            <span class="stat-label">分类数量</span>
                        </div>
                        <div class="stat-item">
                            <span class="stat-number">{total_commands}</span>
                            <span class="stat-label">指令总数</span>
                        </div>
                    </div>
                </div>
                
                <div class="categories">
        """
        
        # 添加分类内容
        for category in categories:
            html_template += f"""
                    <div class="category">
                        <div class="category-header">
                            <div class="category-title">
                                <span class="icon">📁</span>
                                {category['name']}
                            </div>
                            <div class="category-count">
                                {len(category['commands'])} 个指令
                            </div>
                        </div>
                        <div class="commands-grid">
            """
            
            for cmd in category['commands']:
                html_template += f"""
                            <div class="command-item">
                                <div class="command-name">{cmd.get('name', '')}</div>
                                <div class="command-desc">{cmd.get('desc', '该指令暂无描述信息')}</div>
                            </div>
                """
            
            html_template += """
                        </div>
                    </div>
            """
        
        html_template += """
                </div>
                
                <div class="footer">
                    <div class="footer-text">
                        生成时间: """ + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + """ | 指令管理器 v1.3 | 作者: 腾天
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html_template
    
    def generate_cover_html(self) -> str:
        """生成封面HTML"""
        html_template = """
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>指令管理器封面</title>
            <style>
                body {
                    margin: 0;
                    padding: 0;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    min-height: 100vh;
                    font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif;
                }
                .cover-container {
                    width: 800px;
                    height: 600px;
                    background: rgba(255, 255, 255, 0.1);
                    backdrop-filter: blur(20px);
                    border-radius: 30px;
                    box-shadow: 0 25px 50px rgba(0, 0, 0, 0.2);
                    border: 1px solid rgba(255, 255, 255, 0.2);
                    display: flex;
                    flex-direction: column;
                    justify-content: center;
                    align-items: center;
                    text-align: center;
                    color: white;
                    position: relative;
                    overflow: hidden;
                }
                .cover-container::before {
                    content: '';
                    position: absolute;
                    top: -50%;
                    left: -50%;
                    width: 200%;
                    height: 200%;
                    background: radial-gradient(circle, rgba(255,255,255,0.1) 0%, rgba(255,255,255,0) 70%);
                    animation: rotate 20s linear infinite;
                }
                @keyframes rotate {
                    0% { transform: rotate(0deg); }
                    100% { transform: rotate(360deg); }
                }
                .content {
                    position: relative;
                    z-index: 2;
                }
                .title {
                    font-size: 4em;
                    font-weight: 800;
                    margin-bottom: 20px;
                    text-shadow: 0 5px 15px rgba(0,0,0,0.3);
                    background: linear-gradient(45deg, #fff, #e0e7ff);
                    -webkit-background-clip: text;
                    -webkit-text-fill-color: transparent;
                    background-clip: text;
                }
                .subtitle {
                    font-size: 1.8em;
                    margin-bottom: 40px;
                    opacity: 0.9;
                    font-weight: 500;
                }
                .features {
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    gap: 20px;
                    margin-bottom: 40px;
                }
                .feature {
                    background: rgba(255, 255, 255, 0.1);
                    padding: 15px 25px;
                    border-radius: 15px;
                    backdrop-filter: blur(10px);
                    border: 1px solid rgba(255, 255, 255, 0.2);
                    font-size: 1.1em;
                }
                .info {
                    margin-top: 30px;
                    opacity: 0.8;
                    font-size: 1em;
                }
                .version {
                    font-size: 1.2em;
                    font-weight: 600;
                    margin-top: 10px;
                }
            </style>
        </head>
        <body>
            <div class="cover-container">
                <div class="content">
                    <div class="title">🚀 指令管理器</div>
                    <div class="subtitle">现代化指令管理解决方案</div>
                    
                    <div class="features">
                        <div class="feature">📋 可视化指令管理</div>
                        <div class="feature">🎯 智能分类系统</div>
                        <div class="feature">🌐 Web UI界面</div>
                        <div class="feature">🖼️ 图片报告生成</div>
                        <div class="feature">⚡ 高性能处理</div>
                        <div class="feature">🔧 一键安装依赖</div>
                    </div>
                    
                    <div class="info">
                        <div>作者: 腾天</div>
                        <div class="version">版本: v1.3</div>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html_template
    
    async def create_help_image(self, categories: List[Dict]) -> Optional[str]:
        """创建帮助图片"""
        html_content = self.generate_help_html(categories)
        return await self.html_renderer.render_html_to_image(html_content, 1000, 1600)
    
    async def create_cover_image(self) -> Optional[str]:
        """创建封面图片"""
        html_content = self.generate_cover_html()
        return await self.html_renderer.render_html_to_image(html_content, 900, 700)


class WebUIManager:
    def __init__(self, config_manager: ConfigManager, command_parser: CommandParser):
        self.config_manager = config_manager
        self.command_parser = command_parser
        self.web_app = None
        self.runner = None
        self.site = None
        self.port = 8081
        logger.debug("WebUIManager初始化完成")

    async def start_web_server(self):
        """启动Web服务器"""
        try:
            self.web_app = web.Application()
            self.setup_routes()
            
            self.runner = web.AppRunner(self.web_app)
            await self.runner.setup()
            
            self.site = web.TCPSite(self.runner, 'localhost', self.port)
            await self.site.start()
            
            logger.info(f"指令管理器Web UI已启动: http://localhost:{self.port}")
            
        except Exception as e:
            logger.error(f"启动Web服务器失败: {e}")
            logger.exception(e)

    def setup_routes(self):
        """设置Web路由"""
        self.web_app.router.add_get('/', self.handle_index)
        self.web_app.router.add_get('/api/commands', self.handle_api_commands)
        self.web_app.router.add_get('/api/all-commands', self.handle_api_all_commands)
        self.web_app.router.add_post('/api/save-config', self.handle_api_save_config)

    async def handle_index(self, request):
        """处理主页请求"""
        logger.debug("收到主页请求")
        
        # 从外部文件读取HTML内容
        html_file = Path(__file__).parent / "web_ui.html"
        if html_file.exists():
            with open(html_file, 'r', encoding='utf-8') as f:
                html_content = f.read()
            return web.Response(text=html_content, content_type='text/html')
        else:
            # 如果文件不存在，返回简单的错误信息
            return web.Response(text="Web UI文件未找到", status=500)

    async def handle_api_commands(self, request):
        """API: 获取当前配置"""
        logger.debug("收到获取配置API请求")
        try:
            config = self.config_manager.custom_commands
            logger.debug(f"返回配置: {config}")
            return web.json_response(config)
        except Exception as e:
            logger.error(f"获取配置失败: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_all_commands(self, request):
        """API: 获取所有可用指令"""
        logger.debug("收到获取所有命令API请求")
        try:
            all_commands = self.command_parser.get_all_commands()
            logger.debug(f"返回命令数量: {len(all_commands)}")
            return web.json_response(all_commands)
        except Exception as e:
            logger.error(f"获取所有命令失败: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_save_config(self, request):
        """API: 保存配置"""
        try:
            data = await request.json()
            logger.debug(f"收到保存配置请求，数据: {data}")
            
            categories = data.get('categories', [])
            logger.debug(f"分类数量: {len(categories)}")
            
            success = self.config_manager.set_categories(categories)
            logger.debug(f"保存结果: {success}")
            
            if success:
                return web.json_response({'success': True, 'message': '配置保存成功'})
            else:
                return web.json_response({'success': False, 'error': '保存配置失败'})
                
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            logger.exception(e)
            return web.json_response({'success': False, 'error': f'保存失败: {str(e)}'})

    async def stop_web_server(self):
        """停止Web服务器"""
        try:
            if self.site:
                await self.site.stop()
            if self.runner:
                await self.runner.cleanup()
            logger.debug("Web服务器已停止")
        except Exception as e:
            logger.error(f"停止Web服务器失败: {e}")

    def get_web_url(self) -> str:
        """获取Web界面URL"""
        return f"http://localhost:{self.port}"


@register("astrbot_plugin_command_manager", "腾天", "指令管理器 - 提取所有指令并提供美观的Web UI管理", "1.3")
class CommandManagerPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        logger.debug("CommandManagerPlugin初始化开始")
        
        # 初始化HTML渲染器
        self.html_renderer = HTMLRenderer()
        
        # 异步安装HTML渲染依赖
        asyncio.create_task(self._install_dependencies())
        
        # 初始化组件
        self.config_manager = ConfigManager()
        self.command_parser = CommandParser(context)
        self.web_ui = WebUIManager(self.config_manager, self.command_parser)
        self.image_generator = ImageGenerator(self.html_renderer)
        
        # 生成插件封面图片
        asyncio.create_task(self._generate_cover_image())
        
        # 启动Web服务器
        asyncio.create_task(self.web_ui.start_web_server())
        logger.debug("CommandManagerPlugin初始化完成")

    async def _install_dependencies(self):
        """异步安装HTML渲染依赖"""
        logger.info("开始检查HTML渲染依赖...")
        success = await DependencyInstaller.install_html_dependencies()
        if success:
            logger.info("✅ HTML渲染依赖安装完成")
            # 初始化HTML渲染器
            await self.html_renderer.initialize()
        else:
            logger.warning("❌ HTML渲染依赖安装失败，图片生成功能将不可用")

    async def _generate_cover_image(self):
        """生成封面图片"""
        self.cover_image_path = await self.image_generator.create_cover_image()
        if self.cover_image_path:
            logger.info(f"插件封面图片已生成: {self.cover_image_path}")

    @filter.command("帮助", alias={"帮助", "菜单", "功能", "指令", "help"})
    async def show_help(self, event: AstrMessageEvent, 详细程度: str = "简单"):
        """显示自定义帮助菜单"""
        logger.debug(f"收到帮助请求，用户: {event.get_sender_id()}, 详细程度: {详细程度}")
        
        if not self.config_manager.is_enabled():
            logger.debug("帮助系统未启用，拒绝请求")
            yield event.plain_result("帮助功能暂未启用")
            return
            
        categories = self.config_manager.get_categories()
        logger.debug(f"获取到 {len(categories)} 个分类")
        
        if not categories:
            yield event.plain_result("📋 暂无配置的帮助菜单\n\n请通过Web UI配置您的指令分类：\n" + self.web_ui.get_web_url())
            return
        
        # 检查HTML渲染器是否可用
        if not self.html_renderer.initialized:
            # 如果HTML渲染不可用，直接返回文本帮助
            help_text = "🚀 AstrBot 指令帮助系统\n\n"
            help_text += "⚠️ 图片生成功能暂不可用，使用文本格式显示帮助\n\n"
            
            for category in categories:
                help_text += f"📁 {category['name']}\n"
                
                for cmd in category['commands']:
                    cmd_name = cmd.get('name', '')
                    cmd_desc = cmd.get('desc', '')
                    
                    if cmd_desc:
                        help_text += f"  • /{cmd_name} - {cmd_desc}\n"
                    else:
                        help_text += f"  • /{cmd_name}\n"
                
                help_text += "\n"
            
            help_text += f"💡 更多功能请访问Web界面: {self.web_ui.get_web_url()}\n"
            help_text += f"🖼️ 使用 /帮助管理 安装依赖 来安装图片生成功能"
            
            logger.debug(f"发送文本帮助信息，长度: {len(help_text)}")
            yield event.plain_result(help_text)
            return
        
        # HTML渲染可用，尝试生成图片
        try:
            image_path = await self.image_generator.create_help_image(categories)
            
            if image_path:
                # 发送图片
                yield event.image_result(image_path)
                
                # 异步清理临时文件
                async def cleanup_temp_file():
                    await asyncio.sleep(30)  # 等待30秒确保文件已发送
                    try:
                        if os.path.exists(image_path):
                            os.unlink(image_path)
                            logger.debug(f"临时文件已清理: {image_path}")
                    except Exception as e:
                        logger.warning(f"清理临时文件失败: {e}")
                
                asyncio.create_task(cleanup_temp_file())
            else:
                # 图片生成失败，回退到文本帮助
                help_text = "🚀 AstrBot 指令帮助系统\n\n"
                help_text += "⚠️ 图片生成失败，使用文本格式显示帮助\n\n"
                
                for category in categories:
                    help_text += f"📁 {category['name']}\n"
                    
                    for cmd in category['commands']:
                        cmd_name = cmd.get('name', '')
                        cmd_desc = cmd.get('desc', '')
                        
                        if cmd_desc:
                            help_text += f"  • /{cmd_name} - {cmd_desc}\n"
                        else:
                            help_text += f"  • /{cmd_name}\n"
                    
                    help_text += "\n"
                
                help_text += f"💡 更多功能请访问Web界面: {self.web_ui.get_web_url()}\n"
                
                yield event.plain_result(help_text)
                
        except Exception as e:
            logger.error(f"生成帮助时出错: {e}")
            yield event.plain_result("❌ 生成帮助时出现错误，请查看日志")

    @filter.command("帮助管理", alias={"管理帮助", "help_admin"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def help_admin(self, event: AstrMessageEvent, 操作: str = "状态"):
        """管理帮助系统"""
        logger.debug(f"收到管理请求，用户: {event.get_sender_id()}, 操作: {操作}")
        
        if 操作 == "启用":
            self.config_manager.set_enabled(True)
            yield event.plain_result("✅ 帮助系统已启用")
        elif 操作 == "禁用":
            self.config_manager.set_enabled(False)
            yield event.plain_result("🔒 帮助系统已禁用")
        elif 操作 == "重载":
            self.config_manager.reload_config()
            yield event.plain_result("🔄 配置已重新加载")
        elif 操作 == "链接":
            yield event.plain_result(f"🌐 Web管理界面: {self.web_ui.get_web_url()}")
        elif 操作 == "封面" and hasattr(self, 'cover_image_path') and self.cover_image_path:
            yield event.image_result(self.cover_image_path)
        elif 操作 == "安装依赖":
            yield event.plain_result("🔄 开始安装图片生成依赖...")
            success = await DependencyInstaller.install_html_dependencies()
            if success:
                # 重新初始化HTML渲染器
                await self.html_renderer.initialize()
                yield event.plain_result("✅ 图片生成依赖安装成功")
            else:
                yield event.plain_result("❌ 图片生成依赖安装失败，请查看日志")
        elif 操作 == "图片状态":
            renderer_status = "✅ 可用" if self.html_renderer.initialized else "❌ 不可用"
            yield event.plain_result(f"图片生成功能状态: {renderer_status}")
        else:
            status = "✅ 启用" if self.config_manager.is_enabled() else "🔒 禁用"
            categories_count = len(self.config_manager.get_categories())
            commands_count = sum(len(cat.get('commands', [])) for cat in self.config_manager.get_categories())
            renderer_status = "✅ 可用" if self.html_renderer.initialized else "❌ 不可用"
            
            response_text = (
                f"📊 帮助系统状态: {status}\n"
                f"📁 分类数量: {categories_count}\n"
                f"📋 指令数量: {commands_count}\n"
                f"🖼️ 图片生成: {renderer_status}\n"
                f"🌐 Web界面: {self.web_ui.get_web_url()}\n\n"
                f"🛠️ 可用操作:\n"
                f"/帮助管理 启用 - 启用帮助\n"
                f"/帮助管理 禁用 - 禁用帮助\n"
                f"/帮助管理 重载 - 重新加载配置\n"
                f"/帮助管理 链接 - 获取Web界面链接\n"
                f"/帮助管理 图片状态 - 查看图片生成功能状态\n"
                f"/帮助管理 安装依赖 - 安装图片生成依赖"
            )
            
            if hasattr(self, 'cover_image_path') and self.cover_image_path:
                response_text += f"\n/帮助管理 封面 - 查看插件封面图片"
            
            yield event.plain_result(response_text)

    async def terminate(self):
        """插件停止时清理资源"""
        logger.debug("开始停止插件")
        await self.web_ui.stop_web_server()
        await self.html_renderer.close()
        logger.info("指令管理器已停止")