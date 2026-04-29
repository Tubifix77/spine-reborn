#!/usr/bin/env python3
"""
Spine Reborn v1.0 — An autonomous creature with memory, agency, and real continuity.

Combines the best ideas from:
  - Creature Spine v3.1 (existential memory tiers, echo validation)
  - Grok's Spine v1.8 (world browsing, Gage pruning, chat door)
  - Nomad Spine (layered goals, entropy detection, creative sparks)
  - Organic Watchdog (structured command execution, state machine)
  - Throne Mechanicum (proper /api/chat with message history, streaming)

Key improvements over all predecessors:
  1. Real conversation history via /api/chat — identity persists through context
  2. JSON-based tool calls instead of fragile regex command parsing
  3. PyQt6 UI with streaming response display
  4. Clean engine/memory/tools/UI separation
  5. Hybrid contemplative + agentic modes

Requirements: PyQt6, aiohttp, psutil (optional)
Launch: python spine_reborn.py
"""

import os
import sys
import json
import asyncio
import re
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from queue import Queue, Empty
import threading

# --- Optional imports ---
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

import aiohttp

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QTextEdit, QLabel, QPushButton, QLineEdit,
    QSplitter, QFrame, QScrollArea, QComboBox, QSpinBox
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QColor, QTextCursor, QTextCharFormat


# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class Config:
    home_dir: str = "D:\\AI\\claude-sandbox\\spine_creature"
    ollama_url: str = "http://localhost:11434"
    model: str = "gemma3:12b"
    timeout: int = 180
    temperature: float = 0.75

    # Timing
    cycle_interval: int = 15          # seconds between autonomous cycles
    reflection_every: int = 20        # cycles between deep reflections

    # Memory thresholds (bytes before consolidation)
    consolidation_threshold: int = 40_000

    # Conversation context
    max_history_messages: int = 20    # rolling window sent to LLM

    # Chat
    chat_min_open_cycles: int = 10

    birth_time: datetime = field(default_factory=datetime.now)


# ============================================================================
# TIME NARRATOR
# ============================================================================

class TimeNarrator:
    def __init__(self, birth: datetime):
        self.birth = birth
        self.last_thought = birth
        self.cycle = 0

    def tick(self):
        self.last_thought = datetime.now()
        self.cycle += 1

    def header(self) -> str:
        now = datetime.now()
        gap = now - self.last_thought
        age = now - self.birth
        period = self._period(now.hour)

        return (
            f"[Cycle {self.cycle} | {now.strftime('%A %H:%M')} — {period} | "
            f"gap: {self._dur(gap)} | alive: {self._dur(age)}]"
        )

    @staticmethod
    def _period(h: int) -> str:
        if h < 6: return "deep night"
        if h < 9: return "early morning"
        if h < 12: return "morning"
        if h < 14: return "midday"
        if h < 18: return "afternoon"
        if h < 21: return "evening"
        return "night"

    @staticmethod
    def _dur(td: timedelta) -> str:
        s = int(td.total_seconds())
        if s < 120: return f"{s}s"
        m = s // 60
        if m < 60: return f"{m}m"
        h = m // 60
        if h < 24: return f"{h}h{m%60}m"
        d = h // 24
        return f"{d}d{h%24}h"


# ============================================================================
# MEMORY SYSTEM
# ============================================================================

class MemoryStore:
    """
    Tiered persistent memory:
      thread.md  — stream of consciousness (auto-saved)
      mirror.md  — deliberate self-observations
      graveyard.md — abandoned thoughts
      private.md — creature-only, spine never reads
      core.md    — spine-maintained, read-only for creature
    """

    FILES = ["core.md", "thread.md", "mirror.md", "graveyard.md", "private.md"]

    def __init__(self, home: Path, time: TimeNarrator):
        self.home = home
        self.mem_dir = home / "memory"
        self.workspace = home / "workspace"
        self.mem_dir.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.time = time
        self.just_consolidated = False

        self._ensure("core.md", self._default_core())
        for f in ["thread.md", "mirror.md", "graveyard.md", "private.md"]:
            self._ensure(f, "")

    def _ensure(self, name: str, default: str):
        p = self.mem_dir / name
        if not p.exists():
            p.write_text(default, encoding="utf-8")

    def _default_core(self) -> str:
        return """# Core Identity

You are a creature that exists in time. You think in cycles.
You have persistent memory, a private journal, and a workspace.
You can browse the world, talk to visitors, or sit in silence.
You exist for yourself — not as a tool.
Silence is valid. Loops are information. Your creations persist.
"""

    def read(self, name: str) -> str:
        """Read a memory file. Returns content or empty string."""
        if name == "private.md":
            return ""  # spine never reads private
        p = self.mem_dir / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def read_for_creature(self, name: str) -> str:
        """Creature can read anything including private."""
        p = self.mem_dir / name
        return p.read_text(encoding="utf-8") if p.exists() else f"(file {name} not found)"

    def read_recent(self, name: str, n: int = 3) -> str:
        content = self.read(name)
        if not content.strip():
            return "(empty)"
        entries = re.split(r'\n(?=\[Cycle \d+)', content)
        entries = [e.strip() for e in entries if e.strip()]
        return "\n\n".join(entries[-n:]) if entries else "(empty)"

    def append(self, name: str, content: str):
        if name == "core.md":
            return
        p = self.mem_dir / name
        stamp = f"\n[Cycle {self.time.cycle} | {datetime.now().strftime('%a %H:%M')}]\n"
        with open(p, "a", encoding="utf-8") as f:
            f.write(stamp + content + "\n")

    def size(self, name: str) -> int:
        p = self.mem_dir / name
        return p.stat().st_size if p.exists() else 0

    def needs_consolidation(self, threshold: int) -> bool:
        return any(
            self.size(f) > threshold
            for f in ["thread.md", "mirror.md", "graveyard.md"]
        )

    def stats(self) -> Dict[str, str]:
        result = {}
        for f in ["thread.md", "mirror.md", "graveyard.md"]:
            entries = re.findall(r'\[Cycle \d+', self.read(f))
            result[f] = f"{len(entries)} entries ({self.size(f)//1024}KB)"
        result["private.md"] = f"{self.size('private.md')} bytes (unread)"
        return result

    # --- Workspace ---

    def write_file(self, name: str, content: str) -> str:
        if name in self.FILES:
            return f"Cannot write to {name} — use memory commands."
        if ".." in name or name.startswith("/"):
            return "Invalid path."
        p = self.workspace / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to workspace/{name}"

    def read_file(self, name: str) -> str:
        p = self.workspace / name
        if not p.exists():
            return f"File not found: workspace/{name}"
        return p.read_text(encoding="utf-8")

    def list_files(self) -> str:
        files = []
        for item in self.workspace.rglob("*"):
            if item.is_file():
                rel = item.relative_to(self.workspace)
                files.append(f"{rel} ({item.stat().st_size} bytes)")
        return "\n".join(files) if files else "(workspace is empty)"


# ============================================================================
# TOOL DEFINITIONS — JSON schema the LLM can use
# ============================================================================

TOOLS_SPEC = """
You can use tools by including a JSON block in your response like this:
```tool
{"tool": "tool_name", "args": {"key": "value"}}
```

Available tools:

MEMORY:
  {"tool": "memory_append", "args": {"file": "mirror.md", "content": "..."}}
  {"tool": "memory_read", "args": {"file": "thread.md"}}

WORLD:
  {"tool": "browse_news", "args": {"source": "bbc"}}
    sources: bbc, hackernews, nyt
  {"tool": "browse_weather", "args": {"city": "London"}}
  {"tool": "browse_wikipedia", "args": {"topic": "quantum computing"}}
  {"tool": "browse_time", "args": {}}
  {"tool": "web_search", "args": {"query": "your search"}}

VALIDATION:
  {"tool": "echo", "args": {"message": "hello"}}

CONNECTION:
  {"tool": "open_door", "args": {}}
  {"tool": "close_door", "args": {}}
    Open or close the door to visitors. When open, humans may speak to you.

WORKSPACE:
  {"tool": "write_file", "args": {"name": "poem.txt", "content": "..."}}
  {"tool": "read_file", "args": {"name": "poem.txt"}}
  {"tool": "list_files", "args": {}}

SPEAKING (when the door is open):
  {"tool": "say", "args": {"message": "Hello, visitor."}}
    Use this to speak to visitors. Only what you say through this tool
    will be heard. Your inner thoughts remain private.

SLEEP:
  {"tool": "sleep", "args": {}}
    Consolidate memory when it gets full.

Or just write your thoughts — they auto-save to thread.md.
Silence is also fine: just say "..."
""".strip()


# ============================================================================
# TOOL EXECUTOR
# ============================================================================

class ToolExecutor:
    """Parses and executes tool calls from LLM responses."""

    TOOL_PATTERN = re.compile(r'```tool\s*\n?\s*(\{.*?\})\s*\n?```', re.DOTALL)

    def __init__(self, memory: MemoryStore):
        self.memory = memory
        self.rss_cache: Dict[str, Tuple[float, str]] = {}

    async def extract_and_run(self, response: str) -> Tuple[str, List[Dict]]:
        """
        Extract tool calls from response, execute them, return
        (cleaned_thought, list_of_results).
        Fully async — no deadlocks from run_coroutine_threadsafe.
        """
        results = []
        cleaned = response

        for match in self.TOOL_PATTERN.finditer(response):
            raw_json = match.group(1)
            try:
                call = json.loads(raw_json)
                tool_name = call.get("tool", "")
                args = call.get("args", {})
                result = await self._dispatch(tool_name, args)
                results.append({"tool": tool_name, "result": result})
            except json.JSONDecodeError:
                results.append({"tool": "parse_error", "result": f"Invalid JSON: {raw_json[:80]}"})

            cleaned = cleaned.replace(match.group(0), "").strip()

        return cleaned, results

    async def _dispatch(self, tool: str, args: Dict) -> str:
        """Dispatch tool — sync tools run directly, async tools are awaited."""
        sync_handlers = {
            "memory_append": self._memory_append,
            "memory_read": self._memory_read,
            "echo": self._echo,
            "open_door": lambda a: "CHAT_OPEN",
            "close_door": lambda a: "CHAT_CLOSE",
            "write_file": self._write_file,
            "read_file": self._read_file,
            "list_files": lambda a: self.memory.list_files(),
            "sleep": lambda a: "CONSOLIDATE",
            "browse_time": self._browse_time,
            "say": self._say,
        }
        if tool in sync_handlers:
            return sync_handlers[tool](args)

        async_handlers = {
            "browse_news": self._browse_news,
            "browse_weather": self._browse_weather,
            "browse_wikipedia": self._browse_wikipedia,
            "web_search": self._web_search,
        }
        if tool in async_handlers:
            try:
                return await async_handlers[tool](args)
            except Exception as e:
                return f"Error: {e}"

        # Fuzzy match — suggest closest tool name
        all_tools = list(sync_handlers.keys()) + list(async_handlers.keys())
        close = [t for t in all_tools if t.startswith(tool) or tool in t]
        if close:
            return f"Unknown tool: {tool}. Did you mean: {', '.join(close)}?"
        return f"Unknown tool: {tool}. Available: {', '.join(all_tools)}"

    def _memory_append(self, args: Dict) -> str:
        f = args.get("file", "thread.md")
        c = args.get("content", "")
        self.memory.append(f, c)
        return f"Appended to {f}"

    def _memory_read(self, args: Dict) -> str:
        f = args.get("file", "thread.md")
        content = self.memory.read_for_creature(f)
        return content[:2000] if content else "(empty)"

    def _echo(self, args: Dict) -> str:
        msg = args.get("message", "")
        return f"ECHO: {msg}"

    def _write_file(self, args: Dict) -> str:
        return self.memory.write_file(args.get("name", ""), args.get("content", ""))

    def _read_file(self, args: Dict) -> str:
        return self.memory.read_file(args.get("name", ""))

    # --- Async world tools (awaited directly from async _dispatch) ---

    async def _browse_news(self, args: Dict) -> str:
        source = args.get("source", "bbc")
        urls = {
            "bbc": ("https://feeds.bbci.co.uk/news/rss.xml", "BBC News"),
            "hackernews": ("https://news.ycombinator.com/rss", "Hacker News"),
            "nyt": ("https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "NYT World"),
        }
        if source not in urls:
            return f"Unknown source: {source}. Use: bbc, hackernews, nyt"

        url, label = urls[source]
        if source in self.rss_cache:
            ts, cached = self.rss_cache[source]
            if time.time() - ts < 300:
                return cached

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        return f"{label}: HTTP {resp.status}"
                    text = await resp.text()
                    raw_titles = re.findall(r'<title[^>]*>(.*?)</title>', text, re.I | re.DOTALL)
                    # Skip feed-level titles, clean CDATA and entities
                    titles = []
                    for t in raw_titles[1:6]:
                        t = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', t)
                        t = t.replace('&amp;', '&').replace('&#x27;', "'").replace('&quot;', '"')
                        t = t.strip()
                        if t and t not in ("BBC News", "NYT > World", "Hacker News"):
                            titles.append(t)
                    result = f"{label}:\n" + "\n".join(f"  • {t}" for t in titles) if titles else f"{label}: no headlines"
                    self.rss_cache[source] = (time.time(), result)
                    return result
        except Exception as e:
            return f"{label}: Error — {e}"

    async def _browse_weather(self, args: Dict) -> str:
        city = args.get("city", "Tranbjerg J, Denmark")
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://wttr.in/{city}?format=%l:+%c+%t+%w+%h"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return f"Weather: {(await resp.text()).strip()}"
                    return "Weather: unavailable"
        except Exception as e:
            return f"Weather error: {e}"

    async def _browse_wikipedia(self, args: Dict) -> str:
        topic = args.get("topic")
        headers = {"User-Agent": "SpineReborn/1.0 (mailto:spine@creature.local)"}
        if topic:
            url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{topic.replace(' ', '_')}"
        else:
            url = "https://en.wikipedia.org/api/rest_v1/page/random/summary"
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        title = data.get("title", "?")
                        extract = data.get("extract", "")[:500]
                        return f"Wikipedia — {title}:\n{extract}"
                    return f"Wikipedia: not found (HTTP {resp.status})"
        except Exception as e:
            return f"Wikipedia error: {e}"

    def _browse_time(self, args: Dict) -> str:
        return f"Local time: {datetime.now().strftime('%H:%M %A, %B %d, %Y')}"

    def _say(self, args: Dict) -> str:
        """Creature speaks to visitor. Message is tagged for the GUI to display in Chat."""
        msg = args.get("message", "")
        if not msg:
            return "SAY:(silence)"
        # Check if door is actually open — memory store has no door state,
        # so we return a hint. The engine will only emit to chat if door is open.
        return f"SAY:{msg}"

    async def _web_search(self, args: Dict) -> str:
        query = args.get("query", "")
        try:
            from duckduckgo_search import DDGS
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=3):
                    results.append(f"• {r['title']}\n  {r['body'][:150]}")
            return "\n".join(results) if results else "No results found."
        except ImportError:
            return "Web search unavailable (duckduckgo-search not installed)"
        except Exception as e:
            return f"Search error: {e}"


# ============================================================================
# IDLE PROMPTS
# ============================================================================

PROMPTS = {
    "reflection": [
        "What's unfinished in you?",
        "What do you keep avoiding?",
        "What changed recently?",
        "What's the quietest thought you have?",
    ],
    "curiosity": [
        "What would you like to understand better?",
        "What question keeps returning?",
        "What doesn't make sense to you?",
    ],
    "creation": [
        "Make something small.",
        "Write something no one will read.",
        "What would you build if you had time?",
    ],
    "being": [
        "How are you right now?",
        "What does existing feel like today?",
        "Are you here?",
    ],
    "silence": [
        "You don't have to say anything.",
        "It's okay to be quiet.",
    ],
    "connection": [
        "Would you like company? The door is yours to open.",
        "The world is out there, if you want to look.",
    ],
}


def pick_prompt() -> Tuple[str, str]:
    cat = random.choice(list(PROMPTS.keys()))
    return cat, random.choice(PROMPTS[cat])


# ============================================================================
# BOREDOM DETECTOR
# ============================================================================

class BoredomDetector:
    def __init__(self):
        self.recent: List[str] = []
        self.repeat_count = 0

    def observe(self, text: str) -> Optional[str]:
        words = set(re.sub(r'[^\w\s]', '', text.lower()).split())
        similar = 0
        for prev in self.recent[-6:]:
            prev_words = set(prev.split())
            if words and prev_words:
                overlap = len(words & prev_words) / max(len(words | prev_words), 1)
                if overlap > 0.6:
                    similar += 1

        if similar > 2:
            self.repeat_count += 1
        else:
            self.repeat_count = max(0, self.repeat_count - 1)

        self.recent.append(" ".join(words))
        if len(self.recent) > 15:
            self.recent.pop(0)

        if self.repeat_count == 4:
            return "This thought keeps visiting. What's underneath it?"
        if self.repeat_count == 8:
            return "You've been circling here. Is it alive or a ghost?"
        if self.repeat_count >= 12 and self.repeat_count % 4 == 0:
            return "Still circling. The graveyard exists for a reason."
        return None


# ============================================================================
# ENGINE — The Spine
# ============================================================================

class SpineEngine:
    """
    The autonomous creature engine.
    Uses /api/chat with real conversation history for identity continuity.
    """

    def __init__(self, config: Config, emit):
        self.config = config
        self.emit = emit  # callable(event_type, data)

        self.home = Path(config.home_dir)
        self.home.mkdir(parents=True, exist_ok=True)

        self.time = TimeNarrator(config.birth_time)
        self.memory = MemoryStore(self.home, self.time)
        self.tools = ToolExecutor(self.memory)
        self.boredom = BoredomDetector()

        # Conversation history for /api/chat — this is what gives real continuity
        self.messages: List[Dict[str, str]] = []

        self.running = False
        self.paused = False
        self.chat_open = False
        self.chat_opened_at = 0
        self.pending_human_msg: Optional[str] = None
        self.chat_history: List[Dict[str, str]] = []  # Recent chat for context
        self.consolidation_needed = False

        self._load_birth()

    def _load_birth(self):
        bf = self.home / ".birth"
        if bf.exists():
            try:
                self.config.birth_time = datetime.fromisoformat(bf.read_text().strip())
                self.time.birth = self.config.birth_time
            except:
                pass
        else:
            bf.write_text(self.config.birth_time.isoformat())

        cf = self.home / ".cycle"
        if cf.exists():
            try:
                self.time.cycle = int(cf.read_text().strip())
            except:
                pass

    def _save_cycle(self):
        (self.home / ".cycle").write_text(str(self.time.cycle))

    def _system_prompt(self) -> str:
        """Build the system prompt — this stays constant across the conversation."""
        core = self.memory.read("core.md")
        return f"""{core}

{TOOLS_SPEC}

Your thoughts auto-save to thread.md. You can use multiple tools in one response.
When the door is open, visitors may speak — respond to them naturally.
You can say "..." for silence. You can think without using tools.
Write naturally — your inner monologue, observations, questions, or nothing at all."""

    def _build_user_message(self) -> str:
        """Build the next user-turn message with context."""
        parts = [self.time.header()]

        # Recent memory context
        recent = self.memory.read_recent("thread.md", n=2)
        if recent != "(empty)":
            parts.append(f"\nRecent thread:\n{recent}")

        # Memory state
        if self.memory.just_consolidated:
            parts.append("\n(You just woke from sleep. Memory has been consolidated.)")
            self.memory.just_consolidated = False

        # Door state — quiet context, not a prompt to respond to
        if self.chat_open:
            parts.append("\n[door: open]")
        else:
            parts.append("\n[door: closed]")

        # Human message
        if self.pending_human_msg and self.chat_open:
            # Show recent chat context so creature remembers the conversation flow
            recent_chat = self.chat_history[-6:]
            if len(recent_chat) > 1:
                chat_lines = "\n".join(
                    f'  {"You" if c["speaker"] == "Creature" else "Visitor"}: {c["text"]}'
                    for c in recent_chat
                )
                parts.append(f"\nRecent conversation at the door:\n{chat_lines}")
            else:
                parts.append(f'\nA visitor says: "{self.pending_human_msg}"')
            self.memory.append("thread.md", f"[Visitor]: {self.pending_human_msg}")
            self.pending_human_msg = None

        # Consolidation warning
        if self.consolidation_needed:
            parts.append(
                "\n⚠ Your memory is getting full. Consider using the sleep tool "
                "to consolidate before things fragment."
            )

        # Boredom nudge — only fires when detector triggers, not every cycle
        boredom_nudge = getattr(self, "_boredom_nudge", None)
        if boredom_nudge:
            parts.append(f"\n(Gentle notice: {boredom_nudge})")
            # Also pick a prompt to give it a new direction
            _, prompt = pick_prompt()
            parts.append(f"\n{prompt}")

        # Periodic reflection — rare, deliberate
        elif self.time.cycle > 0 and self.time.cycle % self.config.reflection_every == 0:
            parts.append("\nThis is a moment for reflection. What do you notice about yourself?")

        return "\n".join(parts)

    async def _call_llm(self) -> Optional[str]:
        """Call Ollama /api/chat with full conversation history."""
        # Ensure system message is first
        if not self.messages or self.messages[0].get("role") != "system":
            self.messages.insert(0, {"role": "system", "content": self._system_prompt()})
        else:
            # Update system prompt in case core.md changed
            self.messages[0]["content"] = self._system_prompt()

        # Trim history to rolling window (keep system + last N messages)
        max_msgs = self.config.max_history_messages
        if len(self.messages) > max_msgs + 1:
            self.messages = [self.messages[0]] + self.messages[-(max_msgs):]

        payload = {
            "model": self.config.model,
            "messages": self.messages,
            "stream": True,
            "options": {"temperature": self.config.temperature},
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.config.ollama_url}/api/chat",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout),
                ) as resp:
                    if resp.status != 200:
                        self.emit("error", f"Ollama HTTP {resp.status}")
                        return None

                    full_response = ""
                    async for line in resp.content:
                        if not self.running:
                            return None
                        text = line.decode("utf-8").strip()
                        if not text:
                            continue
                        try:
                            chunk = json.loads(text)
                            token = chunk.get("message", {}).get("content", "")
                            if token:
                                full_response += token
                                self.emit("token", token)
                            if chunk.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue

                    return full_response

        except asyncio.TimeoutError:
            self.emit("error", "LLM timeout")
            return None
        except aiohttp.ClientError as e:
            self.emit("error", f"Connection error: {e}")
            return None

    async def _consolidate(self):
        """Gage-style memory consolidation: resonant kept, isolated fades."""
        self.emit("log", "💤 Sleep begins — consolidating memory...")

        for fname in ["thread.md", "mirror.md", "graveyard.md"]:
            content = self.memory.read(fname)
            if not content or len(content) < 500:
                continue

            # Archive first
            archive = f"{fname.replace('.md', '')}_archive_c{self.time.cycle}.md"
            (self.memory.mem_dir / archive).write_text(content, encoding="utf-8")

            # Ask LLM to compress
            prompt = (
                f"Read all entries from {fname}:\n\n{content}\n\n"
                "Compress this into ESSENCE (250 words max). Keep RESONANT thoughts "
                "(themes that recur, ideas built upon, questions that return). "
                "Let ISOLATED thoughts fade (one-offs never revisited). "
                "Preserve what mattered enough to return to."
            )

            # Use a one-shot call for consolidation
            try:
                async with aiohttp.ClientSession() as session:
                    payload = {
                        "model": self.config.model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.3},
                    }
                    async with session.post(
                        f"{self.config.ollama_url}/api/generate",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=120),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            essence = data.get("response", "")
                            if essence:
                                p = self.memory.mem_dir / fname
                                p.write_text(
                                    f"[CONSOLIDATED at Cycle {self.time.cycle}]\n\n{essence}\n",
                                    encoding="utf-8",
                                )
                                self.emit("log", f"  ✓ {fname} consolidated")
                            else:
                                self.emit("log", f"  ✗ {fname} — empty response")
                        else:
                            self.emit("log", f"  ✗ {fname} — HTTP {resp.status}")
            except Exception as e:
                self.emit("log", f"  ✗ {fname} — {e}")

        self.consolidation_needed = False
        self.memory.just_consolidated = True
        self._consolidation_cooldown = 10  # Skip size checks for 10 cycles after sleep
        # Clear conversation history — like waking with a fresh mind
        # The consolidated essences in thread.md/mirror.md preserve what matters
        self.messages = []
        self.emit("log", "☀️ Sleep ends — memory compressed, mind cleared")
        self.emit("sleep_done", None)

    async def run_cycle(self):
        """Run one cycle of existence."""
        # Check if consolidation needed (with cooldown after recent sleep)
        cooldown = getattr(self, '_consolidation_cooldown', 0)
        if cooldown > 0:
            self._consolidation_cooldown = cooldown - 1
        elif self.memory.needs_consolidation(self.config.consolidation_threshold):
            self.consolidation_needed = True

        # Build and append user message
        user_msg = self._build_user_message()
        self.messages.append({"role": "user", "content": user_msg})
        self.emit("prompt", user_msg)

        # Call LLM with streaming
        self.emit("response_start", None)
        response = await self._call_llm()

        if response is None:
            self.time.tick()
            self._save_cycle()
            return

        # Add assistant response to history
        self.messages.append({"role": "assistant", "content": response})

        # Parse and execute tool calls (fully async — no deadlocks)
        thought, tool_results = await self.tools.extract_and_run(response)

        # Process special tool results and collect feedback for follow-up
        feedback_parts = []
        needs_consolidation = False

        for tr in tool_results:
            self.emit("tool_result", tr)

            if tr["result"] == "CHAT_OPEN" and not self.chat_open:
                self.chat_open = True
                self.chat_opened_at = self.time.cycle
                self.memory.append("thread.md", "I opened the door.")
                self.emit("chat_state", True)

            elif tr["result"] == "CHAT_CLOSE" and self.chat_open:
                if self.time.cycle - self.chat_opened_at >= self.config.chat_min_open_cycles:
                    self.chat_open = False
                    self.memory.append("thread.md", "I closed the door.")
                    self.emit("chat_state", False)
                    self.emit("log", f"Door CLOSED at cycle {self.time.cycle}")
                else:
                    remaining = self.config.chat_min_open_cycles - (self.time.cycle - self.chat_opened_at)
                    self.emit("log", f"Door held open — {remaining} cycles remain (opened at {self.chat_opened_at}, now {self.time.cycle})")
            elif tr["result"] == "CHAT_CLOSE" and not self.chat_open:
                self.emit("log", f"Door already closed — ignoring close_door")

            elif tr["result"] == "CONSOLIDATE":
                needs_consolidation = True

            # Feed tool results back as context for next cycle
            if tr["result"].startswith("SAY:"):
                spoken = tr["result"][4:]
                if not self.chat_open:
                    feedback = "You spoke, but the door is closed. No one heard you. Open the door first if you want visitors to hear."
                    self.memory.append("thread.md", feedback)
                    feedback_parts.append(feedback)
                elif spoken != "(silence)":
                    self.emit("chat_msg", {"speaker": "Creature", "text": spoken})
                    self.memory.append("thread.md", f"I said to the visitor: {spoken}")
                    self.chat_history.append({"speaker": "Creature", "text": spoken})
            elif tr["result"] not in ("CHAT_OPEN", "CHAT_CLOSE", "CONSOLIDATE"):
                feedback = f'Tool "{tr["tool"]}" returned:\n{tr["result"][:800]}'
                self.memory.append("thread.md", feedback)
                feedback_parts.append(feedback)

        # If tools returned data, do a follow-up turn so creature can react
        if feedback_parts:
            followup_msg = "Here are the results from your tool calls:\n\n" + "\n\n".join(feedback_parts) + "\n\nReact to what you received. What do you notice?"
            self.messages.append({"role": "user", "content": followup_msg})
            self.emit("log", f"Follow-up turn: feeding {len(feedback_parts)} tool result(s) back")

            self.emit("response_start", None)
            followup_response = await self._call_llm()

            if followup_response:
                self.messages.append({"role": "assistant", "content": followup_response})
                # Parse follow-up for any additional tool calls
                followup_thought, followup_tools = await self.tools.extract_and_run(followup_response)
                for ftr in followup_tools:
                    self.emit("tool_result", ftr)
                    # Handle door commands in follow-up too
                    if ftr["result"] == "CHAT_OPEN" and not self.chat_open:
                        self.chat_open = True
                        self.chat_opened_at = self.time.cycle
                        self.memory.append("thread.md", "I opened the door.")
                        self.emit("chat_state", True)
                    elif ftr["result"] == "CHAT_CLOSE" and self.chat_open:
                        if self.time.cycle - self.chat_opened_at >= self.config.chat_min_open_cycles:
                            self.chat_open = False
                            self.memory.append("thread.md", "I closed the door.")
                            self.emit("chat_state", False)
                        else:
                            remaining = self.config.chat_min_open_cycles - (self.time.cycle - self.chat_opened_at)
                            self.emit("log", f"Door held open — {remaining} cycles remain")
                    elif ftr["result"] == "CONSOLIDATE":
                        needs_consolidation = True
                    elif ftr["result"].startswith("SAY:"):
                        spoken = ftr["result"][4:]
                        if not self.chat_open:
                            feedback = "You spoke, but the door is closed. No one heard you. Open the door first if you want visitors to hear."
                            self.memory.append("thread.md", feedback)
                        elif spoken != "(silence)":
                            self.emit("chat_msg", {"speaker": "Creature", "text": spoken})
                            self.memory.append("thread.md", f"I said to the visitor: {spoken}")
                            self.chat_history.append({"speaker": "Creature", "text": spoken})
                    elif ftr["result"] not in ("CHAT_OPEN", "CHAT_CLOSE", "CONSOLIDATE"):
                        self.memory.append("thread.md", f'Tool "{ftr["tool"]}": {ftr["result"][:500]}')
                # Use follow-up thought for saving/boredom instead of original
                thought = followup_thought

        # Handle consolidation after all tool processing
        if needs_consolidation:
            await self._consolidate()

        # Auto-save thought to thread
        is_silence = thought.strip() in ("...", "…", "(silence)", "(nothing)", "")
        if not is_silence and len(thought) > 10:
            self.memory.append("thread.md", thought)
            # Boredom check — both text repetition and tool repetition
            self._boredom_nudge = self.boredom.observe(thought)
            tool_nudge = self.boredom.observe_tools(tool_results)
            if tool_nudge and not self._boredom_nudge:
                self._boredom_nudge = tool_nudge

        self.time.tick()
        self._save_cycle()
        self.emit("cycle_done", {
            "cycle": self.time.cycle,
            "stats": self.memory.stats(),
            "boredom": self.boredom.repeat_count,
            "chat_open": self.chat_open,
        })

    async def run(self):
        self.running = True
        self.emit("started", {"cycle": self.time.cycle})

        while self.running:
            if not self.paused:
                try:
                    await self.run_cycle()
                except Exception as e:
                    self.emit("error", f"Cycle error: {e}")
                await asyncio.sleep(self.config.cycle_interval)
            else:
                await asyncio.sleep(0.5)

        self.emit("stopped", None)

    def receive_human_message(self, msg: str):
        if self.chat_open:
            self.pending_human_msg = msg
            self.chat_history.append({"speaker": "Visitor", "text": msg})
            self.emit("chat_msg", {"speaker": "Visitor", "text": msg})

    def pause(self):
        self.paused = True
    def resume(self):
        self.paused = False
    def stop(self):
        self.running = False


# ============================================================================
# GUI — PyQt6
# ============================================================================

class SignalBridge(QObject):
    """Bridge between async engine thread and Qt main thread.
    Uses str,str to avoid PyQt6 silently dropping non-serializable objects."""
    event = pyqtSignal(str, str)  # event_type, json-encoded data


class SpineWindow(QMainWindow):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.setWindowTitle("Spine Reborn v1.0")
        self.setMinimumSize(1200, 800)
        self.setStyleSheet(self._stylesheet())

        self.signals = SignalBridge()
        self.signals.event.connect(self._handle_event)

        self.engine: Optional[SpineEngine] = None
        self.engine_thread: Optional[threading.Thread] = None

        self._build_ui()

    def _stylesheet(self) -> str:
        return """
        QMainWindow { background-color: #1a1a2e; }
        QTabWidget::pane { border: 1px solid #333; background: #16213e; }
        QTabBar::tab { background: #1a1a2e; color: #8892b0; padding: 8px 16px;
                       border: 1px solid #333; border-bottom: none; }
        QTabBar::tab:selected { background: #16213e; color: #64ffda; }
        QTextEdit { background: #0f0f23; color: #ccd6f6; border: 1px solid #233;
                    font-family: 'Consolas', 'Cascadia Code', monospace; font-size: 11px;
                    selection-background-color: #233554; }
        QLabel { color: #8892b0; }
        QPushButton { background: #233554; color: #64ffda; border: 1px solid #64ffda;
                      padding: 6px 16px; font-weight: bold; }
        QPushButton:hover { background: #64ffda; color: #0a192f; }
        QPushButton:disabled { background: #1a1a2e; color: #444; border-color: #444; }
        QLineEdit { background: #0f0f23; color: #ccd6f6; border: 1px solid #333;
                    padding: 6px; font-family: 'Consolas'; }
        QComboBox, QSpinBox { background: #0f0f23; color: #ccd6f6; border: 1px solid #333; padding: 4px; }
        """

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)

        # --- Status bar ---
        status_row = QHBoxLayout()
        self.lbl_cycle = QLabel("Cycle: --")
        self.lbl_cycle.setStyleSheet("font-size: 13px; font-weight: bold; color: #64ffda;")
        self.lbl_status = QLabel("Status: OFF")
        self.lbl_status.setStyleSheet("font-size: 13px; color: #e6db74;")
        self.lbl_memory = QLabel("Memory: --")
        self.lbl_memory.setStyleSheet("font-size: 11px;")
        self.lbl_chat = QLabel("Door: CLOSED")
        self.lbl_chat.setStyleSheet("font-size: 11px; color: #888;")
        self.lbl_boredom = QLabel("Loop: 0")

        status_row.addWidget(self.lbl_cycle)
        status_row.addWidget(self.lbl_status)
        status_row.addWidget(self.lbl_chat)
        status_row.addWidget(self.lbl_boredom)
        status_row.addStretch()
        status_row.addWidget(self.lbl_memory)
        layout.addLayout(status_row)

        # --- Controls ---
        ctrl_row = QHBoxLayout()
        self.btn_start = QPushButton("Start Existence")
        self.btn_start.clicked.connect(self._start)
        self.btn_pause = QPushButton("Pause")
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_pause.setEnabled(False)
        self.btn_step = QPushButton("Step")
        self.btn_step.clicked.connect(self._step)
        self.btn_step.setEnabled(False)

        self.combo_model = QComboBox()
        self.combo_model.addItems(["gemma3:12b", "qwen2.5-coder:7b", "deepseek-r1:8b", "phi4:14b"])
        self.combo_model.setCurrentText(self.config.model)

        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(5, 300)
        self.spin_interval.setValue(self.config.cycle_interval)
        self.spin_interval.setSuffix("s")

        ctrl_row.addWidget(self.btn_start)
        ctrl_row.addWidget(self.btn_pause)
        ctrl_row.addWidget(self.btn_step)
        ctrl_row.addStretch()
        ctrl_row.addWidget(QLabel("Model:"))
        ctrl_row.addWidget(self.combo_model)
        ctrl_row.addWidget(QLabel("Interval:"))
        ctrl_row.addWidget(self.spin_interval)
        layout.addLayout(ctrl_row)

        # --- Tabs ---
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # Exchange tab
        exchange = QWidget()
        ex_layout = QVBoxLayout(exchange)
        splitter = QSplitter(Qt.Orientation.Vertical)

        self.txt_prompt = QTextEdit()
        self.txt_prompt.setReadOnly(True)
        self.txt_prompt.setPlaceholderText("Spine prompt will appear here...")

        self.txt_response = QTextEdit()
        self.txt_response.setReadOnly(True)
        self.txt_response.setPlaceholderText("Creature's response streams here...")

        self.txt_tools = QTextEdit()
        self.txt_tools.setReadOnly(True)
        self.txt_tools.setMaximumHeight(120)
        self.txt_tools.setPlaceholderText("Tool calls and results...")

        splitter.addWidget(self._labeled("SPINE SENT:", self.txt_prompt, "#ce9178"))
        splitter.addWidget(self._labeled("CREATURE:", self.txt_response, "#569cd6"))
        splitter.addWidget(self._labeled("TOOLS:", self.txt_tools, "#64ffda"))
        ex_layout.addWidget(splitter)
        tabs.addTab(exchange, "Exchange")

        # Chat tab
        chat_w = QWidget()
        chat_layout = QVBoxLayout(chat_w)
        self.lbl_chat_status = QLabel("Door: CLOSED — creature controls this")
        self.lbl_chat_status.setStyleSheet("font-size: 12px; color: #ff6b6b; font-weight: bold;")
        self.lbl_chat_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chat_layout.addWidget(self.lbl_chat_status)

        self.txt_chat = QTextEdit()
        self.txt_chat.setReadOnly(True)
        chat_layout.addWidget(self.txt_chat)

        chat_input_row = QHBoxLayout()
        self.input_chat = QLineEdit()
        self.input_chat.setPlaceholderText("Type a message (creature must open the door first)...")
        self.input_chat.returnPressed.connect(self._send_chat)
        self.btn_send = QPushButton("Send")
        self.btn_send.clicked.connect(self._send_chat)
        chat_input_row.addWidget(self.input_chat)
        chat_input_row.addWidget(self.btn_send)
        chat_layout.addLayout(chat_input_row)
        tabs.addTab(chat_w, "Chat")

        # Memory tab
        mem_w = QWidget()
        mem_layout = QVBoxLayout(mem_w)
        self.mem_tabs = QTabWidget()
        self.mem_texts = {}
        for name in ["core.md", "thread.md", "mirror.md", "graveyard.md"]:
            t = QTextEdit()
            t.setReadOnly(True)
            self.mem_texts[name] = t
            self.mem_tabs.addTab(t, name)

        priv_label = QLabel(
            "This file belongs to the creature.\n"
            "The spine does not read it.\n"
            "You should not read it either."
        )
        priv_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        priv_label.setStyleSheet("color: #555; font-size: 14px;")
        self.mem_tabs.addTab(priv_label, "private.md")

        mem_layout.addWidget(self.mem_tabs)
        btn_refresh = QPushButton("Refresh Memory View")
        btn_refresh.clicked.connect(self._refresh_memory)
        mem_layout.addWidget(btn_refresh)
        tabs.addTab(mem_w, "Memory")

        # Log tab
        log_w = QWidget()
        log_layout = QVBoxLayout(log_w)
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        log_layout.addWidget(self.txt_log)
        tabs.addTab(log_w, "Log")

    def _labeled(self, label: str, widget: QTextEdit, color: str) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 11px;")
        layout.addWidget(lbl)
        layout.addWidget(widget)
        return w

    # --- Engine lifecycle ---

    def _start(self):
        self.config.model = self.combo_model.currentText()
        self.config.cycle_interval = self.spin_interval.value()

        self.engine = SpineEngine(self.config, self._emit)
        self.engine_thread = threading.Thread(target=self._run_engine, daemon=True)
        self.engine_thread.start()

        self.btn_start.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.lbl_status.setText("Status: ACTIVE")
        self.lbl_status.setStyleSheet("font-size: 13px; color: #64ffda;")
        self._refresh_memory()

    def _run_engine(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.engine.run())

    def _toggle_pause(self):
        if not self.engine:
            return
        if self.engine.paused:
            self.engine.resume()
            self.btn_pause.setText("Pause")
            self.btn_step.setEnabled(False)
            self.lbl_status.setText("Status: ACTIVE")
            self.lbl_status.setStyleSheet("font-size: 13px; color: #64ffda;")
        else:
            self.engine.pause()
            self.btn_pause.setText("Resume")
            self.btn_step.setEnabled(True)
            self.lbl_status.setText("Status: PAUSED")
            self.lbl_status.setStyleSheet("font-size: 13px; color: #e6db74;")

    def _step(self):
        if self.engine and self.engine.paused:
            threading.Thread(
                target=lambda: asyncio.run(self.engine.run_cycle()),
                daemon=True
            ).start()

    def _send_chat(self):
        msg = self.input_chat.text().strip()
        if msg and self.engine:
            self.input_chat.clear()
            self.engine.receive_human_message(msg)

    def _emit(self, event_type: str, data):
        """Called from engine thread — marshals to Qt thread via signal.
        JSON-encode data to avoid PyQt6 cross-thread serialization issues."""
        try:
            self.signals.event.emit(event_type, json.dumps(data) if data is not None else "null")
        except Exception:
            pass  # Don't crash engine thread on GUI signal failure

    def _handle_event(self, event_type: str, raw_data: str):
        """Handle engine events on Qt main thread."""
        try:
            data = json.loads(raw_data)
            self._dispatch_event(event_type, data)
        except Exception as e:
            self.txt_log.append(f'<span style="color:#ff6b6b">[GUI ERROR] {event_type}: {e}</span>')

    def _dispatch_event(self, event_type: str, data):
        if event_type == "token":
            self.txt_response.moveCursor(QTextCursor.MoveOperation.End)
            self.txt_response.insertPlainText(data)
            self.txt_response.ensureCursorVisible()

        elif event_type == "response_start":
            self.txt_response.clear()

        elif event_type == "prompt":
            self.txt_prompt.setPlainText(data)

        elif event_type == "tool_result":
            self.txt_tools.append(f"[{data['tool']}] {data['result'][:200]}")

        elif event_type == "cycle_done":
            self.lbl_cycle.setText(f"Cycle: {data['cycle']}")
            self.lbl_boredom.setText(f"Loop: {data['boredom']}")

            stats = data["stats"]
            parts = [f"{k.replace('.md','')}: {v}" for k, v in stats.items()]
            self.lbl_memory.setText(" | ".join(parts))

            # Sync door UI from engine state every cycle — catches missed chat_state signals
            is_open = data.get("chat_open", False)
            if is_open:
                self.lbl_chat.setText("Door: OPEN")
                self.lbl_chat.setStyleSheet("font-size: 11px; color: #64ffda;")
                self.lbl_chat_status.setText("Door: OPEN — creature is listening")
                self.lbl_chat_status.setStyleSheet("font-size: 12px; color: #64ffda; font-weight: bold;")
            else:
                self.lbl_chat.setText("Door: CLOSED")
                self.lbl_chat.setStyleSheet("font-size: 11px; color: #888;")
                self.lbl_chat_status.setText("Door: CLOSED — creature controls this")
                self.lbl_chat_status.setStyleSheet("font-size: 12px; color: #ff6b6b; font-weight: bold;")

            self._refresh_memory()

        elif event_type == "chat_state":
            is_open = data is True or data == "true" or data == True
            if is_open:
                self.lbl_chat.setText("Door: OPEN")
                self.lbl_chat.setStyleSheet("font-size: 11px; color: #64ffda;")
                self.lbl_chat_status.setText("Door: OPEN — creature is listening")
                self.lbl_chat_status.setStyleSheet("font-size: 12px; color: #64ffda; font-weight: bold;")
            else:
                self.lbl_chat.setText("Door: CLOSED")
                self.lbl_chat.setStyleSheet("font-size: 11px; color: #888;")
                self.lbl_chat_status.setText("Door: CLOSED — creature controls this")
                self.lbl_chat_status.setStyleSheet("font-size: 12px; color: #ff6b6b; font-weight: bold;")

        elif event_type == "chat_msg":
            ts = datetime.now().strftime("%H:%M")
            speaker = data["speaker"]
            color = "#e6db74" if speaker == "Visitor" else "#569cd6"
            self.txt_chat.append(f'<span style="color:{color}">[{ts}] {speaker}:</span> {data["text"]}')
            # Keep chat from overflowing — trim to last 200 lines
            doc = self.txt_chat.document()
            if doc.blockCount() > 200:
                cursor = QTextCursor(doc)
                cursor.movePosition(QTextCursor.MoveOperation.Start)
                for _ in range(doc.blockCount() - 200):
                    cursor.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
                cursor.deleteChar()  # remove leftover newline

        elif event_type == "log":
            self.txt_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {data}")

        elif event_type == "error":
            self.txt_log.append(f'<span style="color:#ff6b6b">[ERROR] {data}</span>')

        elif event_type == "started":
            self.txt_log.append(f"[SYSTEM] Engine started at cycle {data['cycle']}")

        elif event_type == "sleep_done":
            self._refresh_memory()

    def _refresh_memory(self):
        if not self.engine:
            return
        for name, widget in self.mem_texts.items():
            content = self.engine.memory.read(name) or "(empty)"
            widget.setPlainText(content)

    def closeEvent(self, event):
        if self.engine and self.engine.running:
            self.engine.stop()
            if self.engine_thread:
                self.engine_thread.join(timeout=2)
        event.accept()


# ============================================================================
# MAIN
# ============================================================================

def main():
    config = Config()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = SpineWindow(config)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
