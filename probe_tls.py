"""
Diagnostic: list traffic-light groups and member IDs in the currently-loaded
CARLA world, then flag the group that contains SUMO intersection 238's nearest
neighbours. Run while CARLA is up (Town03 loaded); no SUMO required.
"""
import math
import carla

TARGET_SIZE = 4   # we expect a 4-way junction group

client = carla.Client("127.0.0.1", 2000)
client.set_timeout(10.0)
world = client.get_world()
print(f"Map: {world.get_map().name}")

# Diagnose and fix stuck-sync-mode leftover from a crashed dashboard run.
settings = world.get_settings()
print(f"synchronous_mode={settings.synchronous_mode}  "
      f"fixed_delta_seconds={settings.fixed_delta_seconds}")
if settings.synchronous_mode:
    print("World is in synchronous mode — disabling and ticking once to refresh.")
    settings.synchronous_mode = False
    settings.fixed_delta_seconds = None
    world.apply_settings(settings)

# Positional arg (older signature doesn't accept `timeout=`).
try:
    world.wait_for_tick(5.0)
except Exception as exc:
    print(f"wait_for_tick note: {exc}")

all_actors = list(world.get_actors())
print(f"Total actors in world: {len(all_actors)}")
type_counts: dict[str, int] = {}
for a in all_actors:
    type_counts[a.type_id] = type_counts.get(a.type_id, 0) + 1
print("Actor type breakdown (top 15 by count):")
for t, n in sorted(type_counts.items(), key=lambda kv: -kv[1])[:15]:
    print(f"  {n:4d}  {t}")
print()

all_tls = list(world.get_actors().filter("traffic.traffic_light*"))
print(f"Traffic lights matching 'traffic.traffic_light*': {len(all_tls)}")
if not all_tls:
    # Fallback filters in case the type_id differs.
    for alt in ("*traffic_light*", "*TrafficLight*", "*.traffic_light"):
        alt_hits = list(world.get_actors().filter(alt))
        print(f"  alt filter {alt!r}: {len(alt_hits)}")
print()

visited: set[int] = set()
groups: list[list[carla.TrafficLight]] = []
for tl in all_tls:
    if tl.id in visited:
        continue
    try:
        g = list(tl.get_group_traffic_lights())
    except Exception:
        g = [tl]
    for t in g:
        visited.add(t.id)
    groups.append(g)

groups.sort(key=lambda g: -len(g))
print(f"Found {len(groups)} groups. Showing groups of size >= 3:\n")

for idx, g in enumerate(groups):
    if len(g) < 3:
        break
    locs = [t.get_location() for t in g]
    cx = sum(l.x for l in locs) / len(locs)
    cy = sum(l.y for l in locs) / len(locs)
    print(f"Group #{idx}: {len(g)} lights, centroid=({cx:.1f}, {cy:.1f})")
    for t in g:
        l = t.get_location()
        ang = math.degrees(math.atan2(l.y - cy, l.x - cx)) % 360
        quadrant = ("E" if ang < 45 or ang >= 315
                    else "N" if ang < 135
                    else "W" if ang < 225
                    else "S")
        print(f"  id={t.id:<5d} at ({l.x:7.1f}, {l.y:7.1f})  "
              f"angle_from_centroid={ang:5.1f}°  quadrant={quadrant}")
    print()
