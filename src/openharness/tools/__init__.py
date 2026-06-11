"""Built-in tool registration."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any

from pydantic import BaseModel

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult


@dataclass(frozen=True)
class _ToolSpec:
    module: str
    class_name: str
    name: str
    description: str


class _LazyTool(BaseTool):
    """Defer importing a built-in tool module until the tool is actually needed."""

    def __init__(self, spec: _ToolSpec, *args: Any) -> None:
        self._spec = spec
        self._args = args
        self._instance: BaseTool | None = None
        self.name = spec.name
        self.description = spec.description

    def _load(self) -> BaseTool:
        if self._instance is None:
            module = import_module(self._spec.module)
            tool_class = getattr(module, self._spec.class_name)
            self._instance = tool_class(*self._args)
        return self._instance

    @property
    def input_model(self) -> type[BaseModel]:
        return self._load().input_model

    def is_read_only(self, arguments: BaseModel) -> bool:
        return self._load().is_read_only(arguments)

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        return await self._load().execute(arguments, context)


_CORE_TOOL_SPECS: tuple[_ToolSpec, ...] = (
    _ToolSpec("openharness.tools.bash_tool", "BashTool", "bash", "Execute shell commands."),
    _ToolSpec(
        "openharness.tools.ask_user_question_tool",
        "AskUserQuestionTool",
        "ask_user_question",
        "Ask the user a clarifying question.",
    ),
    _ToolSpec("openharness.tools.file_read_tool", "FileReadTool", "read_file", "Read a file."),
    _ToolSpec("openharness.tools.file_write_tool", "FileWriteTool", "write_file", "Write a file."),
    _ToolSpec("openharness.tools.file_edit_tool", "FileEditTool", "edit_file", "Edit a file."),
    _ToolSpec(
        "openharness.tools.notebook_edit_tool",
        "NotebookEditTool",
        "notebook_edit",
        "Edit a Jupyter notebook cell.",
    ),
    _ToolSpec("openharness.tools.lsp_tool", "LspTool", "lsp", "Run lightweight LSP-style code queries."),
    _ToolSpec("openharness.tools.mcp_auth_tool", "McpAuthTool", "mcp_auth", "Manage MCP auth flows."),
    _ToolSpec("openharness.tools.glob_tool", "GlobTool", "glob", "List files matching a glob pattern."),
    _ToolSpec("openharness.tools.grep_tool", "GrepTool", "grep", "Search file contents with a regular expression."),
    _ToolSpec("openharness.tools.skill_tool", "SkillTool", "skill", "Load an OpenHarness skill."),
    _ToolSpec("openharness.tools.tool_search_tool", "ToolSearchTool", "tool_search", "Search the available tool list by name or description."),
    _ToolSpec("openharness.tools.session_search_tool", "SessionSearchTool", "session_search", "Search past conversations (full-text, zero LLM cost)."),
    _ToolSpec("openharness.tools.skill_manage_tool", "SkillManageTool", "skill_manage", "Create, improve, or retire user skills."),
    _ToolSpec("openharness.tools.config_tool", "ConfigTool", "config", "Read or update OpenHarness configuration."),
    _ToolSpec("openharness.tools.brief_tool", "BriefTool", "brief", "Condense text to a requested length."),
    _ToolSpec("openharness.tools.sleep_tool", "SleepTool", "sleep", "Wait for a number of seconds."),
    _ToolSpec("openharness.tools.enter_worktree_tool", "EnterWorktreeTool", "enter_worktree", "Enter an isolated git worktree."),
    _ToolSpec("openharness.tools.exit_worktree_tool", "ExitWorktreeTool", "exit_worktree", "Exit the active git worktree."),
    _ToolSpec("openharness.tools.todo_write_tool", "TodoWriteTool", "todo_write", "Write the current task checklist."),
    _ToolSpec("openharness.tools.enter_plan_mode_tool", "EnterPlanModeTool", "enter_plan_mode", "Enter plan mode."),
    _ToolSpec("openharness.tools.exit_plan_mode_tool", "ExitPlanModeTool", "exit_plan_mode", "Exit plan mode."),
    _ToolSpec("openharness.tools.cron_create_tool", "CronCreateTool", "cron_create", "Create a local cron job."),
    _ToolSpec("openharness.tools.cron_list_tool", "CronListTool", "cron_list", "List local cron jobs."),
    _ToolSpec("openharness.tools.cron_delete_tool", "CronDeleteTool", "cron_delete", "Delete a local cron job."),
    _ToolSpec("openharness.tools.cron_toggle_tool", "CronToggleTool", "cron_toggle", "Enable or disable a local cron job."),
    _ToolSpec("openharness.tools.remote_trigger_tool", "RemoteTriggerTool", "remote_trigger", "Trigger a named remote command."),
    _ToolSpec("openharness.tools.task_create_tool", "TaskCreateTool", "task_create", "Create a background task."),
    _ToolSpec("openharness.tools.task_get_tool", "TaskGetTool", "task_get", "Inspect a background task."),
    _ToolSpec("openharness.tools.task_list_tool", "TaskListTool", "task_list", "List background tasks."),
    _ToolSpec("openharness.tools.task_stop_tool", "TaskStopTool", "task_stop", "Stop a background task."),
    _ToolSpec("openharness.tools.task_output_tool", "TaskOutputTool", "task_output", "Read the output log for a background task."),
    _ToolSpec("openharness.tools.task_update_tool", "TaskUpdateTool", "task_update", "Update background task metadata."),
    _ToolSpec("openharness.tools.agent_tool", "AgentTool", "agent", "Spawn a background agent task."),
    _ToolSpec("openharness.tools.send_message_tool", "SendMessageTool", "send_message", "Send a message to a background task."),
    _ToolSpec("openharness.tools.team_create_tool", "TeamCreateTool", "team_create", "Create a local agent team."),
    _ToolSpec("openharness.tools.team_delete_tool", "TeamDeleteTool", "team_delete", "Delete a local agent team."),
)

_NETWORK_TOOL_SPECS: tuple[_ToolSpec, ...] = (
    _ToolSpec("openharness.tools.image_to_text_tool", "ImageToTextTool", "image_to_text", "Describe an image with a vision model."),
    _ToolSpec("openharness.tools.image_generation_tool", "ImageGenerationTool", "image_generation", "Generate an image with an image model."),
    _ToolSpec("openharness.tools.web_fetch_tool", "WebFetchTool", "web_fetch", "Fetch a URL."),
    _ToolSpec("openharness.tools.web_search_tool", "WebSearchTool", "web_search", "Search the web."),
)

_MCP_STATIC_TOOL_SPECS: tuple[_ToolSpec, ...] = (
    _ToolSpec(
        "openharness.tools.list_mcp_resources_tool",
        "ListMcpResourcesTool",
        "list_mcp_resources",
        "List resources exposed by connected MCP servers.",
    ),
    _ToolSpec(
        "openharness.tools.read_mcp_resource_tool",
        "ReadMcpResourceTool",
        "read_mcp_resource",
        "Read a resource exposed by a connected MCP server.",
    ),
)


def create_default_tool_registry(
    mcp_manager=None,
    *,
    include_network_tools: bool = True,
) -> ToolRegistry:
    """Return the default built-in tool registry."""
    registry = ToolRegistry()
    for spec in _CORE_TOOL_SPECS:
        registry.register(_LazyTool(spec))
    if include_network_tools:
        for spec in _NETWORK_TOOL_SPECS:
            registry.register(_LazyTool(spec))
    if mcp_manager is not None:
        for spec in _MCP_STATIC_TOOL_SPECS:
            registry.register(_LazyTool(spec, mcp_manager))
        for tool_info in mcp_manager.list_tools():
            from openharness.tools.mcp_tool import McpToolAdapter

            registry.register(McpToolAdapter(mcp_manager, tool_info))
    return registry


__all__ = [
    "BaseTool",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolResult",
    "create_default_tool_registry",
]
