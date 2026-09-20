"""Delivery orders for the rider app: who is on duty, what they are offered,
and the one rule that ties orders to fatigue.

    rider app --duty/accept/advance-->  OrderBook  <--offer()-- sources/order_simulator
                                           |
                                           +-- store.dispatch_block(rider)   (circuit breaker + signal health)

Order life cycle:
    offered --accept--> accepted --advance--> picked_up --advance--> delivered
       |--decline--> declined
       |--timeout--> expired
       |--breaker pauses the rider--> withdrawn

The fatigue rule (plan.md: the intervention point is dispatch):
  * a paused rider is never offered a new order, and an offer that is still
    waiting for an answer is withdrawn;
  * an order that was already accepted is NOT taken away — the rider finishes
    the delivery in hand and then rests;
  * a rider without a trustworthy detection signal (detector not connected, link
    lost, no face, camera fault) is not offered new orders either. An offer
    already on screen stays: a rider who looks over their shoulder for two
    seconds ("no face") must not lose the order they were about to accept.

No networking in here. Thread-safe: the HTTP threads, the 1 Hz ticker and the
order simulator all call in.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from ..config import Config
from .store import BLOCK_FATIGUE, RiderStore

ORDER_OFFERED = "offered"
ORDER_ACCEPTED = "accepted"
ORDER_PICKED_UP = "picked_up"
ORDER_DELIVERED = "delivered"
ORDER_DECLINED = "declined"
ORDER_EXPIRED = "expired"
ORDER_WITHDRAWN = "withdrawn"


class OrderError(Exception):
    """The request doesn't fit the current state (HTTP 409)."""


@dataclass
class Order:
    id: str
    rider_id: str
    restaurant: str
    pickup: str
    dropoff: str
    items: str
    distance_km: float
    fee: int
    state: str
    offered_at: float
    expires_at: float
    pickup_pos: "tuple | None" = None   # (lat, lng); None = this order source has no coordinates, app hides the map
    dropoff_pos: "tuple | None" = None

    def to_dict(self, now: float) -> dict:
        return {
            "id": self.id, "state": self.state, "restaurant": self.restaurant, "pickup": self.pickup,
            "dropoff": self.dropoff, "items": self.items, "distance_km": self.distance_km, "fee": self.fee,
            "pickup_pos": list(self.pickup_pos) if self.pickup_pos else None,
            "dropoff_pos": list(self.dropoff_pos) if self.dropoff_pos else None,
            "offered_at": round(self.offered_at, 3),
            "expires_in": round(max(0.0, self.expires_at - now), 1) if self.state == ORDER_OFFERED else None,
        }


class _Session:
    """One rider's app: duty switch, what is on their screen, today's totals."""

    def __init__(self):
        self.on_duty = False
        self.last_seen = None   # last time the app polled; a closed app gets no offers
        self.offer = None       # Order waiting for accept / decline
        self.order = None       # Order being delivered
        self.last_closed = None  # most recent offer that ended without being accepted (tells the app why)
        self.idle_since = None  # when the rider last became free; the simulator spaces offers from here
        self.delivered = 0
        self.earnings = 0


class OrderBook:
    def __init__(self, config: Config, store: RiderStore, clock=time.time):
        self.config = config
        self.store = store
        self._clock = clock
        self._lock = threading.RLock()
        self._sessions = {}
        self._seq = 0

    # ---- rider app (api layer) -----------------------------------------
    def app_state(self, rider_id: str) -> "dict | None":
        """Everything one phone needs, in one poll. None for an unknown rider."""
        with self._lock:
            rider = self.store.rider(rider_id)
            if rider is None:
                return None
            now = self._clock()
            session = self._session(rider_id)
            session.last_seen = now
            self._sweep(rider_id, session, now)
            return {
                "server_time": round(now, 3),
                "rider": rider,
                "on_duty": session.on_duty,
                "dispatch_blocked": self.store.dispatch_block(rider_id),
                "offer": session.offer.to_dict(now) if session.offer else None,
                "order": session.order.to_dict(now) if session.order else None,
                "last_closed": session.last_closed.to_dict(now) if session.last_closed else None,
                "stats": {"delivered": session.delivered, "earnings": session.earnings},
            }

    def set_duty(self, rider_id: str, on: bool) -> None:
        with self._lock:
            session = self._require(rider_id)
            if session.on_duty == on:
                return
            now = self._clock()
            session.on_duty = on
            session.idle_since = now
            if not on and session.offer is not None:
                self._close_offer(session, ORDER_WITHDRAWN)
            self.store.record_event(rider_id, "duty_on" if on else "duty_off")

    def answer_offer(self, rider_id: str, order_id: str, accept: bool) -> None:
        with self._lock:
            session = self._require(rider_id)
            self._sweep(rider_id, session, self._clock())
            offer = session.offer
            if offer is None or offer.id != order_id:
                raise OrderError("這張訂單已經不在了（逾時或已撤回）")
            if not accept:
                self._close_offer(session, ORDER_DECLINED)
                return
            offer.state = ORDER_ACCEPTED
            session.order, session.offer, session.last_closed = offer, None, None

    def advance(self, rider_id: str, order_id: str) -> None:
        """accepted -> picked_up -> delivered."""
        with self._lock:
            session = self._require(rider_id)
            order = session.order
            if order is None or order.id != order_id:
                raise OrderError("沒有進行中的這張訂單")
            if order.state == ORDER_ACCEPTED:
                order.state = ORDER_PICKED_UP
                return
            order.state = ORDER_DELIVERED
            session.delivered += 1
            session.earnings += order.fee
            session.order = None
            session.idle_since = self._clock()

    def current_order(self, rider_id: str) -> "Order | None":
        """What the rider's map should show: the delivery in hand, else the offer on screen."""
        with self._lock:
            session = self._sessions.get(rider_id)
            return None if session is None else (session.order or session.offer)

    def acknowledge_alert(self, rider_id: str, level: str) -> None:
        """The rider tapped "我知道了" on a fatigue alert: the dashboard's event
        log shows the warning actually reached a human."""
        with self._lock:
            self._require(rider_id)
            self.store.record_event(rider_id, f"alert_ack_{level}")

    # ---- order source (sources layer) ----------------------------------
    def riders_ready_for_offer(self) -> dict:
        """{rider id: seconds idle} for riders who are on duty, have the app
        alive, have nothing on screen and are not paused."""
        with self._lock:
            now = self._clock()
            ready = {}
            for rider_id, session in self._sessions.items():
                self._sweep(rider_id, session, now)
                app_alive = session.last_seen is not None and now - session.last_seen < self.config.app_alive_sec
                if (session.on_duty and app_alive and session.offer is None and session.order is None
                        and session.idle_since is not None and self.store.dispatch_block(rider_id) is None):
                    ready[rider_id] = now - session.idle_since
            return ready

    def offer(self, rider_id: str, restaurant: str, pickup: str, dropoff: str, items: str,
              distance_km: float, fee: int, pickup_pos=None, dropoff_pos=None) -> "Order | None":
        """Returns None when the rider can't take an offer right now — the
        fatigue / signal check lives here so no order source can bypass it."""
        with self._lock:
            session = self._sessions.get(rider_id)
            if (session is None or not session.on_duty or session.offer is not None or session.order is not None
                    or self.store.dispatch_block(rider_id) is not None):
                return None
            now = self._clock()
            self._seq += 1
            session.offer = Order(f"o-{self._seq}", rider_id, restaurant, pickup, dropoff, items, distance_km, fee,
                                  ORDER_OFFERED, now, now + self.config.offer_timeout_sec, pickup_pos, dropoff_pos)
            session.last_closed = None
            return session.offer

    def tick(self) -> None:
        """~1 Hz: offers time out and get withdrawn even if the phone is asleep."""
        with self._lock:
            now = self._clock()
            for rider_id, session in self._sessions.items():
                self._sweep(rider_id, session, now)

    # ---- internals ------------------------------------------------------
    def _session(self, rider_id: str) -> _Session:
        return self._sessions.setdefault(rider_id, _Session())

    def _require(self, rider_id: str) -> _Session:
        if self.store.rider(rider_id) is None:
            raise OrderError(f"unknown rider '{rider_id}'")
        return self._session(rider_id)

    def _sweep(self, rider_id: str, session: _Session, now: float) -> None:
        offer = session.offer
        if offer is None:
            return
        if self.store.dispatch_block(rider_id) == BLOCK_FATIGUE:
            self._close_offer(session, ORDER_WITHDRAWN)
            self.store.record_event(rider_id, "offer_withdrawn")
        elif now >= offer.expires_at:
            self._close_offer(session, ORDER_EXPIRED)

    def _close_offer(self, session: _Session, state: str) -> None:
        session.offer.state = state
        session.last_closed, session.offer = session.offer, None
        session.idle_since = self._clock()
