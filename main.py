"""
cron: 0 */6 * * *
new Env("Linux.Do 签到")
"""

import os
import random
import time
import functools
import sys
import re
from loguru import logger
from DrissionPage import ChromiumOptions, Chromium
from tabulate import tabulate
from curl_cffi import requests
from bs4 import BeautifulSoup


def retry_decorator(retries=3):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == retries - 1:  # 最后一次尝试
                        logger.error(f"函数 {func.__name__} 最终执行失败: {str(e)}")
                    logger.warning(
                        f"函数 {func.__name__} 第 {attempt + 1}/{retries} 次尝试失败: {str(e)}"
                    )
                    time.sleep(1)
            return None

        return wrapper

    return decorator


os.environ.pop("DISPLAY", None)
os.environ.pop("DYLD_LIBRARY_PATH", None)

USERNAME = os.environ.get("LINUXDO_USERNAME")
PASSWORD = os.environ.get("LINUXDO_PASSWORD")
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in [
    "false",
    "0",
    "off",
]
if not USERNAME:
    USERNAME = os.environ.get("USERNAME")
if not PASSWORD:
    PASSWORD = os.environ.get("PASSWORD")
GOTIFY_URL = os.environ.get("GOTIFY_URL")  # Gotify 服务器地址
GOTIFY_TOKEN = os.environ.get("GOTIFY_TOKEN")  # Gotify 应用的 API Token
SC3_PUSH_KEY = os.environ.get("SC3_PUSH_KEY")  # Server酱³ SendKey
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")  # Telegram Bot Token
TELEGRAM_USERID = os.environ.get("TELEGRAM_USERID")  # Telegram 用户 ID

HOME_URL = "https://linux.do/"
LOGIN_URL = "https://linux.do/login"
SESSION_URL = "https://linux.do/session"
CSRF_URL = "https://linux.do/session/csrf"


class LinuxDoBrowser:
    def __init__(self) -> None:
        from sys import platform

        if platform == "linux" or platform == "linux2":
            platformIdentifier = "X11; Linux x86_64"
        elif platform == "darwin":
            platformIdentifier = "Macintosh; Intel Mac OS X 10_15_7"
        elif platform == "win32":
            platformIdentifier = "Windows NT 10.0; Win64; x64"

        co = (
            ChromiumOptions()
            .headless(True)
            .incognito(True)
            .set_argument("--no-sandbox")
        )
        co.set_user_agent(
            f"Mozilla/5.0 ({platformIdentifier}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        self.browser = Chromium(co)
        self.page = self.browser.new_tab()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )

        # 任务统计
        self.stats = {
            "browse_count": 0,      # 浏览帖子数
            "like_count": 0,        # 点赞次数
            "like_success": 0,      # 点赞成功次数
            "login_success": False, # 登录是否成功
            "browse_success": False,# 浏览任务是否成功
        }

        # Connect Info 数据
        self.connect_info = []
        self.user_level = 0  # 用户等级

    def login(self):
        logger.info("开始登录")
        # Step 1: Get CSRF Token
        logger.info("获取 CSRF token...")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": LOGIN_URL,
        }
        resp_csrf = self.session.get(CSRF_URL, headers=headers, impersonate="chrome136")
        csrf_data = resp_csrf.json()
        csrf_token = csrf_data.get("csrf")
        logger.info(f"CSRF Token obtained: {csrf_token[:10]}...")

        # Step 2: Login
        logger.info("正在登录...")
        headers.update(
            {
                "X-CSRF-Token": csrf_token,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://linux.do",
            }
        )

        data = {
            "login": USERNAME,
            "password": PASSWORD,
            "second_factor_method": "1",
            "timezone": "Asia/Shanghai",
        }

        try:
            resp_login = self.session.post(
                SESSION_URL, data=data, impersonate="chrome136", headers=headers
            )

            if resp_login.status_code == 200:
                response_json = resp_login.json()
                if response_json.get("error"):
                    logger.error(f"登录失败: {response_json.get('error')}")
                    return False
                logger.info("登录成功!")
                self.stats["login_success"] = True
            else:
                logger.error(f"登录失败，状态码: {resp_login.status_code}")
                logger.error(resp_login.text)
                return False
        except Exception as e:
            logger.error(f"登录请求异常: {e}")
            return False

        self.get_user_level_only()  # 只获取用户等级（1个API请求）

        # Step 3: Pass cookies to DrissionPage
        logger.info("同步 Cookie 到 DrissionPage...")

        # 先访问页面，确保域名正确
        self.page.get("https://linux.do/")
        time.sleep(2)

        cookies_dict = self.session.cookies.get_dict()
        logger.info(f"获取到 {len(cookies_dict)} 个 Cookie")

        dp_cookies = []
        for name, value in cookies_dict.items():
            dp_cookies.append(
                {
                    "name": name,
                    "value": value,
                    "domain": ".linux.do",
                    "path": "/",
                }
            )

        self.page.set.cookies(dp_cookies)

        # 刷新页面使 Cookie 生效
        logger.info("刷新页面使 Cookie 生效...")
        self.page.refresh()
        time.sleep(5)

        # 导航到 /latest 页面
        logger.info("导航至 linux.do/latest...")
        self.page.get("https://linux.do/latest")

        # 等待页面加载（代理环境下可能需要更长时间）
        logger.info("等待页面加载...")
        time.sleep(10)

        # 验证登录状态
        login_btn = self.page.ele('css:button.login-button', timeout=2)
        if login_btn:
            logger.warning("检测到登录按钮，Cookie 可能未正确同步，尝试再次刷新...")
            self.page.refresh()
            time.sleep(5)

        logger.info("Cookie 已设置，继续执行任务...")
        return True

    def click_topic(self):
        # 等待页面加载完成
        time.sleep(2)

        # 使用多种选择器尝试获取帖子链接
        # 方法1：使用 class 选择器（最可靠）
        topic_list = self.page.eles('css:a.title.raw-topic-link')
        if not topic_list:
            # 方法2：使用 span.link-top-line 下的链接
            topic_list = self.page.eles('css:span.link-top-line > a')
        if not topic_list:
            # 方法3：使用 #list-area 下的链接（原方法）
            list_area = self.page.ele('@id=list-area', timeout=5)
            if list_area:
                topic_list = list_area.eles('css:a.title')
        if not topic_list:
            # 方法4：备用 - 获取所有帖子链接
            all_links = self.page.eles('css:a[href^="/t/topic/"]')
            # 过滤：只保留标题链接（href 不包含 /数字 结尾的回复链接）
            topic_list = [t for t in all_links if not re.search(r'/\d+$', t.attr("href") or "")]

        if not topic_list:
            logger.error("未找到主题帖")
            return False

        # 过滤掉置顶帖（前3个通常是置顶的公告帖）
        if len(topic_list) > 3:
            topic_list = topic_list[3:]
        sample_count = min(10, len(topic_list))
        logger.info(f"发现 {len(topic_list)} 个主题帖，随机选择 {sample_count} 个")
        topics_to_browse = random.sample(topic_list, sample_count)

        # 在浏览到一半时获取升级进度（分散请求，避免429）
        mid_point = len(topics_to_browse) // 2
        for i, topic in enumerate(topics_to_browse):
            self.click_one_topic(topic.attr("href"))

            # 在中间点获取升级进度
            if i == mid_point and not self.connect_info:
                logger.info("浏览中途，获取升级进度...")
                time.sleep(5)  # 短暂等待
                self.get_user_progress()

        return True

    @retry_decorator()
    def click_one_topic(self, topic_url):
        new_page = self.browser.new_tab()
        new_page.get(topic_url)
        # 等待页面加载完成
        time.sleep(random.uniform(2, 3))
        if random.random() < 0.3:  # 30% 概率点赞
            self.stats["like_count"] += 1
            self.click_like(new_page)
        self.browse_post(new_page)
        self.stats["browse_count"] += 1
        new_page.close()

    def browse_post(self, page):
        prev_url = None
        # 开始自动滚动，最多滚动10次
        for _ in range(10):
            # 随机滚动一段距离
            scroll_distance = random.randint(550, 650)  # 随机滚动 550-650 像素
            logger.info(f"向下滚动 {scroll_distance} 像素...")
            page.run_js(f"window.scrollBy(0, {scroll_distance})")
            logger.info(f"已加载页面: {page.url}")

            if random.random() < 0.03:  # 33 * 4 = 132
                logger.success("随机退出浏览")
                break

            # 检查是否到达页面底部
            at_bottom = page.run_js(
                "window.scrollY + window.innerHeight >= document.body.scrollHeight"
            )
            current_url = page.url
            if current_url != prev_url:
                prev_url = current_url
            elif at_bottom and prev_url == current_url:
                logger.success("已到达页面底部，退出浏览")
                break

            # 动态随机等待
            wait_time = random.uniform(2, 4)  # 随机等待 2-4 秒
            logger.info(f"等待 {wait_time:.2f} 秒...")
            time.sleep(wait_time)

    def run(self):
        task_success = True  # 跟踪任务是否全部成功

        login_res = self.login()
        if not login_res:
            logger.error("登录失败，程序终止")
            task_success = False
            self.page.close()
            self.browser.quit()
            return

        browse_success = True
        if BROWSE_ENABLED:
            click_topic_res = self.click_topic()
            if not click_topic_res:
                # 如果找不到主题，可能是页面未加载完成，等待后重试
                logger.warning("未找到主题帖，等待后重试...")
                time.sleep(5)
                self.page.refresh()
                time.sleep(3)
                click_topic_res = self.click_topic()
                if not click_topic_res:
                    logger.error("点击主题失败，程序终止")
                    browse_success = False
                    task_success = False

            if browse_success:
                logger.info("完成浏览任务")
                self.stats["browse_success"] = True
                # 如果中途没有获取到升级进度，最后再尝试一次
                if not self.connect_info:
                    logger.info("等待 30 秒后获取升级进度...")
                    time.sleep(30)
                    self.get_user_progress()

        # 只有在任务成功时才发送通知
        if task_success and self.stats["browse_count"] > 0:
            logger.success("Check in success")
            self.send_notifications(BROWSE_ENABLED, task_success=True)
        else:
            logger.warning("任务未完全成功，不发送通知")

        self.page.close()
        self.browser.quit()

    def click_like(self, page):
        """点赞帖子 - 使用 Discourse Reactions 插件

        HTML结构（来自真实页面）：
        <div class="discourse-reactions-actions can-toggle-reaction">  <!-- 或 has-reacted -->
          <div class="discourse-reactions-reaction-button" title="点赞此帖子">
            <button class="btn btn-toggle-reaction-like" title="点赞此帖子">

        注意：页面有两个 discourse-reactions-actions div（left 和 right），
        只有 right 那个包含点赞按钮
        """
        try:
            # 等待页面稳定
            time.sleep(1.5)

            # 使用 DrissionPage 定位第一个帖子
            articles = page.eles('tag:article')
            if not articles:
                logger.info("未找到帖子")
                return

            first_article = articles[0]

            # 查找包含点赞按钮的 actions div（right 那个）
            # 通过查找包含 button 的 div 来定位正确的容器
            actions_divs = first_article.eles('.discourse-reactions-actions')
            right_actions_div = None
            for div in actions_divs:
                if div.ele('button', timeout=0.2):
                    right_actions_div = div
                    break

            if right_actions_div:
                classes = right_actions_div.attr('class') or ''

                # 检查是否已点赞
                if 'has-reacted' in classes:
                    logger.info("帖子已经点过赞了，跳过")
                    return

                # 检查是否可以点赞
                if 'can-toggle-reaction' not in classes:
                    logger.info(f"无法点赞此帖子，class: {classes}")
                    return

            # 查找点赞按钮 - 使用多种选择器
            like_btn = first_article.ele('button.btn-toggle-reaction-like', timeout=1)
            if not like_btn:
                like_btn = first_article.ele('css:.discourse-reactions-reaction-button button', timeout=0.5)
            if not like_btn:
                like_btn = first_article.ele('css:button.reaction-button', timeout=0.5)

            if not like_btn:
                # 检查是否是未登录状态
                login_hint = first_article.ele('css:button[title*="登录"]', timeout=0.3)
                if login_hint or first_article.ele('css:button[title*="注册"]', timeout=0.3):
                    logger.warning("检测到未登录状态，无法点赞（Cookie 可能未正确同步）")
                else:
                    logger.info("未找到点赞按钮")
                return

            # 检查按钮状态
            btn_title = like_btn.attr('title') or ''
            logger.info(f"点赞按钮 title: {btn_title}")

            if '登录' in btn_title or '注册' in btn_title:
                logger.warning("需要登录才能点赞（Cookie 可能未正确同步）")
                return
            if '自己' in btn_title:
                logger.info("无法给自己的帖子点赞")
                return
            if '移除' in btn_title or '无法' in btn_title:
                logger.info("帖子已点赞或无法操作")
                return
            if btn_title != '点赞此帖子':
                logger.info(f"按钮状态异常: {btn_title}")
                # 继续尝试点赞

            # 直接点击按钮进行点赞
            logger.info("点击点赞按钮...")
            like_btn.click()

            # 等待页面响应（点赞需要服务器处理）
            time.sleep(2.0)

            # 验证点赞是否成功 - 重新查找正确的 actions div
            verified = False
            actions_divs_verify = first_article.eles('.discourse-reactions-actions')
            for div in actions_divs_verify:
                if div.ele('button', timeout=0.2):
                    classes = div.attr('class') or ''
                    if 'has-reacted' in classes:
                        logger.info("点赞成功！")
                        self.stats["like_success"] += 1
                        verified = True
                    else:
                        # 检查按钮 title 是否变化
                        btn = div.ele('button', timeout=0.2)
                        if btn:
                            new_title = btn.attr('title') or ''
                            if '移除' in new_title or '无法' in new_title:
                                logger.info("点赞成功！（通过按钮状态验证）")
                                self.stats["like_success"] += 1
                                verified = True
                            else:
                                logger.warning(f"点赞可能未成功，按钮title: {new_title}")
                    break

            if not verified:
                logger.warning("无法验证点赞状态（可能已成功但页面未更新）")

            time.sleep(random.uniform(1, 2))

        except Exception as e:
            logger.error(f"点赞失败: {str(e)}")

    def get_user_level_only(self):
        """仅获取用户等级 - 登录后立即调用，只请求 connect.linux.do"""
        logger.info("获取用户等级...")
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        }

        try:
            resp = self.session.get(
                "https://connect.linux.do/", headers=headers, impersonate="chrome136"
            )
            soup = BeautifulSoup(resp.text, "html.parser")
            page_text = soup.get_text()

            # 解析用户等级 - 从页面文本中提取 "X级用户"
            level_match = re.search(r"(\d+)级用户", page_text)
            if level_match:
                self.user_level = int(level_match.group(1))
                logger.info(f"用户等级: {self.user_level} 级")
        except Exception as e:
            logger.warning(f"获取用户等级失败: {e}")

    def get_user_progress(self):
        """获取用户升级进度数据 - 浏览任务完成后调用，请求 linux.do API

        包含重试机制，遇到 429 会等待后重试
        """
        logger.info("获取用户升级进度...")

        max_retries = 3
        retry_delays = [60, 120, 180]  # 重试等待时间：1分钟、2分钟、3分钟

        for attempt in range(max_retries):
            try:
                api_headers = {"Accept": "application/json"}
                resp_api = self.session.get(
                    f"https://linux.do/u/{USERNAME}.json",
                    headers=api_headers,
                    impersonate="chrome136"
                )

                if resp_api.status_code == 200:
                    user_data = resp_api.json()
                    user = user_data.get("user", {})

                    # 构建 connect_info 数据
                    info = []

                    # 从 API 获取的数据
                    days_visited = user.get("days_visited", 0)
                    posts_read_count = user.get("posts_read_count", 0)
                    topics_entered = user.get("topics_entered", 0)
                    likes_given = user.get("likes_given", 0)
                    likes_received = user.get("likes_received", 0)
                    topic_count = user.get("topic_count", 0)
                    post_count = user.get("post_count", 0)
                    time_read = user.get("time_read", 0)  # 秒

                    # 转换阅读时间为分钟
                    time_read_minutes = time_read // 60 if time_read else 0

                    # 根据当前等级设置升级要求 (1级升2级的要求)
                    if self.user_level == 0:
                        requirements = {
                            "访问天数": 5, "浏览的话题": 10, "已读帖子": 50,
                            "阅读时间": 30, "点赞": 0, "获赞": 0, "回复的话题": 0
                        }
                    elif self.user_level == 1:
                        requirements = {
                            "访问天数": 15, "浏览的话题": 20, "已读帖子": 100,
                            "阅读时间": 60, "点赞": 1, "获赞": 1, "回复的话题": 3
                        }
                    elif self.user_level == 2:
                        requirements = {
                            "访问天数": 50, "浏览的话题": 100, "已读帖子": 500,
                            "阅读时间": 120, "点赞": 20, "获赞": 10, "回复的话题": 10
                        }
                    else:
                        requirements = {}

                    # 构建数据
                    info.append(["访问天数", str(days_visited), str(requirements.get("访问天数", 0))])
                    info.append(["点赞", str(likes_given), str(requirements.get("点赞", 0))])
                    info.append(["获赞", str(likes_received), str(requirements.get("获赞", 0))])
                    info.append(["回复的话题", str(post_count), str(requirements.get("回复的话题", 0))])
                    info.append(["浏览的话题", str(topics_entered), str(requirements.get("浏览的话题", 0))])
                    info.append(["已读帖子", str(posts_read_count), str(requirements.get("已读帖子", 0))])
                    info.append(["阅读时间", str(time_read_minutes), str(requirements.get("阅读时间", 0))])

                    self.connect_info = info
                    logger.info(f"获取到 {len(info)} 条用户数据")

                    print("--------------Connect Info-----------------")
                    print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
                    return  # 成功，退出函数

                elif resp_api.status_code == 429:
                    if attempt < max_retries - 1:
                        wait_time = retry_delays[attempt]
                        logger.warning(f"获取用户 API 失败: 429 (速率限制)，等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                    else:
                        logger.warning(f"获取用户 API 失败: 429 (已重试 {max_retries} 次)")
                else:
                    logger.warning(f"获取用户 API 失败: {resp_api.status_code}")
                    return  # 非 429 错误，不重试

            except Exception as e:
                logger.error(f"获取用户摘要失败: {e}")
                return

    def get_user_level(self):
        """获取用户当前等级"""
        # 优先使用从 Connect Info 页面解析的等级
        if self.user_level > 0:
            return self.user_level

        # 备用方案：从 API 获取
        try:
            headers = {
                "Accept": "application/json",
            }
            resp = self.session.get(
                f"https://linux.do/u/{USERNAME}.json",
                headers=headers,
                impersonate="chrome136"
            )
            if resp.status_code == 200:
                data = resp.json()
                user = data.get("user", {})
                return user.get("trust_level", 0)
        except Exception as e:
            logger.warning(f"获取用户等级失败: {e}")
        return 0

    def parse_connect_info_value(self, value_str):
        """解析 Connect Info 中的数值，返回 (当前值, 要求值, 是否达标)"""
        try:
            # 处理百分比格式，如 "96% (96 / 100 天数)"
            if "%" in value_str:
                match = re.search(r"(\d+)%", value_str)
                if match:
                    return int(match.group(1)), 100, int(match.group(1)) >= 50
            # 处理 ≥ 格式，如 "≥ 2"
            if "≥" in value_str:
                match = re.search(r"≥\s*(\d+)", value_str)
                if match:
                    return int(match.group(1)), int(match.group(1)), True
            # 处理纯数字
            match = re.search(r"(\d+)", value_str)
            if match:
                return int(match.group(1)), 0, True
        except:
            pass
        return 0, 0, False

    def build_telegram_message(self):
        """构建 Telegram 通知消息"""
        level = self.get_user_level()
        next_level = level + 1 if level < 4 else 4

        # 构建消息
        msg_lines = []
        msg_lines.append(f"✅ LINUX DO 签到成功")
        msg_lines.append(f"👤 {USERNAME}")
        msg_lines.append("")

        # 执行统计 - 从 connect_info 获取更多数据
        msg_lines.append("📊 执行统计")
        msg_lines.append(f"├ 📖 浏览：{self.stats['browse_count']} 篇")

        # 从 connect_info 获取阅读评论数（已读帖子）
        read_posts = "0"
        for item in self.connect_info:
            if len(item) >= 2 and "已读帖子" in item[0]:
                read_posts = item[1]
                break
        msg_lines.append(f"├ 💬 阅读评论：{read_posts} 条")
        msg_lines.append(f"├ 👍 点赞：{self.stats['like_success']} 次")
        msg_lines.append(f"├ 📝 发帖：0 篇")
        msg_lines.append(f"└ ✍️ 评论：0 条")
        msg_lines.append("")

        # 当前等级
        msg_lines.append(f"🏆 当前等级：{level} 级")
        msg_lines.append("")

        # 升级进度
        if self.connect_info:
            msg_lines.append(f"📈 升级进度 ({level}→{next_level} 级)")

            completed_count = 0
            total_count = 0
            progress_items = []

            for item in self.connect_info:
                if len(item) >= 3:
                    project = item[0]
                    current = item[1]
                    requirement = item[2]

                    # 跳过负面指标
                    if "举报" in project or "禁言" in project or "封禁" in project:
                        continue

                    total_count += 1

                    # 解析当前值和要求值
                    try:
                        # 处理百分比格式，如 "96% (96 / 100 天数)"
                        if "%" in current:
                            match = re.search(r"(\d+)%", current)
                            curr_val = int(match.group(1)) if match else 0
                            req_val = 50  # 访问次数要求 50%
                            is_complete = curr_val >= req_val
                            display = f"{curr_val}% (要求 {req_val}%)"
                        else:
                            # 纯数字
                            curr_match = re.search(r"(\d+)", current)
                            req_match = re.search(r"(\d+)", requirement)
                            curr_val = int(curr_match.group(1)) if curr_match else 0
                            req_val = int(req_match.group(1)) if req_match else 0
                            is_complete = curr_val >= req_val if req_val > 0 else True

                            # 根据项目类型确定单位
                            if "天" in project or "访问" in project:
                                unit = "天"
                            elif "时间" in project or "分钟" in project:
                                unit = "分钟"
                            elif "话题" in project:
                                unit = "个"
                            elif "帖子" in project:
                                unit = "篇"
                            else:
                                unit = "次"

                            if req_val > 0:
                                diff = req_val - curr_val
                                if is_complete:
                                    display = f"{curr_val}{unit}/{req_val}{unit}"
                                else:
                                    display = f"{curr_val}{unit}/{req_val}{unit} (差 {diff}{unit})"
                            else:
                                display = f"{curr_val}{unit}"

                        status_emoji = "✅" if is_complete else "⏳"
                        if is_complete:
                            completed_count += 1

                        # 简化项目名称
                        short_name = project.replace("（所有时间）", "").replace("（过去 6 个月）", "")
                        progress_items.append(f"├ {status_emoji} {short_name}：{display}")

                    except Exception as e:
                        progress_items.append(f"├ 📌 {project}：{current}/{requirement}")

            # 修改最后一项的前缀为 └
            if progress_items:
                progress_items[-1] = progress_items[-1].replace("├", "└", 1)
                msg_lines.extend(progress_items)

            msg_lines.append("")

            # 完成度
            if total_count > 0:
                completion_rate = int(completed_count / total_count * 100)
                # 生成进度条
                filled = completed_count
                empty = total_count - completed_count
                progress_bar = "🟩" * filled + "⬜" * empty

                msg_lines.append(f"🎯 完成度 {completion_rate}%")
                msg_lines.append(progress_bar)
                msg_lines.append(f"已完成 {completed_count}/{total_count} 项")
        else:
            # 没有获取到升级进度数据（可能是 API 429）
            msg_lines.append("📈 升级进度：暂无数据")
            msg_lines.append("（API 速率限制，稍后重试）")

        return "\n".join(msg_lines)

    def send_notifications(self, browse_enabled, task_success=True):
        """发送通知，只在任务成功时发送"""
        if not task_success:
            logger.info("任务未完全成功，跳过通知发送")
            return

        # 构建详细的通知消息
        telegram_msg = self.build_telegram_message()

        # 简单消息用于 Gotify 和 Server酱
        status_msg = f"✅每日登录成功: {USERNAME}"
        if browse_enabled:
            status_msg += f" + 浏览 {self.stats['browse_count']} 篇"
            if self.stats['like_success'] > 0:
                status_msg += f" + 点赞 {self.stats['like_success']} 次"

        # Telegram 通知（使用详细格式）
        if TELEGRAM_TOKEN and TELEGRAM_USERID:
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                data = {
                    "chat_id": TELEGRAM_USERID,
                    "text": telegram_msg,
                    "parse_mode": "HTML"
                }
                response = requests.post(url, json=data, timeout=10)
                response.raise_for_status()
                result = response.json()
                if result.get("ok"):
                    logger.success("消息已推送至 Telegram")
                else:
                    logger.error(f"Telegram 推送失败: {result}")
            except Exception as e:
                logger.error(f"Telegram 推送失败: {str(e)}")
        else:
            logger.info("未配置 Telegram 环境变量，跳过 Telegram 通知")

        # Gotify 通知
        if GOTIFY_URL and GOTIFY_TOKEN:
            try:
                response = requests.post(
                    f"{GOTIFY_URL}/message",
                    params={"token": GOTIFY_TOKEN},
                    json={"title": "LINUX DO", "message": status_msg, "priority": 1},
                    timeout=10,
                )
                response.raise_for_status()
                logger.success("消息已推送至Gotify")
            except Exception as e:
                logger.error(f"Gotify推送失败: {str(e)}")
        else:
            logger.info("未配置Gotify环境变量，跳过通知发送")

        # Server酱³ 通知
        if SC3_PUSH_KEY:
            match = re.match(r"sct(\d+)t", SC3_PUSH_KEY, re.I)
            if not match:
                logger.error(
                    "❌ SC3_PUSH_KEY格式错误，未获取到UID，无法使用Server酱³推送"
                )
                return

            uid = match.group(1)
            url = f"https://{uid}.push.ft07.com/send/{SC3_PUSH_KEY}"
            params = {"title": "LINUX DO", "desp": status_msg}

            attempts = 5
            for attempt in range(attempts):
                try:
                    response = requests.get(url, params=params, timeout=10)
                    response.raise_for_status()
                    logger.success(f"Server酱³推送成功: {response.text}")
                    break
                except Exception as e:
                    logger.error(f"Server酱³推送失败: {str(e)}")
                    if attempt < attempts - 1:
                        sleep_time = random.randint(180, 360)
                        logger.info(f"将在 {sleep_time} 秒后重试...")
                        time.sleep(sleep_time)


if __name__ == "__main__":
    if not USERNAME or not PASSWORD:
        print("Please set USERNAME and PASSWORD")
        exit(1)
    l = LinuxDoBrowser()
    l.run()
