"""
itch.io Autonomous Game & Systems Engineering Engine.
Official API v1 integration for high-signal devlogs, AI simulation architectures,
game jam radar scanning, and views/downloads/purchases analytics.
"""

from automations.itch.analytics import (
    GameMetrics,
    ItchAnalyticsReport,
    format_analytics_digest,
    track_analytics,
)
from automations.itch.api import ItchAPIClient, ItchAPIError
from automations.itch.devlogs import (
    ItchDevlogCandidate,
    generate_itch_devlog,
    validate_devlog_content,
)
from automations.itch.publisher import ItchPublisher
from automations.itch.radar import (
    JamOpportunity,
    filter_unseen_jams,
    format_jams_digest,
    scan_game_jams,
)
from automations.itch.runner import (
    ItchRunner,
    run_all,
    run_analytics,
    run_devlog,
    run_radar,
)

__all__ = [
    "ItchAPIClient",
    "ItchAPIError",
    "ItchDevlogCandidate",
    "generate_itch_devlog",
    "validate_devlog_content",
    "JamOpportunity",
    "scan_game_jams",
    "filter_unseen_jams",
    "format_jams_digest",
    "GameMetrics",
    "ItchAnalyticsReport",
    "track_analytics",
    "format_analytics_digest",
    "ItchPublisher",
    "ItchRunner",
    "run_all",
    "run_devlog",
    "run_radar",
    "run_analytics",
]
