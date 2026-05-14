"""Application entry point — delegates to the pipeline orchestrator.

Usage:
    ai-knowledge-base --sources github,rss --limit 20
    ai-knowledge-base --sources github --limit 5 --dry-run
"""

import asyncio
import sys

from pipeline.pipeline import main as pipeline_main


def main() -> None:
    """Entry point for the ``ai-knowledge-base`` CLI command."""
    sys.exit(asyncio.run(pipeline_main()))


if __name__ == "__main__":
    main()
