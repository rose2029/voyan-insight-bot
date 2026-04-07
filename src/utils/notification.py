"""
通知推送模块
"""
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
import requests


class NotificationManager:
    """管理通知推送（企业微信Webhook + 邮件）"""

    def __init__(self, config: dict):
        self.config = config
        self.wecom_webhook = config.get("notification", {}).get("wecom_webhook")
        email_config = config.get("notification", {}).get("email", {})
        self.email_enabled = email_config.get("enabled", False)
        self.smtp_server = email_config.get("smtp_server", "")
        self.smtp_port = email_config.get("smtp_port", 465)
        self.sender = email_config.get("sender", "")
        self.password = email_config.get("password", "")
        self.receivers = email_config.get("receivers", [])

    def send_wecom(self, title: str, content: str, file_path: Optional[str] = None):
        """发送企业微信通知"""
        if not self.wecom_webhook:
            return
        try:
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "content": f"## {title}\n\n{content}"
                },
            }
            resp = requests.post(self.wecom_webhook, json=payload, timeout=10)
            if resp.status_code == 200:
                print(f"[通知] 企业微信推送成功: {title}")
            else:
                print(f"[通知] 企业微信推送失败: {resp.text}")
        except Exception as e:
            print(f"[通知] 企业微信推送异常: {e}")

    def send_email(self, subject: str, body: str, file_path: Optional[str] = None):
        """发送邮件通知"""
        if not self.email_enabled or not self.receivers:
            return
        try:
            msg = MIMEMultipart()
            msg["From"] = self.sender
            msg["To"] = ", ".join(self.receivers)
            msg["Subject"] = subject

            msg.attach(MIMEText(body, "plain", "utf-8"))

            if file_path:
                import os
                from email.mime.base import MIMEBase
                from email import encoders

                part = MIMEBase("application", "octet-stream")
                with open(file_path, "rb") as f:
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f"attachment; filename={os.path.basename(file_path)}",
                )
                msg.attach(part)

            with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port) as server:
                server.login(self.sender, self.password)
                server.sendmail(self.sender, self.receivers, msg.as_string())
            print(f"[通知] 邮件推送成功: {subject}")
        except Exception as e:
            print(f"[通知] 邮件推送异常: {e}")

    def notify(self, title: str, content: str, file_path: Optional[str] = None):
        """发送所有已配置的通知渠道"""
        self.send_wecom(title, content, file_path)
        self.send_email(title, content, file_path)
