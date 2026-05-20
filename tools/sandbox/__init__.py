"""
Sandbox Tools Package - E2B sandbox tool collection.

Contains all tools that run in an E2B/Daytona sandbox environment:
- Browser operations
- File operations
- Shell commands
- Spreadsheet generation
- Presentation generation
- Image processing
- Web search
"""

from tools.sandbox.daytona_client import daytona_create, daytona_stop, daytona_stop_all
from tools.sandbox.sandbox_browser_session import (
    SandboxBrowserSession,
    sandbox_browser_sessions,
)
from tools.sandbox.sandbox_browser_tools import build_sandbox_browser_tools
from tools.sandbox.sandbox_files_tool import build_sandbox_files_tools
from tools.sandbox.sandbox_image_edit_tool import build_image_edit_tools
from tools.sandbox.sandbox_presentation_outline_tool import build_presentation_outline_tools
from tools.sandbox.sandbox_presentation_tool import build_sandbox_presentation_tools
from tools.sandbox.sandbox_sheets_tool import build_sandbox_sheets_tools
from tools.sandbox.sandbox_shell_tool import build_sandbox_shell_tools
from tools.sandbox.sandbox_vision_tool import build_sandbox_vision_tools
from tools.sandbox.sandbox_web_dev_tool import build_sandbox_web_dev_tools
from tools.sandbox.sandbox_web_search_tool import build_sandbox_web_search_tools

__all__ = [
    # Session management
    "sandbox_browser_sessions",
    "SandboxBrowserSession",
    # Tool builders
    "build_sandbox_browser_tools",
    "build_sandbox_files_tools",
    "build_sandbox_shell_tools",
    "build_sandbox_sheets_tools",
    "build_sandbox_presentation_tools",
    "build_presentation_outline_tools",
    "build_sandbox_vision_tools",
    "build_image_edit_tools",
    "build_sandbox_web_search_tools",
    "build_sandbox_web_dev_tools",
    "daytona_create",
    "daytona_stop",
    "daytona_stop_all",
]
