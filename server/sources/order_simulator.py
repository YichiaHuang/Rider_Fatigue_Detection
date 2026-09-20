"""Simulated merchant orders for the rider app (no real delivery-platform API
is involved — plan.md). Every rider who is on duty in the app and free gets an
offer after a short random wait. Whether a rider MAY be offered anything is not
decided here: OrderBook.offer() asks the circuit breaker.

Replacing this with a real order feed means writing another Source that calls
OrderBook.offer(); core/ and api/ stay as they are.
"""
from __future__ import annotations

import random
import threading

from ..config import Config
from ..core.orders import OrderBook
from .base import Source
from .routing import estimate_distance_m

# Made-up shops around the two campuses; no real brands, and the house numbers
# are invented. The coordinates are real points ON the named street (looked up
# in OpenStreetMap, then checked that the router snaps them to that same street)
# — a point a few metres into an alley makes the router take a 1 km detour.
RESTAURANTS = (
    ("阿明滷肉飯", "新竹市東區建功路 12 號", (24.79687, 121.00028), ("滷肉飯 ×2、燙青菜", "雞腿便當、味噌湯")),
    ("竹風鍋貼", "新竹市東區光復路二段 88 號", (24.79890, 120.99349), ("鍋貼 ×15、酸辣湯", "韭菜水餃 ×20")),
    ("小梅手搖", "新竹市東區大學路 51 號", (24.78932, 121.00153), ("珍珠奶茶 ×3", "四季春 ×2、冬瓜檸檬")),
    ("清夜鹹酥雞", "新竹市東區建新路 7 號", (24.79895, 120.99900), ("鹹酥雞、甜不辣、四季豆", "雞排 ×2")),
    ("南大門拉麵", "新竹市東區食品路 140 號", (24.79878, 120.97895), ("豚骨拉麵、煎餃", "味噌拉麵 ×2")),
    ("十八尖早午餐", "新竹市東區寶山路 30 號", (24.78527, 120.98946), ("總匯三明治、鮮奶茶", "蛋餅 ×2、豆漿")),
)
DROPOFFS = (
    ("清華大學 台達館", (24.79542, 120.99187)),
    ("陽明交大 工程三館", (24.78746, 120.99754)),
    ("新竹市東區金山街 45 號 3 樓", (24.77710, 121.02351)),
    ("科學園區 力行路 6 號 大廳", (24.77583, 121.02220)),
    ("新竹市東區關新路 27 號 12 樓", (24.78856, 121.02307)),
    ("新竹市東區光明新村 18 號", (24.79277, 120.99759)),
    ("新竹市東區慈雲路 118 號", (24.79734, 121.01331)),
)

class OrderSimulatorSource(Source):
    name = "order_simulator"

    def __init__(self, book: OrderBook, config: Config, seed=None):
        super().__init__(book.store)
        self.book = book
        self.gap_min, self.gap_max = config.order_gap_sec
        self.rng = random.Random(seed)
        self.offered = 0
        self._waits = {}  # rider id -> how long this rider idles before the next offer
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="order-simulator", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        return {"name": self.name, "offered": self.offered}

    def _run(self) -> None:
        while not self._stop.wait(1.0):
            self.step()

    def step(self) -> None:
        for rider_id, idle_sec in self.book.riders_ready_for_offer().items():
            wait = self._waits.setdefault(rider_id, self.rng.uniform(self.gap_min, self.gap_max))
            if idle_sec < wait:
                continue
            name, pickup, pickup_pos, menus = self.rng.choice(RESTAURANTS)
            dropoff, dropoff_pos = self.rng.choice(DROPOFFS)
            # Quoted before any route lookup; the app shows the routed distance once it has one.
            distance = round(max(0.5, estimate_distance_m(pickup_pos, dropoff_pos) / 1000.0), 1)
            fee = int(round(30 + distance * 12))
            if self.book.offer(rider_id, name, pickup, dropoff, self.rng.choice(menus), distance, fee,
                               pickup_pos, dropoff_pos):
                self.offered += 1
                del self._waits[rider_id]
