"""Multi-agent pipeline for capturing and screening R&D funding calls."""
import os

# Keep everything local: turn off CrewAI's anonymous telemetry and AMP tracing
# before any CrewAI module is imported. setdefault lets a user override them.
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
