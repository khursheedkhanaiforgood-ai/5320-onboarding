import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
BUFFER_SIZE: int = int(os.getenv("BUFFER_SIZE", "4000"))
BRIDGE_TOKEN: str = os.getenv("BRIDGE_TOKEN", "changeme")
