#!/usr/bin/env python3
"""
Claude Code Usage — macOS Menu Bar App
Shows Claude subscription usage limits (5h, 7d, Sonnet) in the menu bar.
Reads OAuth credentials from macOS Keychain (same as Claude Code CLI).
"""

import json
import subprocess
import urllib.request
import urllib.error
import ssl
import threading
import time
import os
import sys

# Fix SSL on macOS Python
try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = ssl.create_default_context()

import rumps
import AppKit

# --- Config ---
REFRESH_INTERVAL = 600  # seconds (10 min)
MESSAGES_URL = "https://api.anthropic.com/v1/messages"
KEYCHAIN_SERVICE = "Claude Code-credentials"
KEYCHAIN_ACCOUNT = os.environ.get("USER", "matthew")


def get_credentials_from_keychain():
    """Read Claude Code OAuth credentials from macOS Keychain."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.strip()
        try:
            creds = json.loads(raw)
            return creds
        except json.JSONDecodeError:
            return {"token": raw}
    except Exception:
        return None


def get_oauth_token():
    """Extract OAuth access token from stored credentials."""
    creds = get_credentials_from_keychain()
    if not creds:
        return None
    # Credentials may be stored as { "claudeAiOauth": { "accessToken": "...", ... } }
    # or as { "token": "..." } or other formats
    if isinstance(creds, dict):
        for key in ["claudeAiOauth", "oauth", "default"]:
            if key in creds and isinstance(creds[key], dict):
                token = creds[key].get("accessToken") or creds[key].get("access_token")
                if token:
                    return token
        # Try direct token
        return creds.get("accessToken") or creds.get("access_token") or creds.get("token")
    return None


def refresh_oauth_token():
    """Refresh OAuth token using refresh_token and update Keychain."""
    creds = get_credentials_from_keychain()
    if not creds:
        return None
    oauth = creds.get("claudeAiOauth", {})
    refresh_token = oauth.get("refreshToken")
    if not refresh_token:
        return None
    try:
        data = json.dumps({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
        }).encode()
        req = urllib.request.Request(
            "https://platform.claude.com/v1/oauth/token",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10, context=SSL_CONTEXT) as resp:
            result = json.loads(resp.read().decode())
        new_token = result.get("access_token")
        if new_token:
            # Update keychain
            oauth["accessToken"] = new_token
            if result.get("refresh_token"):
                oauth["refreshToken"] = result["refresh_token"]
            if result.get("expires_in"):
                import time
                oauth["expiresAt"] = int((time.time() + result["expires_in"]) * 1000)
            creds["claudeAiOauth"] = oauth
            new_creds = json.dumps(creds)
            subprocess.run(
                ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT],
                capture_output=True
            )
            subprocess.run(
                ["security", "add-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w", new_creds],
                capture_output=True
            )
            return new_token
    except Exception:
        pass
    return None


def fetch_usage(token):
    """Fetch usage data by making a minimal API call and reading rate limit headers."""
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ok"}],
    }).encode()
    req = urllib.request.Request(MESSAGES_URL, data=payload, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "claude-code/2.1.77",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "oauth-2025-04-20",
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT) as resp:
            headers = resp.headers
            result = {}
            # Parse 5h usage
            u5 = headers.get("anthropic-ratelimit-unified-5h-utilization")
            r5 = headers.get("anthropic-ratelimit-unified-5h-reset")
            if u5 is not None:
                from datetime import datetime, timezone
                reset_dt = datetime.fromtimestamp(int(r5), tz=timezone.utc).isoformat() if r5 else None
                result["five_hour"] = {"utilization": float(u5) * 100, "resets_at": reset_dt}
            # Parse 7d usage
            u7 = headers.get("anthropic-ratelimit-unified-7d-utilization")
            r7 = headers.get("anthropic-ratelimit-unified-7d-reset")
            if u7 is not None:
                from datetime import datetime, timezone
                reset_dt = datetime.fromtimestamp(int(r7), tz=timezone.utc).isoformat() if r7 else None
                result["seven_day"] = {"utilization": float(u7) * 100, "resets_at": reset_dt}
            return result
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        if e.code == 429:
            retry = e.headers.get("Retry-After", "60")
            return {"error": f"Rate limited, retry in {retry}s", "retry_after": int(retry)}
        if e.code == 401:
            return {"error": "Token expired", "need_refresh": True}
        return {"error": f"HTTP {e.code}", "detail": body}
    except Exception as e:
        return {"error": str(e)}


def format_bar(pct, width=20):
    """Create a text progress bar."""
    filled = int(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def format_reset(resets_at):
    """Format reset time as relative string."""
    if not resets_at:
        return ""
    try:
        from datetime import datetime, timezone
        reset_dt = datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = reset_dt - now
        total_min = int(diff.total_seconds() / 60)
        if total_min < 0:
            return "resetting..."
        if total_min < 60:
            return f"{total_min}m"
        hours = total_min // 60
        mins = total_min % 60
        if hours < 24:
            return f"{hours}h{mins}m"
        days = hours // 24
        return f"{days}d{hours % 24}h"
    except Exception:
        return ""


class ClaudeUsageApp(rumps.App):
    def __init__(self):
        super().__init__("◐ ...", quit_button=None)
        self.data = None
        self.error = None
        self.menu = [
            rumps.MenuItem("5h: ...", callback=None),
            rumps.MenuItem("7d: ...", callback=None),
            None,
            rumps.MenuItem("Refresh", callback=self.on_refresh),
            rumps.MenuItem("Open Usage Page", callback=self.on_open_usage),
            None,
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]
        self.timer = rumps.Timer(self.refresh_data, REFRESH_INTERVAL)
        self.timer.start()
        # Initial fetch in background
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _fetch_and_update(self):
        token = get_oauth_token()
        if not token:
            self.error = "No OAuth token"
            self._update_display()
            return
        data = fetch_usage(token)
        if data.get("need_refresh"):
            # Try refreshing the token
            new_token = refresh_oauth_token()
            if new_token:
                data = fetch_usage(new_token)
        if data.get("retry_after"):
            # Keep showing old data if we have it, just note the rate limit
            if self.data:
                self._update_display()  # keep old data visible
            else:
                self.error = "Rate limited, waiting..."
                self._update_display()
            return
        if "error" in data:
            if self.data:
                self._update_display()  # keep old data on transient errors
                return
            self.error = data["error"]
            self.data = None
        else:
            self.data = data
            self.error = None
        self._update_display()

    def _set_menubar_title(self, top_line, bottom_line, color=None):
        """Set two-line menu bar title using NSAttributedString, like Stats.app."""
        def _do_set():
            try:
                status_item = self._nsapp.nsstatusitem
            except AttributeError:
                return  # not initialized yet
            button = status_item.button()
            font = AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(11, AppKit.NSFontWeightMedium)
            para = AppKit.NSMutableParagraphStyle.alloc().init()
            para.setMinimumLineHeight_(11)
            para.setMaximumLineHeight_(11)
            para.setLineSpacing_(0)
            para.setAlignment_(AppKit.NSTextAlignmentLeft)
            attrs = {
                AppKit.NSFontAttributeName: font,
                AppKit.NSBaselineOffsetAttributeName: -3,
                AppKit.NSParagraphStyleAttributeName: para,
            }
            if color:
                attrs[AppKit.NSForegroundColorAttributeName] = color
            text = f"{top_line}\n{bottom_line}"
            attr_str = AppKit.NSAttributedString.alloc().initWithString_attributes_(text, attrs)
            button.setAttributedTitle_(attr_str)
        # Must update UI on main thread
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(_do_set)

    def _update_display(self):
        if self.error:
            self._set_menubar_title("⚠️", "err")
            self.menu["5h: ..."].title = f"Error: {self.error}"
            self.menu["7d: ..."].title = ""
            return

        if not self.data:
            self._set_menubar_title("...", "...")
            return

        five_h = self.data.get("five_hour", {})
        seven_d = self.data.get("seven_day", {})

        five_pct = five_h.get("utilization", 0) if five_h else 0
        seven_pct = seven_d.get("utilization", 0) if seven_d else 0

        # Pick color based on 5h usage (most urgent)
        if five_pct >= 80:
            color = AppKit.NSColor.redColor()
        elif five_pct >= 50:
            color = AppKit.NSColor.orangeColor()
        else:
            color = None  # system default

        self._set_menubar_title(f"5h {int(five_pct)}%", f"7d {int(seven_pct)}%", color)

        # Update menu items
        five_reset = format_reset(five_h.get("resets_at")) if five_h else ""
        seven_reset = format_reset(seven_d.get("resets_at")) if seven_d else ""

        self.menu["5h: ..."].title = (
            f"5h: {format_bar(five_pct)} {int(five_pct)}%  ↻{five_reset}"
            if five_h and five_pct is not None else "5h: —"
        )
        self.menu["7d: ..."].title = (
            f"7d: {format_bar(seven_pct)} {int(seven_pct)}%  ↻{seven_reset}"
            if seven_d and seven_pct is not None else "7d: —"
        )

    def refresh_data(self, _=None):
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    @rumps.clicked("Refresh")
    def on_refresh(self, _):
        self._set_menubar_title("...", "...")
        self.refresh_data()

    @rumps.clicked("Open Usage Page")
    def on_open_usage(self, _):
        subprocess.Popen(["open", "https://claude.ai/settings/usage"])


if __name__ == "__main__":
    ClaudeUsageApp().run()
