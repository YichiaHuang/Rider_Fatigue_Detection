"""All tunables in one place. Nothing else in server/ hard-codes a port,
threshold, topic or rider id — change it here (or via CLI flags / env vars in
server/__main__.py) and every layer follows.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SOURCE_REAL = "real"
SOURCE_SIMULATED = "simulated"


@dataclass(frozen=True)
class RiderSpec:
    id: str
    name: str
    source: str  # SOURCE_REAL | SOURCE_SIMULATED — shown on the dashboard, never hidden
    profile: str = "steady"  # simulator profile; ignored for real riders


DEFAULT_RIDERS = (
    RiderSpec("rider-01", "騎手 01", SOURCE_REAL),
    RiderSpec("rider-02", "騎手 02", SOURCE_SIMULATED, "steady"),
    RiderSpec("rider-03", "騎手 03", SOURCE_SIMULATED, "rising"),
    RiderSpec("rider-04", "騎手 04", SOURCE_SIMULATED, "yawny"),
    RiderSpec("rider-05", "騎手 05", SOURCE_SIMULATED, "dropout"),
)


@dataclass
class Config:
    # --- web server (this laptop) ---
    http_host: str = "0.0.0.0"
    http_port: int = 8000

    # --- MQTT broker (plan.md 4.4). Empty host = don't connect. ---
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_topic_root: str = "riders"  # riders/{id}/fatigue_score etc.

    # --- boards' MJPEG stream servers (rider/stream_server.py on each i.MX93):
    #     rider id -> base URL. Riders not listed here simply have no video. ---
    board_urls: dict = field(default_factory=lambda: {"rider-01": "http://100.71.95.53:8080"})
    board_token: str = ""  # forwarded as X-Token on mode switches

    # --- multi-board: a rider id we've never heard of that starts publishing is
    #     added to the dashboard as a real device instead of being rejected ---
    auto_register: bool = True
    max_riders: int = 24

    # --- dispatch circuit breaker. Starting values from plan.md 4.4; must be
    #     re-tuned after the Stage A time-scale fix. ---
    pause_threshold: float = 15.0
    resume_threshold: float = 8.0

    # --- link health: how long without a score before we stop trusting it ---
    stale_after_sec: float = 5.0     # -> status "unknown", last score greyed
    offline_after_sec: float = 15.0  # -> link "offline"

    # --- history kept in memory per rider, and chart scale ---
    history_seconds: float = 600.0
    score_display_max: float = 30.0

    # --- simulator ---
    run_simulator: bool = True
    simulate_real_rider: bool = False  # dev only: fake rider-01 while the board is off
    simulator_hz: float = 1.0

    riders: tuple = field(default_factory=lambda: DEFAULT_RIDERS)

    def public_dict(self) -> dict:
        """What the browser is allowed to know (GET /api/config)."""
        return {
            "pause_threshold": self.pause_threshold,
            "resume_threshold": self.resume_threshold,
            "stale_after_sec": self.stale_after_sec,
            "offline_after_sec": self.offline_after_sec,
            "history_seconds": self.history_seconds,
            "score_display_max": self.score_display_max,
            "simulate_real_rider": self.simulate_real_rider,
            "mqtt_enabled": bool(self.mqtt_host),
        }
