"""Configuration loader for the 5320 onboarding agent."""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    api_key: str
    serial_port: str | None  # None = auto-detect
    baud_rate: int
    model: str
    buffer_size: int
    verbose: bool = False


def load_config(port: str | None = None, verbose: bool = False) -> Config:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY not set. Copy .env.example to .env and add your key."
        )
    return Config(
        api_key=api_key,
        serial_port=port or os.getenv("SERIAL_PORT") or None,
        baud_rate=int(os.getenv("SERIAL_BAUD", "115200")),
        model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
        buffer_size=int(os.getenv("BUFFER_SIZE", "4000")),
        verbose=verbose,
    )
