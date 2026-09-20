"""Rider app: order life cycle, the fatigue/dispatch rule, and the /api/app routes."""
import json
import threading
import unittest
import urllib.error
import urllib.request

from server.api.http_server import build_server
from server.config import Config
from server.core.models import ScoreSample
from server.core.orders import OrderBook, OrderError
from server.core.store import RiderStore
from server.sources.order_simulator import OrderSimulatorSource
from server.sources.routing import RoutePlanner


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class OrderBookTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.config = Config()
        self.store = RiderStore(self.config, clock=self.clock)
        self.book = OrderBook(self.config, self.store, clock=self.clock)
        self.rider = "rider-01"
        self.score(3)  # a live detector: without one nobody is offered anything (see test_no_signal_*)

    def wait(self, seconds, score=3):
        """Let time pass with the detector still reporting (a silent one goes stale after 5 s)."""
        self.clock.now += seconds
        self.score(score)

    def score(self, value):
        self.store.ingest_score(ScoreSample(self.rider, self.clock.now, value))

    def offer(self):
        return self.book.offer(self.rider, "店", "取餐地", "送達地", "餐點", 2.0, 54)

    def state(self):
        return self.book.app_state(self.rider)

    def test_full_delivery(self):
        self.assertIsNone(self.offer(), "off duty: no offers")
        self.book.set_duty(self.rider, True)
        order = self.offer()
        self.assertEqual(self.state()["offer"]["id"], order.id)
        self.assertIsNone(self.offer(), "one offer at a time")
        self.book.answer_offer(self.rider, order.id, True)
        self.assertEqual((self.state()["offer"], self.state()["order"]["state"]), (None, "accepted"))
        self.book.advance(self.rider, order.id)
        self.assertEqual(self.state()["order"]["state"], "picked_up")
        self.book.advance(self.rider, order.id)
        state = self.state()
        self.assertEqual((state["order"], state["stats"]), (None, {"delivered": 1, "earnings": 54}))

    def test_offer_expires(self):
        self.book.set_duty(self.rider, True)
        order = self.offer()
        self.wait(self.config.offer_timeout_sec + 1)
        self.book.tick()
        state = self.state()
        self.assertEqual((state["offer"], state["last_closed"]["state"]), (None, "expired"))
        with self.assertRaises(OrderError):
            self.book.answer_offer(self.rider, order.id, True)

    def test_fatigue_pause_blocks_and_withdraws_offers(self):
        self.book.set_duty(self.rider, True)
        self.score(3)
        self.offer()
        self.score(16)  # breaker pauses
        state = self.state()
        self.assertEqual((state["dispatch_blocked"], state["offer"], state["last_closed"]["state"]),
                         ("fatigue", None, "withdrawn"))
        self.assertIsNone(self.offer(), "paused rider must not be offered orders")
        self.assertEqual(self.book.riders_ready_for_offer(), {})
        self.assertIn("offer_withdrawn", [e["kind"] for e in self.store.snapshot()["events"]])
        self.score(12)  # between thresholds: still paused
        self.assertIsNone(self.offer())
        self.wait(10, score=7)
        self.assertEqual(self.state()["dispatch_blocked"], "fatigue", "low score, but only 10 s of the 60 s rest served")
        self.assertIsNone(self.offer())
        self.assertEqual(self.state()["rider"]["rest_remaining_sec"], 50.0)
        self.wait(51, score=7)
        self.assertIsNone(self.state()["dispatch_blocked"])
        self.assertIsNotNone(self.offer())

    def test_no_signal_means_no_new_orders(self):
        self.book.set_duty(self.rider, True)
        self.state()
        self.assertIn(self.rider, self.book.riders_ready_for_offer())
        self.clock.now += self.config.stale_after_sec + 1  # detector went quiet
        self.state()
        self.assertEqual((self.state()["dispatch_blocked"], self.book.riders_ready_for_offer()), ("no_signal", {}))
        self.assertIsNone(self.offer())
        self.score(3)
        self.assertIsNotNone(self.offer(), "signal back: orders again")

    def test_rider_without_a_detector_is_never_offered_orders(self):
        self.book.set_duty("rider-02", True)  # on duty in the app, but no board has ever reported for them
        self.assertEqual(self.book.app_state("rider-02")["dispatch_blocked"], "no_signal")
        self.assertIsNone(self.book.offer("rider-02", "店", "取餐地", "送達地", "餐點", 2.0, 54))

    def test_no_face_blocks_new_offers_but_keeps_the_one_on_screen(self):
        from server.core.models import HealthSample
        self.book.set_duty(self.rider, True)
        order = self.offer()
        self.store.ingest_health(HealthSample(self.rider, self.clock.now, "no_face"))  # looked over the shoulder
        state = self.state()
        self.assertEqual((state["dispatch_blocked"], state["offer"]["id"]), ("no_signal", order.id))
        self.book.answer_offer(self.rider, order.id, True)
        self.assertEqual(self.state()["order"]["state"], "accepted")

    def test_accepted_order_survives_a_pause(self):
        self.book.set_duty(self.rider, True)
        order = self.offer()
        self.book.answer_offer(self.rider, order.id, True)
        self.score(20)
        self.assertEqual(self.state()["order"]["state"], "accepted", "finish the delivery in hand, then rest")
        self.book.advance(self.rider, order.id)
        self.book.advance(self.rider, order.id)
        self.assertEqual(self.state()["stats"]["delivered"], 1)

    def test_closed_app_gets_no_offers(self):
        self.book.set_duty(self.rider, True)
        self.state()
        self.assertIn(self.rider, self.book.riders_ready_for_offer())
        self.wait(self.config.app_alive_sec + 1)  # detector fine, but the phone stopped polling
        self.assertEqual(self.book.riders_ready_for_offer(), {})

    def test_simulator_offers_after_the_gap(self):
        simulator = OrderSimulatorSource(self.book, self.config, seed=1)
        self.book.set_duty(self.rider, True)
        self.state()
        simulator.step()
        self.assertIsNone(self.state()["offer"], "not before the minimum gap")
        self.wait(self.config.order_gap_sec[1])
        self.state()
        simulator.step()
        self.assertEqual(self.state()["offer"]["state"], "offered")

    def test_ack_lands_in_the_event_log(self):
        self.book.acknowledge_alert(self.rider, "paused")
        self.assertEqual(self.store.snapshot()["events"][-1]["kind"], "alert_ack_paused")
        with self.assertRaises(OrderError):
            self.book.set_duty("nobody", True)


class RoutePlannerTest(unittest.TestCase):
    POINTS = [(24.7995, 120.9990), (24.7957, 120.9920)]
    OSRM_ANSWER = {"code": "Ok", "routes": [{
        "distance": 1438.6, "duration": 297.4,
        "legs": [{"distance": 1438.6, "duration": 297.4, "steps": [
            {"name": "建功路", "distance": 1400.0, "maneuver": {"type": "depart", "modifier": "right", "location": [120.9990, 24.7995]}},
            {"name": "光復路二段", "distance": 38.6, "maneuver": {"type": "turn", "modifier": "left", "location": [120.9950, 24.7970]}},
            {"name": "", "distance": 0, "maneuver": {"type": "arrive", "location": [120.9920, 24.7957]}}]}],
        "geometry": {"coordinates": [[120.9990, 24.7995], [120.9950, 24.7970], [120.9920, 24.7957]]}}]}

    def test_osrm_answer_is_converted_and_cached(self):
        urls = []
        planner = RoutePlanner("https://osrm.example", fetch=lambda url, timeout: urls.append(url) or self.OSRM_ANSWER)
        route = planner.plan(self.POINTS)
        self.assertIn("/route/v1/driving/120.99900,24.79950;120.99200,24.79570?", urls[0], "OSRM wants lng,lat")
        self.assertEqual((route["source"], route["distance_m"], route["legs"][0]["duration_s"]), ("osrm", 1439, 297))
        self.assertEqual(route["geometry"][1], [24.7970, 120.9950], "geometry is handed to the app as lat,lng")
        self.assertIn("steps=true", urls[0])
        self.assertEqual(route["steps"][1], {"leg": 0, "type": "turn", "modifier": "left", "name": "光復路二段", "exit": None,
                                             "distance_m": 39, "location": [24.7970, 120.9950]})
        planner.plan(self.POINTS)
        self.assertEqual(len(urls), 1, "same trip again must come from the cache")

    def test_unreachable_server_falls_back_and_backs_off(self):
        clock = FakeClock()
        calls = []

        def failing(url, timeout):
            calls.append(url)
            raise TimeoutError()

        planner = RoutePlanner("https://osrm.example", fetch=failing, clock=clock)
        route = planner.plan(self.POINTS)
        self.assertEqual((route["source"], route["geometry"]), ("estimate", [list(p) for p in self.POINTS]))
        self.assertTrue(900 < route["distance_m"] < 1300, route["distance_m"])
        self.assertEqual([s["type"] for s in route["steps"]], ["depart", "arrive"], "no road, no turns to announce")
        planner.plan(self.POINTS)
        self.assertEqual(len(calls), 1, "don't pay one timeout per poll while the venue Wi-Fi is down")
        clock.now += 31
        planner.plan(self.POINTS)
        self.assertEqual(len(calls), 2)

    def test_no_server_configured_never_goes_online(self):
        planner = RoutePlanner("", fetch=lambda url, timeout: self.fail("must not fetch"))
        self.assertEqual(planner.plan(self.POINTS)["source"], "estimate")


class AppApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = Config(http_host="127.0.0.1", http_port=0, routing_url="")  # tests never leave the machine
        cls.store = RiderStore(cls.config)
        cls.book = OrderBook(cls.config, cls.store)
        cls.server = build_server(cls.config, cls.store, {}, [], "web", cls.book, "app")
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def online(self, rider_id):
        import time
        self.store.ingest_score(ScoreSample(rider_id, time.time(), 2.0))

    def call(self, path, obj=None):
        data = None if obj is None else json.dumps(obj).encode()
        request = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                return resp.status, json.loads(resp.read()), resp.headers
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read()), exc.headers

    def test_order_flow_over_http(self):
        riders = self.call("/api/app/riders")[1]["riders"]
        self.assertEqual(riders[0]["id"], "rider-01")
        self.online("rider-02")
        code, state, headers = self.call("/api/app/rider-02/duty", {"on": True})
        self.assertEqual((code, state["on_duty"], headers.get("Access-Control-Allow-Origin")), (200, True, "*"))
        order = self.book.offer("rider-02", "店", "取餐地", "送達地", "餐點", 1.0, 42)
        self.assertEqual(self.call("/api/app/rider-02/state")[1]["offer"]["fee"], 42)
        state = self.call("/api/app/rider-02/offer", {"order_id": order.id, "accept": True})[1]
        self.assertEqual(state["order"]["state"], "accepted")
        self.call("/api/app/rider-02/advance", {"order_id": order.id})
        state = self.call("/api/app/rider-02/advance", {"order_id": order.id})[1]
        self.assertEqual(state["stats"], {"delivered": 1, "earnings": 42})

    def test_route_follows_the_order_state(self):
        rider, pickup, dropoff = "rider-04", (24.7995, 120.9990), (24.7957, 120.9920)
        self.assertEqual(self.call(f"/api/app/{rider}/route")[0], 404, "nothing on screen: no route")
        self.online(rider)
        self.call(f"/api/app/{rider}/duty", {"on": True})
        order = self.book.offer(rider, "店", "取餐地", "送達地", "餐點", 1.0, 42, pickup, dropoff)
        self.assertEqual(self.call(f"/api/app/{rider}/state")[1]["offer"]["pickup_pos"], list(pickup))
        route = self.call(f"/api/app/{rider}/route")[1]
        self.assertEqual(([leg["to"] for leg in route["legs"]], route["geometry"], route["order_id"], route["from_rider"]),
                         (["dropoff"], [list(pickup), list(dropoff)], order.id, False))
        route = self.call(f"/api/app/{rider}/route?from=24.79,121.0")[1]
        self.assertEqual(([leg["to"] for leg in route["legs"]], route["from_rider"]), (["pickup", "dropoff"], True))
        self.call(f"/api/app/{rider}/offer", {"order_id": order.id, "accept": True})
        self.call(f"/api/app/{rider}/advance", {"order_id": order.id})  # food is on the bike
        route = self.call(f"/api/app/{rider}/route?from=24.79,121.0")[1]
        self.assertEqual(([leg["to"] for leg in route["legs"]], route["geometry"][0]), (["dropoff"], [24.79, 121.0]))
        route = self.call(f"/api/app/{rider}/route")[1]
        self.assertEqual(route["geometry"][0], list(pickup), "picked up + no GPS: still show shop -> customer")
        self.assertEqual(self.call(f"/api/app/{rider}/route?from=here")[0], 400)
        self.assertEqual(self.call(f"/api/app/{rider}/route?from=95,0")[0], 400)

    def test_errors(self):
        self.assertEqual(self.call("/api/app/nobody/state")[0], 404)
        self.assertEqual(self.call("/api/app/rider-03/duty", {"on": "yes"})[0], 400)
        self.assertEqual(self.call("/api/app/rider-03/ack", {"level": "loud"})[0], 400)
        code, body, _ = self.call("/api/app/rider-03/offer", {"order_id": "o-999", "accept": True})
        self.assertEqual((code, body["state"]["rider"]["id"]), (409, "rider-03"))

    def test_app_shell_is_served(self):
        with urllib.request.urlopen(self.base + "/app/", timeout=5) as resp:
            self.assertIn(b"js/main.js", resp.read())
        with urllib.request.urlopen(self.base + "/app/manifest.webmanifest", timeout=5) as resp:
            self.assertEqual(resp.headers.get("Content-Type"), "application/manifest+json")
            self.assertEqual(json.load(resp)["start_url"], "./")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.base + "/app/../server/config.py", timeout=5)
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
