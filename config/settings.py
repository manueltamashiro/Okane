"""
Global configuration and environment variable loading.
All env vars are loaded once at import time from .env via python-dotenv.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent / ".env")

# ---------------------------------------------------------------------------
# Alpaca
# ---------------------------------------------------------------------------
ALPACA_API_KEY: str = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY: str = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_PAPER: bool = os.getenv("ALPACA_PAPER", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_PATH: str = os.getenv("DB_PATH", "./data/trader_bot.db")
DB_URL: str = f"sqlite:///{DB_PATH}"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR: Path = Path(__file__).parent.parent
CONFIG_DIR: Path = BASE_DIR / "config"
DATA_DIR: Path = BASE_DIR / "data"
LOGS_DIR: Path = BASE_DIR / "logs"

LOGS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------
DEFAULT_SYMBOLS: list[str] = ["AAPL", "VTV", "VTI"]

# ---------------------------------------------------------------------------
# Risk constants — HARD CODED, NEVER OVERRIDE
# ---------------------------------------------------------------------------
MAX_RISK_PER_TRADE: float = 0.01       # 1% of account per trade
MAX_POSITION_SIZE_PCT: float = 0.05    # 5% of portfolio max per position
DAILY_LOSS_LIMIT: float = 0.03         # 3% → halt for the day
WEEKLY_LOSS_LIMIT: float = 0.05        # 5% → halt for the week
MAX_DRAWDOWN: float = 0.15             # 15% → halt ALL trading, manual review
MAX_CONCURRENT_POSITIONS: int = 5
MAX_SECTOR_EXPOSURE: int = 3           # max positions in same sector
VIX_BRAKE_THRESHOLD: float = 30.0     # halve position sizes above this
VIX_BRAKE_MULTIPLIER: float = 0.5

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE: Path = LOGS_DIR / "trader_bot.log"

# ---------------------------------------------------------------------------
# Validation — fail loudly if keys are missing in non-paper non-test mode
# ---------------------------------------------------------------------------
def _is_placeholder_credential(value: str) -> bool:
    """True if a credential is empty or still an .env.example placeholder
    (e.g. 'your_bot_token_here'). Single source of truth for 'is this real?'."""
    v = value.strip().lower()
    return (not v) or v.startswith("your_") or v.endswith("_here")


def telegram_configured() -> bool:
    """True when both Telegram credentials are present and not placeholders.

    Shared by make_notifier() (chooses TelegramNotifier vs NullNotifier) and the
    paper_trading CLI guard so both agree on what 'configured' means.
    """
    return not _is_placeholder_credential(TELEGRAM_BOT_TOKEN) and not _is_placeholder_credential(TELEGRAM_CHAT_ID)


def validate_config() -> None:
    """Raise if critical config is missing for live/paper trading."""
    missing = []
    if not ALPACA_API_KEY:
        missing.append("ALPACA_API_KEY")
    if not ALPACA_SECRET_KEY:
        missing.append("ALPACA_SECRET_KEY")
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Copy .env.example to .env and fill in your credentials."
        )
