from __future__ import annotations

from typing import Any

from pc_assistant.branding import ASSISTANT_NAME
from pc_assistant.platform_ import get_platform
from pc_assistant.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk


class NotificationTool(ToolBase):
    name = "notify"
    description = "Show a desktop notification."
    effect = ToolEffect.EXTERNAL_SIDE_EFFECT
    capabilities = frozenset({ToolCapability.DESKTOP_CONTROL})
    risk = ToolRisk.MEDIUM

    async def execute(self, **kwargs: Any) -> Any:
        action = kwargs.get("action", "show")
        handlers = {
            "show": self._show_notification,
            "alert": self._show_alert,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"Unknown action: {action}.", "instruction": "Use action='show' or action='alert'. Use tasks for delayed work."}
        return await handler(kwargs)

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["show", "alert"],
                        "description": "show: normal notification; alert: urgent notification",
                    },
                    "title": {
                        "type": "string",
                        "description": "Notification title",
                    },
                    "message": {
                        "type": "string",
                        "description": "Notification message content",
                    },
                    "urgency": {
                        "type": "string",
                        "enum": ["low", "normal", "critical"],
                        "description": "Urgency level (for alert action)",
                    },
                    "sound": {
                        "type": "boolean",
                        "description": "Play sound with notification (default: true)",
                    },
                    "icon": {
                        "type": "string",
                        "description": "Icon name or path",
                    },
                },
                "required": ["action", "title", "message"],
            },
        }

    def skim_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["show", "alert"]},
                    "title": {"type": "string"},
                    "message": {"type": "string"},
                    "urgency": {"type": "string", "enum": ["low", "normal", "critical"]},
                    "sound": {"type": "boolean"},
                    "icon": {"type": "string"},
                },
                "required": ["action", "title", "message"],
            },
        }

    async def _show_notification(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        title = kwargs.get("title", ASSISTANT_NAME)
        message = kwargs.get("message", "")
        sound = kwargs.get("sound", True)
        icon = kwargs.get("icon")

        plat = get_platform()

        try:
            if plat == "windows":
                return await self._notify_windows(title, message, sound, icon)
            elif plat == "macos":
                return await self._notify_macos(title, message, sound, icon)
            else:
                return await self._notify_linux(title, message, sound, icon)
        except Exception as e:
            return {"error": f"Failed to show notification: {e}"}

    async def _show_alert(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        title = kwargs.get("title", "Alert")
        message = kwargs.get("message", "")
        plat = get_platform()

        # Alerts always have sound
        try:
            if plat == "windows":
                return await self._notify_windows(title, message, True, None)
            elif plat == "macos":
                return await self._notify_macos(title, message, True, None)
            else:
                return await self._notify_linux(title, message, True, None)
        except Exception as e:
            return {"error": f"Failed to show alert: {e}"}

    async def _notify_windows(
        self,
        title: str,
        message: str,
        sound: bool,
        icon: str | None,
    ) -> dict[str, Any]:
        try:
            # Try using plyer first (cross-platform)
            from plyer import Notification

            notification = Notification()
            notification.title = title
            notification.message = message
            notification.app_name = ASSISTANT_NAME
            if icon:
                notification.icon = icon
            notification.notify()
            return {
                "success": True,
                "title": title,
                "message": message,
                "platform": "windows",
            }
        except ImportError:
            pass

        try:
            # Fallback to PowerShell
            import asyncio

            ps_script = f'''
            [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
            [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

            $template = @"
            <toast>
                <visual>
                    <binding template="ToastText02">
                        <text id="1">{title}</text>
                        <text id="2">{message}</text>
                    </binding>
                </visual>
            </toast>
"@

            $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
            $xml.LoadXml($template)
            $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
            $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{ASSISTANT_NAME}")
            $notifier.Show($toast)
            '''

            proc = await asyncio.create_subprocess_exec(
                "powershell", "-Command", ps_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

            return {
                "success": True,
                "title": title,
                "message": message,
                "platform": "windows",
            }
        except Exception as e:
            return {"error": f"Failed to show notification: {e}"}

    async def _notify_macos(
        self,
        title: str,
        message: str,
        sound: bool,
        icon: str | None,
    ) -> dict[str, Any]:
        try:
            import asyncio

            args = ["osascript", "-e", f'display notification "{message}" with title "{title}"']
            if sound:
                args.append("sound name default")

            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

            return {
                "success": True,
                "title": title,
                "message": message,
                "platform": "macos",
            }
        except Exception as e:
            return {"error": f"Failed to show notification: {e}"}

    async def _notify_linux(
        self,
        title: str,
        message: str,
        sound: bool,
        icon: str | None,
    ) -> dict[str, Any]:
        try:
            import asyncio

            # Try notify-send first
            args = ["notify-send", title, message]
            if icon:
                args.extend(["-i", icon])
            if sound:
                args.append("--urgency=normal")

            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                return {
                    "success": True,
                    "title": title,
                    "message": message,
                    "platform": "linux",
                }

            # Try plyer as fallback
            try:
                from plyer import Notification

                notification = Notification()
                notification.title = title
                notification.message = message
                notification.app_name = ASSISTANT_NAME
                if icon:
                    notification.icon = icon
                notification.notify()
                return {
                    "success": True,
                    "title": title,
                    "message": message,
                    "platform": "linux",
                }
            except ImportError:
                pass

            return {"error": f"notify-send not available and plyer not installed: {stderr.decode()}"}
        except FileNotFoundError:
            return {"error": "notify-send not installed. Install with: sudo apt install libnotify-bin"}
        except Exception as e:
            return {"error": f"Failed to show notification: {e}"}
