"""Claude API integration — analyzes console output and advises the human operator."""
import json
from dataclasses import dataclass
from pathlib import Path

import anthropic

from .state_machine import SwitchState, OSContext


@dataclass
class OperatorInstruction:
    action: str              # What the human should do
    command: str | None      # Exact command to type (if applicable)
    explanation: str         # Why (one sentence)
    wait: bool               # Should operator just wait and watch?
    physical: bool           # Requires physical action (e.g. plug cable, press button)?


class ConsoleAnalyzer:
    """Calls Claude API to interpret switch state and generate operator instructions."""

    MAX_HISTORY = 10  # conversation turns to keep
    DEBOUNCE_SECONDS = 3.0  # minimum seconds between API calls

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._history: list[dict] = []
        self._system_prompt = self._load_system_prompt()
        self._last_call_time = 0.0

    def _load_system_prompt(self) -> str:
        prompt_path = Path(__file__).parent / "prompts" / "system_prompt.md"
        if prompt_path.exists():
            return prompt_path.read_text()
        return DEFAULT_SYSTEM_PROMPT

    def analyze(
        self,
        console_buffer: str,
        current_state: SwitchState,
        os_context: OSContext,
        time_in_state: float,
        boot_complete: bool,
    ) -> OperatorInstruction:
        """Call Claude API and return operator instruction."""
        import time
        now = time.monotonic()
        if now - self._last_call_time < self.DEBOUNCE_SECONDS:
            # Return a safe default during debounce
            return OperatorInstruction(
                action="Wait",
                command=None,
                explanation="Processing...",
                wait=True,
                physical=False,
            )
        self._last_call_time = now

        user_message = self._build_user_message(
            console_buffer, current_state, os_context, time_in_state, boot_complete
        )

        self._history.append({"role": "user", "content": user_message})
        if len(self._history) > self.MAX_HISTORY * 2:
            self._history = self._history[-self.MAX_HISTORY * 2:]

        response = self._client.messages.create(
            model=self._model,
            max_tokens=512,
            system=self._system_prompt,
            messages=self._history,
        )

        reply = response.content[0].text
        self._history.append({"role": "assistant", "content": reply})

        return self._parse_response(reply)

    def _build_user_message(
        self,
        console_buffer: str,
        state: SwitchState,
        os_context: OSContext,
        time_in_state: float,
        boot_complete: bool,
    ) -> str:
        return (
            f"CURRENT STATE: {state.name}\n"
            f"OS CONTEXT: {os_context.name}\n"
            f"TIME IN STATE: {time_in_state:.0f}s\n"
            f"BOOT COMPLETE: {boot_complete}\n"
            f"\n=== CONSOLE OUTPUT (last ~4000 chars) ===\n"
            f"{console_buffer}\n"
            f"=== END CONSOLE OUTPUT ===\n\n"
            f"What should the operator do next? "
            f"Reply in this exact JSON format:\n"
            f'{{"action": "...", "command": "...or null", "explanation": "...", "wait": true/false, "physical": true/false}}'
        )

    def _parse_response(self, reply: str) -> OperatorInstruction:
        """Parse JSON from Claude response, with fallback."""
        try:
            # Extract JSON from response (Claude may add surrounding text)
            start = reply.find('{')
            end = reply.rfind('}') + 1
            if start >= 0 and end > start:
                data = json.loads(reply[start:end])
                return OperatorInstruction(
                    action=data.get("action", "Wait and observe"),
                    command=data.get("command") or None,
                    explanation=data.get("explanation", ""),
                    wait=bool(data.get("wait", False)),
                    physical=bool(data.get("physical", False)),
                )
        except (json.JSONDecodeError, KeyError):
            pass
        # Fallback: return raw reply as action
        return OperatorInstruction(
            action=reply[:200],
            command=None,
            explanation="",
            wait=True,
            physical=False,
        )


DEFAULT_SYSTEM_PROMPT = """You are an expert Extreme Networks 5320 switch onboarding specialist.
See agent/prompts/system_prompt.md for the full prompt."""
