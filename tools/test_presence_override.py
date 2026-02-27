import argparse
import os
import sys
import time

import json
import requests

# Ensure we can import from src/
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(REPO_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from core.homeassistant import (  # noqa: E402
    HomeAssistantClient,
    load_homeassistant_config,
)
def _mk_client(repo_root: str) -> HomeAssistantClient:
    cfg_path = os.path.join(repo_root, "config", "homeassistant.json")
    cfg = load_homeassistant_config(cfg_path)
    if not cfg:
        raise RuntimeError(f"Home Assistant config missing/invalid: {cfg_path}")
    return HomeAssistantClient(cfg)


def _print_state(client: HomeAssistantClient, entity_id: str) -> None:
    st = client.get_state(entity_id)
    if not st:
        print(f"{entity_id}: <no state>")
        return
    attrs = st.get("attributes") or {}
    print(f"{entity_id}: state={st.get('state')}")
    if entity_id.startswith("person."):
        print(f"  source={attrs.get('source')}")
        print(f"  gps=({attrs.get('latitude')}, {attrs.get('longitude')}), acc={attrs.get('gps_accuracy')}")


def _print_service_schema(client: HomeAssistantClient, domain: str, service: str) -> None:
    url = client.base_url.rstrip("/") + "/api/services"
    r = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {client.config.token}",
            "Content-Type": "application/json",
        },
        timeout=client.config.timeout_s,
        verify=client.config.verify_ssl,
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        print("/api/services: unexpected response")
        return

    for d in data:
        if str(d.get("domain") or "") != domain:
            continue
        services = d.get("services") or {}
        svc = services.get(service)
        if not svc:
            print(f"Service not found: {domain}.{service}")
            return
        print(f"Service schema: {domain}.{service}")
        print(json.dumps(svc, indent=2, ensure_ascii=False))
        return

    print(f"Domain not found: {domain}")


def _post_service(client: HomeAssistantClient, domain: str, service: str, data: dict) -> object:
    url = client.base_url.rstrip("/") + f"/api/services/{domain}/{service}"
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {client.config.token}",
            "Content-Type": "application/json",
        },
        data=json.dumps(data or {}),
        timeout=client.config.timeout_s,
        verify=client.config.verify_ssl,
    )
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {"status": "no-json"}
    if entity_id.startswith("device_tracker."):
        print(f"  last_seen={attrs.get('last_seen')}")
        print(f"  gps=({attrs.get('latitude')}, {attrs.get('longitude')}), acc={attrs.get('gps_accuracy')}")


def _find_related_trackers(client: HomeAssistantClient, tracker_entity_id: str) -> list[str]:
    needle = str(tracker_entity_id or "").strip().lower()
    if not needle:
        return []
    suffix = needle.split("device_tracker.", 1)[-1]
    matches: list[str] = []
    for st in client.get_states():
        eid = str(st.get("entity_id") or "")
        if not eid.startswith("device_tracker."):
            continue
        if suffix and suffix in eid.lower():
            matches.append(eid)
    return sorted(set(matches))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--person", default="person.laurenz")
    ap.add_argument("--tracker", default="")
    ap.add_argument("--location", choices=["home", "not_home"], required=True)
    ap.add_argument("--wait", type=float, default=2.0, help="Seconds to wait after service call")
    ap.add_argument(
        "--method",
        choices=["force", "see", "set_location"],
        default="force",
        help="Which method to call: force (app logic), see (device_tracker.see), set_location (homeassistant.set_location)",
    )
    ap.add_argument(
        "--show-services",
        action="store_true",
        help="Print HA service schemas for debugging (device_tracker.see, homeassistant.set_location)",
    )
    args = ap.parse_args()

    client = _mk_client(REPO_ROOT)

    if args.show_services:
        _print_service_schema(client, "device_tracker", "see")
        print("")
        _print_service_schema(client, "homeassistant", "set_location")
        print("")

    # Resolve tracker from person if not provided.
    person_state = client.get_state(args.person) or {}
    tracker = args.tracker.strip() or ((person_state.get("attributes") or {}).get("source") or "")

    # Compute target coordinates based on zone.home.
    zone_home = client.get_state("zone.home") or {}
    z_attrs = zone_home.get("attributes") or {}
    home_lat = z_attrs.get("latitude")
    home_lon = z_attrs.get("longitude")
    if args.location == "home" and home_lat is not None and home_lon is not None:
        target_lat = float(home_lat)
        target_lon = float(home_lon)
    elif args.location == "not_home" and home_lat is not None and home_lon is not None:
        target_lat = float(home_lat) + 1.0
        target_lon = float(home_lon) + 1.0
    elif args.location == "not_home":
        target_lat = 0.0
        target_lon = 0.0
    else:
        target_lat = None
        target_lon = None

    before_person = client.get_state(args.person) or {}
    before_tracker = client.get_state(tracker) if tracker else None

    print("Before:")
    _print_state(client, args.person)
    if tracker:
        _print_state(client, tracker)
    else:
        print("(No tracker resolved from person.source)")

    print("\nCalling override:")
    if args.method == "force":
        ok = client.force_person_presence(args.person, args.location, device_tracker_entity_id=tracker or None)
        print(f"force_person_presence -> {ok}")
    elif args.method == "set_location":
        if not tracker:
            raise RuntimeError("No tracker resolved; provide --tracker")
        if target_lat is None or target_lon is None:
            raise RuntimeError("zone.home not available for --location home")
        resp = _post_service(
            client,
            "homeassistant",
            "set_location",
            {"entity_id": tracker, "latitude": target_lat, "longitude": target_lon},
        )
        print("homeassistant.set_location response:")
        print(json.dumps(resp, indent=2, ensure_ascii=False))
    else:  # see
        if not tracker:
            raise RuntimeError("No tracker resolved; provide --tracker")
        dev_id = tracker.split(".", 1)[1] if tracker.startswith("device_tracker.") else tracker
        data = {"dev_id": dev_id, "location_name": args.location}
        if target_lat is not None and target_lon is not None:
            data["gps"] = [target_lat, target_lon]
            data["gps_accuracy"] = 50
        resp = _post_service(client, "device_tracker", "see", data)
        print("device_tracker.see response:")
        print(json.dumps(resp, indent=2, ensure_ascii=False))

    # Poll briefly to catch transient flips that get overwritten quickly.
    poll_s = max(0.0, float(args.wait))
    if poll_s > 0 and tracker:
        start = time.time()
        last_state = None
        while time.time() - start < poll_s:
            st = client.get_state(tracker) or {}
            cur = str(st.get("state") or "")
            if last_state is None:
                last_state = cur
            elif cur != last_state:
                print(f"  tracker changed: {last_state} -> {cur}")
                last_state = cur
            time.sleep(0.5)
    elif poll_s > 0:
        time.sleep(poll_s)

    print("\nAfter:")
    _print_state(client, args.person)
    if tracker:
        _print_state(client, tracker)

        related = _find_related_trackers(client, tracker)
        if related and (len(related) > 1 or related[0] != tracker):
            print("\nRelated device_tracker entities:")
            for eid in related:
                _print_state(client, eid)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
