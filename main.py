from __future__ import annotations

import argparse
import logging
import sys
import time

from common.config import load_settings
from common.ai import test_ai_connectivity
from common.telegram import test_telegram_ping

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("free-automations")


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Free-Automations Platform – Autonomous Tech Growth & Outreach Engine for Aman Kumar (@aman179102)."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--test-telegram", action="store_true", help="Send test ping to verify Telegram Bot configuration")
    group.add_argument("--test-ai", action="store_true", help="Test connectivity to Gemini, Groq, and OpenRouter AI models")
    group.add_argument("--hn-hiring", action="store_true", help="Run HN Who is Hiring radar & draft 100%% remote outreach pitches")
    group.add_argument("--hn-discussion", action="store_true", help="Find top Ask HN engineering questions and draft senior architecture answers")
    group.add_argument("--hn-showcase", action="store_true", help="Draft Show HN posts showcasing Aman's systems engineering projects")
    group.add_argument("--hn-all", action="store_true", help="Run full suite of Hacker News automations")
    group.add_argument("--itch-devlog", action="store_true", help="Generate/publish high-signal systems & simulation devlog to itch.io")
    group.add_argument("--itch-radar", action="store_true", help="Scan game jams filtering for AI, systems, tools, Python, Go, and Rust")
    group.add_argument("--itch-analytics", action="store_true", help="Track project views, downloads, and purchases on itch.io")
    group.add_argument("--itch-all", action="store_true", help="Run full suite of itch.io automations and send consolidated digest")
    group.add_argument("--qiita-article", action="store_true", help="Generate/publish high-signal Tech Japanese article to Qiita")
    group.add_argument("--qiita-radar", action="store_true", help="Scan Qiita tech trends & tag radar (#Python, #AI, #FastAPI, #Go, #Rust)")
    group.add_argument("--qiita-analytics", action="store_true", help="Track Qiita LGTM, Stocks, and Views with delta computation")
    group.add_argument("--qiita-all", action="store_true", help="Run full suite of Qiita automations and send consolidated digest")
    group.add_argument("--velog-article", action="store_true", help="Generate/publish high-signal Tech Korean article to Velog")
    group.add_argument("--velog-radar", action="store_true", help="Scan Velog tech trends & weekly trending radar")
    group.add_argument("--velog-analytics", action="store_true", help="Track Velog Likes, Comments, and Views with delta computation")
    group.add_argument("--velog-all", action="store_true", help="Run full suite of Velog automations and send consolidated digest")
    group.add_argument("--devto-article", action="store_true", help="Generate/publish deep-dive technical article to Dev.to")
    group.add_argument("--devto-radar", action="store_true", help="Scan Dev.to rising tech trend radar (#python, #architecture, #webdev, #ai, #devops)")
    group.add_argument("--devto-analytics", action="store_true", help="Track Dev.to Page Views, Reactions, and Comments with delta computation")
    group.add_argument("--devto-all", action="store_true", help="Run full suite of Dev.to automations and send consolidated digest")
    group.add_argument("--server", action="store_true", help="Launch lightweight HTTP health check and webhook server")

    parser.add_argument("--daemon", action="store_true", help="Run in continuous background loop")
    parser.add_argument("--interval", type=int, default=60, help="Daemon cycle interval in minutes (default: 60)")
    parser.add_argument("--dry-run", action="store_true", help="Run automation without sending messages to Telegram")
    parser.add_argument("--live", "--auto-publish", action="store_true", dest="live", help="Autonomously publish comments and Show HN posts live to Hacker News")
    parser.add_argument("--discussion-mode", choices=["all", "popular", "rising", "ask_hn"], default="all", help="Target discussion type (popular top stories vs fresh rising threads)")
    parser.add_argument("--port", type=int, default=None, help="Custom server port (overrides PORT env)")

    return parser


def run_hn_action(
    action: str,
    settings,
    dry_run: bool = False,
    auto_publish: bool = False,
    discussion_mode: str = "all",
) -> bool:
    """Invokes the HN automation runner if installed."""
    try:
        from automations.hn import runner
    except ImportError as exc:
        logger.error("HN automation module not available: %s", exc)
        print("⚠️  automations.hn is currently not installed or under construction.")
        return False

    dispatch_map = {
        "hiring": "run_hiring_radar",
        "discussion": "run_discussion",
        "showcase": "run_showcase",
        "all": "run_all",
    }
    func_name = dispatch_map.get(action)
    if not func_name or not hasattr(runner, func_name):
        logger.error("Runner method %s not found in automations.hn.runner", func_name)
        return False

    func = getattr(runner, func_name)
    try:
        logger.info(
            "Executing HN action '%s' (dry_run=%s, auto_publish=%s, discussion_mode=%s)...",
            action,
            dry_run,
            auto_publish,
            discussion_mode,
        )
        kwargs: dict[str, Any] = {"dry_run": dry_run}
        import inspect
        sig = inspect.signature(func)
        if "auto_publish" in sig.parameters:
            kwargs["auto_publish"] = auto_publish
        if "mode" in sig.parameters and action == "discussion":
            kwargs["mode"] = discussion_mode

        func(settings, **kwargs)
        return True
    except Exception as exc:
        logger.error("Failed to execute HN %s: %s", action, exc)
        return False


def run_itch_action(
    action: str,
    settings,
    dry_run: bool = False,
    auto_publish: bool = False,
) -> bool:
    """Invokes the itch.io automation runner."""
    try:
        from automations.itch import runner as itch_runner
    except ImportError as exc:
        logger.error("itch.io automation module not available: %s", exc)
        print("⚠️  automations.itch is currently not installed or under construction.")
        return False

    try:
        logger.info("Executing itch.io action '%s' (dry_run=%s, auto_publish=%s)...", action, dry_run, auto_publish)
        if action == "devlog":
            res = itch_runner.run_devlog(settings, dry_run=dry_run, force=auto_publish)
        elif action == "radar":
            res = itch_runner.run_radar(settings, dry_run=dry_run)
        elif action == "analytics":
            res = itch_runner.run_analytics(settings, dry_run=dry_run)
        elif action == "all":
            res = itch_runner.run_all(settings, dry_run=dry_run, force=auto_publish)
        else:
            return False
        return res.get("status") in ("success", "skipped")
    except Exception as exc:
        logger.error("Failed to execute itch %s: %s", action, exc)
        return False


def run_qiita_action(
    action: str,
    settings,
    dry_run: bool = False,
    auto_publish: bool = False,
) -> bool:
    """Invokes the Qiita automation runner."""
    try:
        from automations.qiita import runner as qiita_runner
    except ImportError as exc:
        logger.error("Qiita automation module not available: %s", exc)
        print("⚠️  automations.qiita is currently not installed or under construction.")
        return False

    try:
        logger.info("Executing Qiita action '%s' (dry_run=%s, auto_publish=%s)...", action, dry_run, auto_publish)
        if action == "article":
            res = qiita_runner.run_article(settings, dry_run=dry_run, force=auto_publish)
        elif action == "radar":
            res = qiita_runner.run_radar(settings, dry_run=dry_run)
        elif action == "analytics":
            res = qiita_runner.run_analytics(settings, dry_run=dry_run)
        elif action == "all":
            res = qiita_runner.run_all(settings, dry_run=dry_run, force=auto_publish)
        else:
            return False
        return res.get("status") in ("success", "skipped")
    except Exception as exc:
        logger.error("Failed to execute Qiita %s: %s", action, exc)
        return False


def run_velog_action(
    action: str,
    settings,
    dry_run: bool = False,
    auto_publish: bool = False,
) -> bool:
    """Invokes the Velog automation runner."""
    try:
        from automations.velog import runner as velog_runner
    except ImportError as exc:
        logger.error("Velog automation module not available: %s", exc)
        print("⚠️  automations.velog is currently not installed or under construction.")
        return False

    try:
        logger.info("Executing Velog action '%s' (dry_run=%s, auto_publish=%s)...", action, dry_run, auto_publish)
        if action == "article":
            res = velog_runner.run_article(settings, dry_run=dry_run, force=auto_publish)
        elif action == "radar":
            res = velog_runner.run_radar(settings, dry_run=dry_run)
        elif action == "analytics":
            res = velog_runner.run_analytics(settings, dry_run=dry_run)
        elif action == "all":
            res = velog_runner.run_all(settings, dry_run=dry_run, force=auto_publish)
        else:
            return False
        return res.get("status") in ("success", "published", "skipped", "cooldown", "dry_run")
    except Exception as exc:
        logger.error("Failed to execute Velog %s: %s", action, exc)
        return False


def run_devto_action(
    action: str,
    settings,
    dry_run: bool = False,
    auto_publish: bool = False,
) -> bool:
    """Invokes the Dev.to automation runner."""
    try:
        from automations.devto import runner as devto_runner
    except ImportError as exc:
        logger.error("Dev.to automation module not available: %s", exc)
        print("⚠️  automations.devto is currently not installed or under construction.")
        return False

    try:
        logger.info("Executing Dev.to action '%s' (dry_run=%s, auto_publish=%s)...", action, dry_run, auto_publish)
        if action == "article":
            res = devto_runner.run_article(settings, dry_run=dry_run, force=auto_publish)
        elif action == "radar":
            res = devto_runner.run_radar(settings, dry_run=dry_run)
        elif action == "analytics":
            res = devto_runner.run_analytics(settings, dry_run=dry_run)
        elif action == "all":
            res = devto_runner.run_all(settings, dry_run=dry_run, force=auto_publish)
        else:
            return False
        return res.get("status") in ("success", "published", "skipped", "cooldown", "dry_run")
    except Exception as exc:
        logger.error("Failed to execute Dev.to %s: %s", action, exc)
        return False


def main() -> int:
    parser = _cli_parser()
    args = parser.parse_args()
    settings = load_settings()
    if args.live:
        # Override settings with live publishing active
        object.__setattr__(settings, "hn_auto_publish", True) if hasattr(settings, "__dataclass_fields__") else None

    # 1. Test Telegram Ping
    if args.test_telegram:
        print("\n📲 Verifying Telegram Bot connectivity...")
        ok = test_telegram_ping(settings)
        return 0 if ok else 1

    # 2. Test AI Providers
    if args.test_ai:
        print("\n🤖 Testing AI Model Connectivity across providers...")
        results = test_ai_connectivity(settings)
        for provider, status in results.items():
            mark = "✅ ONLINE" if status else "❌ OFFLINE / UNCONFIGURED"
            print(f"  • {provider.capitalize():12s}: {mark}")
        all_passed = any(results.values())
        return 0 if all_passed else 1

    # 3. HTTP Server
    if args.server:
        from server import run_server
        print(f"\n🌐 Starting Free-Automations HTTP server on port {args.port or settings.port}...")
        run_server(port=args.port)
        return 0

    # 4. Daemon loop
    if args.daemon:
        interval_secs = max(args.interval * 60, 60)
        mode_label = "AUTONOMOUS LIVE AUTO-PILOT (NO BUTTONS)" if args.live else "DRY-RUN SIMULATION"
        logger.info("Starting background daemon. Mode: %s. Interval: %d minutes", mode_label, args.interval)
        try:
            while True:
                logger.info("--- Starting scheduled automation cycle ---")
                run_hn_action("all", settings, dry_run=args.dry_run, auto_publish=args.live, discussion_mode=args.discussion_mode)
                logger.info("Cycle complete. Sleeping for %d minutes...", args.interval)
                time.sleep(interval_secs)
        except KeyboardInterrupt:
            logger.info("Daemon stopped by user.")
            return 0

    # 5. Individual or All HN commands
    if args.hn_hiring:
        ok = run_hn_action("hiring", settings, dry_run=args.dry_run, auto_publish=args.live)
        return 0 if ok else 1

    if args.hn_discussion:
        ok = run_hn_action("discussion", settings, dry_run=args.dry_run, auto_publish=args.live, discussion_mode=args.discussion_mode)
        return 0 if ok else 1

    if args.hn_showcase:
        ok = run_hn_action("showcase", settings, dry_run=args.dry_run, auto_publish=args.live)
        return 0 if ok else 1

    if args.hn_all:
        ok = run_hn_action("all", settings, dry_run=args.dry_run, auto_publish=args.live, discussion_mode=args.discussion_mode)
        return 0 if ok else 1

    # 6. itch.io commands
    if args.itch_devlog:
        ok = run_itch_action("devlog", settings, dry_run=args.dry_run, auto_publish=args.live)
        return 0 if ok else 1

    if args.itch_radar:
        ok = run_itch_action("radar", settings, dry_run=args.dry_run, auto_publish=args.live)
        return 0 if ok else 1

    if args.itch_analytics:
        ok = run_itch_action("analytics", settings, dry_run=args.dry_run, auto_publish=args.live)
        return 0 if ok else 1

    if args.itch_all:
        ok = run_itch_action("all", settings, dry_run=args.dry_run, auto_publish=args.live)
        return 0 if ok else 1

    # 7. Qiita commands
    if args.qiita_article:
        ok = run_qiita_action("article", settings, dry_run=args.dry_run, auto_publish=args.live)
        return 0 if ok else 1

    if args.qiita_radar:
        ok = run_qiita_action("radar", settings, dry_run=args.dry_run, auto_publish=args.live)
        return 0 if ok else 1

    if args.qiita_analytics:
        ok = run_qiita_action("analytics", settings, dry_run=args.dry_run, auto_publish=args.live)
        return 0 if ok else 1

    if args.qiita_all:
        ok = run_qiita_action("all", settings, dry_run=args.dry_run, auto_publish=args.live)
        return 0 if ok else 1

    # 8. Velog commands
    if args.velog_article:
        ok = run_velog_action("article", settings, dry_run=args.dry_run, auto_publish=args.live)
        return 0 if ok else 1

    if args.velog_radar:
        ok = run_velog_action("radar", settings, dry_run=args.dry_run, auto_publish=args.live)
        return 0 if ok else 1

    if args.velog_analytics:
        ok = run_velog_action("analytics", settings, dry_run=args.dry_run, auto_publish=args.live)
        return 0 if ok else 1

    if args.velog_all:
        ok = run_velog_action("all", settings, dry_run=args.dry_run, auto_publish=args.live)
        return 0 if ok else 1

    # 9. Dev.to commands
    if args.devto_article:
        ok = run_devto_action("article", settings, dry_run=args.dry_run, auto_publish=args.live)
        return 0 if ok else 1

    if args.devto_radar:
        ok = run_devto_action("radar", settings, dry_run=args.dry_run, auto_publish=args.live)
        return 0 if ok else 1

    if args.devto_analytics:
        ok = run_devto_action("analytics", settings, dry_run=args.dry_run, auto_publish=args.live)
        return 0 if ok else 1

    if args.devto_all:
        ok = run_devto_action("all", settings, dry_run=args.dry_run, auto_publish=args.live)
        return 0 if ok else 1

    # Default if no arguments provided: print help
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
