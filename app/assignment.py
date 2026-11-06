"""Per-rater case assignment.

A rater is bound to one modality: on their first Cases-page visit they are routed to the first
modality (config.MODALITIES order) that still has fewer than RATERS_PER_MODALITY real raters. Within
that modality the unit of assignment is a *study* (one reference report). A rater who is
assigned a study rates every candidate report of that study, back to back, in
a rater-specific shuffled order — the reference is read once, the candidates
are compared against it one after another, and the model behind each
candidate stays hidden.

Studies are handed out greedily, least-covered-first (ties broken by study id),
so every study converges toward TARGET_COVERAGE raters before any study gets
more. An assignment is made once, on the rater's first visit to the Cases
page, persisted, and never reshuffled afterwards; logging back in resumes the
exact same list.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
from collections import Counter, defaultdict

import auth
from config import (ADMIN_EMAILS, DATA_DIR, MODALITIES, NON_RATER_EMAILS, OVERLAP_FIRST, RATERS_PER_MODALITY,
                    STUDIES_PER_RATER)

ASSIGN_PATH = DATA_DIR / "assignments.json"
NON_RATER_SLUGS = {auth.user_slug(e) for e in NON_RATER_EMAILS} | {auth.user_slug(auth.DEFAULT_ADMIN_USER)}


def load() -> dict:
    if not ASSIGN_PATH.exists():
        return {}
    try:
        return json.loads(ASSIGN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    ASSIGN_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ASSIGN_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ASSIGN_PATH)


def _by_study(cases: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in cases:
        groups[c["study_id"]].append(c)
    return groups


def _modality_of_study(cases: list[dict]) -> dict[str, str]:
    return {c["study_id"]: c.get("modality", next(iter(MODALITIES))) for c in cases}


def _admin_slugs() -> set[str]:
    """Slugs of every admin account, read fresh so a newly promoted admin takes effect at once.

    NON_RATER_SLUGS only covers the built-in `admin` login and ANNOTATION_TEST_EMAILS, so an admin
    signed in under their own address would otherwise be counted as a rater: they would fill one of
    the RATERS_PER_MODALITY slots and inflate the coverage their arm appears to have.
    """
    try:
        accounts = auth.load_accounts()
    except Exception:  # noqa: BLE001 - never let the accounts file break assignment
        return set()
    return {auth.user_slug(e) for e, a in accounts.items()
            if (a or {}).get("role") == "admin" or e.strip().lower() in ADMIN_EMAILS}


def _real_raters(data: dict) -> dict[str, list[str]]:
    skip = NON_RATER_SLUGS | _admin_slugs()
    return {slug: ids for slug, ids in data.items() if slug not in skip and not slug.startswith("_")}


EXTRA_KEY = "_extra_modalities"      # {slug: [modality, ...]} — see grant_modality()
OVERLAP_KEY = "_overlap"             # {modality: [study_id, ...]} — see plan_overlap()
QUOTA_KEY = "_quota"                 # {slug: {modality: n}} — see quota()


def quota(rater_slug: str, modality: str, data: dict | None = None) -> int | None:
    """A per-rater cap on how many studies this rater takes in `modality`, or None for the default.

    STUDIES_PER_RATER is the normal size of a list, and studies_for() tops a short list back up to
    it. That is right for a rater working an arm, and wrong for one given a deliberately small
    block -- a few studies parked with a returning rater, or a hand-picked set -- because the first
    visit to that tab would silently extend it to the full quota out of the least-covered pile,
    taking studies meant for somebody else. A quota pins the size instead.
    """
    data = load() if data is None else data
    cap = (data.get(QUOTA_KEY) or {}).get(rater_slug, {}).get(modality)
    return int(cap) if isinstance(cap, (int, float)) else None


def overlap_block(modality: str, data: dict | None = None) -> list[str]:
    """The studies every rater in `modality` should rate, or [] if none was pinned."""
    data = load() if data is None else data
    return list((data.get(OVERLAP_KEY) or {}).get(modality) or [])


def rater_modalities(rater_slug: str, cases: list[dict], data: dict | None = None) -> list[str]:
    """Every modality this rater holds studies in, plus any granted, in config order.

    A rater is normally bound to one arm, but one who finishes an arm can be invited to a second
    (grant_modality) rather than a new person being recruited. Their study ids simply live in the
    same list; the modality of each study is what separates them.
    """
    data = load() if data is None else data
    mod_of = _modality_of_study(cases)
    have = {mod_of[s] for s in data.get(rater_slug, []) if s in mod_of}
    have |= {m for m in data.get(EXTRA_KEY, {}).get(rater_slug, []) if m in MODALITIES}
    return [m for m in MODALITIES if m in have]


def rater_modality(rater_slug: str, cases: list[dict], data: dict | None = None) -> str | None:
    """The modality this rater started in, or None if new. Kept for callers that want a single
    answer; rater_modalities() is the full picture."""
    data = load() if data is None else data
    mod_of = _modality_of_study(cases)
    ids = [s for s in data.get(rater_slug, []) if s in mod_of]
    if not ids:
        return None
    return Counter(mod_of[s] for s in ids).most_common(1)[0][0]


def grant_modality(rater_slug: str, modality: str) -> bool:
    """Let `rater_slug` also rate `modality`. Records intent only — the studies themselves are
    assigned the first time they open that tab, by the usual least-covered-first rule."""
    if modality not in MODALITIES:
        return False
    data = load()
    extra = data.setdefault(EXTRA_KEY, {})
    if modality in extra.get(rater_slug, []):
        return True
    extra.setdefault(rater_slug, []).append(modality)
    _save(data)
    return True


def _pick_modality(cases: list[dict], data: dict) -> str:
    """First modality (config order) that is present in the pool and still has fewer than
    RATERS_PER_MODALITY real raters; if all are full, the one with the fewest raters."""
    mod_of = _modality_of_study(cases)
    present = [m for m in MODALITIES if m in set(mod_of.values())]
    raters_per: Counter = Counter()
    for slug, ids in _real_raters(data).items():
        # A rater working two arms fills a slot in each. One holding a capped block (quota()) is
        # parked in that arm rather than working it, so they leave the slot open -- otherwise a few
        # studies left with a returning rater would make the arm look staffed and route the next
        # person who signs up somewhere else.
        raters_per.update(m for m in rater_modalities(slug, cases, data)
                          if quota(slug, m, data) is None)
    for m in present:
        if raters_per[m] < RATERS_PER_MODALITY:
            return m
    return min(present, key=lambda m: raters_per[m])


def studies_for(rater_slug: str, cases: list[dict], modality: str | None = None) -> list[str]:
    """This rater's assigned study ids in one modality, extending a prior assignment if it is short.

    `modality` names which arm to assign in; without it the rater's existing arm is used, or a new
    one is chosen for a rater who has none. Calling this creates the assignment, so it is also how
    a rater granted a second arm receives their studies the first time they open that tab.
    """
    data = load()
    mod_of = _modality_of_study(cases)
    modality = modality or rater_modality(rater_slug, cases, data) or _pick_modality(cases, data)
    available = {s for s, m in mod_of.items() if m == modality}

    existing = [s for s in data.get(rater_slug, []) if s in available]
    target = min(STUDIES_PER_RATER, len(available))
    cap = quota(rater_slug, modality, data)
    if cap is not None:
        target = min(target, cap)
    if len(existing) >= target:
        return existing

    coverage: Counter = Counter()
    for slug, ids in _real_raters(data).items():
        if slug != rater_slug:
            coverage.update(s for s in ids if s in available)

    have = set(existing)
    # WHICH studies: least-covered first, so every study reaches one rater before any reaches two.
    # Within one coverage level, round-robin over the candidate systems instead of taking study ids in
    # order — otherwise the overlap block inherits whatever model mix the id order happens to give
    # (15 of one system vs 6 of another), and per-model agreement would rest on very few cases.
    model_of = {c["study_id"]: c.get("candidate_id", "") for c in cases}
    by_level: dict[int, list[str]] = defaultdict(list)
    for s in available:
        if s not in have:
            by_level[coverage.get(s, 0)].append(s)
    pool: list[str] = []
    for level in sorted(by_level):
        per_model: dict[str, list[str]] = defaultdict(list)
        for s in sorted(by_level[level]):
            per_model[model_of.get(s, "")].append(s)
        models = sorted(per_model)
        while any(per_model[m] for m in models):
            for m in models:
                if per_model[m]:
                    pool.append(per_model[m].pop(0))
    # A pinned overlap block (plan_overlap) goes to every rater in the arm, ahead of everything
    # else, so the shared cases land in the same early positions for all of them instead of
    # wherever the coverage ordering happens to drop them. Without one this is a no-op and the
    # least-covered-first behaviour below is unchanged.
    block = [s for s in overlap_block(modality, data) if s in available and s not in have]
    if block:
        pool = block + [s for s in pool if s not in set(block)]

    fresh = pool[: target - len(existing)]
    if existing:
        picked = existing + fresh          # never reorder a list a rater has already started
    elif block:
        rest = [s for s in fresh if s not in set(block)]
        picked = [s for s in block if s in set(fresh)] + rest
    elif OVERLAP_FIRST:
        # IN WHICH ORDER: overlap (already covered) first, so agreement data accrues from day one.
        picked = sorted(fresh, key=lambda s: (-coverage.get(s, 0), s))
    else:
        picked = fresh
    if picked != existing:
        # A rater may hold assignments in more than one modality (see rater_modalities). `picked`
        # covers THIS modality only, so everything they hold elsewhere has to be carried over --
        # assigning `picked` alone would silently delete a completed arm.
        other = [s for s in data.get(rater_slug, []) if mod_of.get(s) != modality]
        data[rater_slug] = other + picked
        _save(data)
    return picked


def cases_for(rater_slug: str, cases: list[dict], modality: str | None = None) -> list[dict]:
    """Ordered case list for this rater: assigned studies in assignment order, each study's
    candidates in a per-rater deterministic shuffle."""
    groups = _by_study(cases)
    out: list[dict] = []
    for study_id in studies_for(rater_slug, cases, modality):
        cands = list(groups[study_id])
        seed = int(hashlib.sha256(f"{rater_slug}|{study_id}".encode()).hexdigest(), 16) % (2**32)
        random.Random(seed).shuffle(cands)
        out.extend(cands)
    return out


def preview_cases(modality: str, cases: list[dict]) -> tuple[str | None, list[dict]]:
    """(rater_slug, their case list) for `modality` — read-only, for the admin preview.

    Deliberately does NOT go through studies_for(): that persists an assignment for whoever asks,
    so an admin opening the page would be written into assignments.json, take studies off the
    least-covered pile that real raters should get, and skew coverage. This reads the stored
    assignment of a rater who already has one and reproduces their exact case order, including the
    per-rater candidate shuffle, so the admin sees that rater's screen rather than an approximation.

    Falls back to the modality's own studies in pool order when nobody is assigned yet, which is the
    normal state for a freshly built arm. Returns (None, cases) there: no rater to name.
    """
    data = load()
    groups = _by_study(cases)
    mod_of = _modality_of_study(cases)

    slug = next((s for s in sorted(_real_raters(data)) if modality in rater_modalities(s, cases, data)), None)
    if slug:
        study_ids = [s for s in data.get(slug, []) if mod_of.get(s) == modality]
    else:
        study_ids = list(dict.fromkeys(s for s, m in mod_of.items() if m == modality))

    out: list[dict] = []
    for study_id in study_ids:
        cands = list(groups[study_id])
        if slug:                                  # same shuffle the rater sees
            seed = int(hashlib.sha256(f"{slug}|{study_id}".encode()).hexdigest(), 16) % (2**32)
            random.Random(seed).shuffle(cands)
        out.extend(cands)
    return slug, out


def coverage_table(cases: list[dict]) -> dict[str, int]:
    """{study_id: number of real raters assigned} — for the admin page."""
    cov: Counter = Counter()
    for ids in _real_raters(load()).values():
        cov.update(ids)
    return {s: cov.get(s, 0) for s in _by_study(cases)}


def raters_by_modality(cases: list[dict]) -> dict[str, list[str]]:
    data = load()
    out: dict[str, list[str]] = {m: [] for m in MODALITIES}
    for slug in _real_raters(data):
        for m in rater_modalities(slug, cases, data):
            out.setdefault(m, []).append(slug)
    return out


def plan_overlap(cases: list[dict], modality: str, size: int | None = None,
                 dry_run: bool = False) -> dict:
    """Pin the shared block for `modality` and move it to the front of every rater's list.

    The overlap is what inter-rater agreement is computed from, so it should be rated early (a
    study both raters reach in week one is worth more than one they reach only if they finish) and
    it should be balanced across candidate systems, or per-system agreement rests on a handful of
    cases. Falling out of the coverage ordering gives neither: the second rater gets the shared
    cases first, while the first rater, assigned when nothing was covered yet, meets them scattered
    through their list.

    `size` defaults to the arithmetic overlap, RATERS_PER_MODALITY * STUDIES_PER_RATER - n_studies.
    The block is drawn from studies already assigned to someone (so nobody's set has to change) and
    balanced across candidate_id, taking study ids in sorted order within each system so the choice
    is reproducible.

    A rater who has already saved a rating in this modality is left in their current order --
    reordering under someone mid-pass would move cases they have already worked through.
    """
    import storage

    data = load()
    mod_of = _modality_of_study(cases)
    cand_of = {c["study_id"]: c.get("candidate_id", "") for c in cases}
    available = [s for s, m in mod_of.items() if m == modality]
    if size is None:
        size = max(RATERS_PER_MODALITY * STUDIES_PER_RATER - len(available), 0)

    assigned: list[str] = []
    for slug, ids in _real_raters(data).items():
        assigned += [s for s in ids if mod_of.get(s) == modality]
    pool = sorted(set(assigned)) or sorted(available)

    by_system: dict[str, list[str]] = defaultdict(list)
    for s in pool:
        by_system[cand_of.get(s, "")].append(s)
    systems = sorted(by_system)
    block: list[str] = []
    while len(block) < size and any(by_system[m] for m in systems):
        for m in systems:                       # round-robin keeps the block balanced
            if by_system[m] and len(block) < size:
                block.append(by_system[m].pop(0))

    report = {"modality": modality, "size": len(block),
              "per_system": dict(Counter(cand_of.get(s, "") for s in block)),
              "reordered": {}, "skipped_started": []}

    for slug, ids in _real_raters(data).items():
        mine = [s for s in ids if mod_of.get(s) == modality]
        if not mine:
            continue
        rated = {r.get("study_id") for r in storage.load_ratings(slug).values()}
        if rated & set(mine):
            report["skipped_started"].append(slug)
            continue
        front = [s for s in block if s in set(mine)]
        rest = [s for s in mine if s not in set(front)]
        if front + rest != mine:
            others = [s for s in ids if mod_of.get(s) != modality]
            data[slug] = others + front + rest
            report["reordered"][slug] = len(front)

    if not dry_run:
        data.setdefault(OVERLAP_KEY, {})[modality] = block
        _save(data)
    return report
