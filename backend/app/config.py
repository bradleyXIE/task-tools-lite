import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tasktools:tasktools@127.0.0.1:5440/tasktools")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5-5")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
MAX_STEPS = int(os.getenv("MAX_STEPS", "30"))
SCRIPT_TIMEOUT = int(os.getenv("SCRIPT_TIMEOUT", "10"))
HTTP_TOOL_TIMEOUT = int(os.getenv("HTTP_TOOL_TIMEOUT", "15"))
TOOL_OUTPUT_CAP = int(os.getenv("TOOL_OUTPUT_CAP", "20000"))
