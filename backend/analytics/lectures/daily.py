"""Combine a module's same-day evidence without inventing meeting identities."""
from collections import defaultdict
from copy import deepcopy


def combine_days(prepared):
    buckets = defaultdict(list)
    for key, item in prepared.items():
        course_id = item["course"].pk if item["course"] else None
        # Undated evidence cannot establish a common calendar day.
        bucket = (course_id, item["day"]) if item["day"] else ("undated", key)
        buckets[bucket].append(key)
    combined, targets = {}, {}
    for keys in buckets.values():
        # Preserve an established attendance URL in preference to a media fragment.
        keys.sort(key=lambda key: (0 if prepared[key]["label"].startswith("Historical") else
                                   1 if prepared[key]["label"] == "Teams attendance" else 2, key))
        target = keys[0]
        result = deepcopy(prepared[target])
        result["observations"] = {}
        for key in keys:
            targets[key] = target
            item = prepared[key]
            result["reports"].update(item["reports"])
            result["resources"].update(item["resources"])
            result["folder"] = result["folder"] or item["folder"]
            if item["starts"] and (not result["starts"] or item["starts"] < result["starts"]):
                result["starts"] = item["starts"]
            if item["ends"] and (not result["ends"] or item["ends"] > result["ends"]):
                result["ends"] = item["ends"]
            for lid, row in item["observations"].items():
                if lid not in result["observations"]:
                    result["observations"][lid] = deepcopy(row)
                    result["observations"][lid]["timing_claims"] = []
                    result["observations"][lid]["intervals"] = []
                merged = result["observations"][lid]
                if row.get("duration_seconds") is not None:
                    merged["timing_claims"].append((row["first_join"], row["last_leave"], row["duration_seconds"]))
                for field in ("source_emails", "identity_ids", "speaking_emails"):
                    merged[field].update(row[field])
                merged["intervals"].extend(row["intervals"])
                for field in ("camera_on", "microphone_on"):
                    if row[field] is True:
                        merged[field] = True
                merged["needs_review"] |= row["needs_review"]
                rank = {"not_available": 0, "absent": 1, "review": 2, "present": 3}
                if rank[row["attendance"]] > rank[merged["attendance"]]:
                    merged["attendance"] = row["attendance"]
                for field, choose in (("first_join", min), ("last_leave", max)):
                    if row[field]:
                        merged[field] = choose(merged[field], row[field]) if merged[field] else row[field]
        if len(keys) > 1 and result["label"].startswith("Historical") and any(prepared[k]["label"] != "Historical attendance" for k in keys):
            result["label"] = "Historical attendance + Teams"
        combined[target] = result
    return combined, targets


def attendance_timing(intervals, claims, union, seconds):
    """Union exact spans; preserve conservative archived totals with missing spans.

    An archived first/last envelope can contain gaps, so it is never treated as
    continuous presence unless its reported duration fills the entire envelope.
    Overlapping incomplete claims contribute their maximum, not their sum.
    """
    exact = list(intervals)
    uncertain, unlocated = [], []
    for start, end, duration in claims:
        if start and end and end > start:
            if abs((end - start).total_seconds() - duration) < 1:
                exact.append((start, end))
            else:
                uncertain.append((start, end, duration))
        else:
            unlocated.append(duration)
    exact = union(exact)
    envelopes = sorted(exact + [(a, b) for a, b, _ in uncertain])
    components = []
    for start, end in envelopes:
        if components and start < components[-1][1]:
            components[-1] = (components[-1][0], max(end, components[-1][1]))
        else:
            components.append((start, end))
    duration = sum(max(
        seconds([(max(a, x), min(b, y)) for x, y in exact if min(b, y) > max(a, x)]),
        max((value for x, y, value in uncertain if min(y, b) > max(x, a)), default=0),
    ) for a, b in components)
    if unlocated:
        duration = max(duration, *unlocated)
    starts = [a for a, _, _ in claims if a] + [a for a, _ in exact]
    ends = [b for _, b, _ in claims if b] + [b for _, b in exact]
    known = bool(intervals or claims)
    return (min(starts) if starts else None, max(ends) if ends else None, duration if known else None)
