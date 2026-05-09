"""Per-race-product route groups (dogs, horses, speedway, motorbikes, mma).

Race products share enough shape that one factory builds all five sets of
endpoints. The shape mirrors the football routes where it makes sense
(`/<product>/schemas`, `/<product>/schemas/{id}`, etc.), with race-specific
analytics added (`winners-by-trap`, `top runners`).

Football has its own dedicated module because its concepts (seasons,
matchdays, standings) don't apply to races at all.
"""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from src.api.deps import SessionDep
from src.db.models import Event, EventRunner, Schema


def make_race_router(*, product: str, tag: str, label: str) -> APIRouter:
    """Build a router with the standard race endpoints for one product.

    `product`: the value stored in `events.product` (e.g. `"dogs"`).
    `tag`: the OpenAPI tag for the routes (e.g. `"Dogs"`).
    `label`: human label for descriptions (e.g. `"dog racing"`).
    """
    prefix = f"/{product}"
    router = APIRouter(tags=[tag])

    @router.get(
        f"{prefix}/schemas",
        summary=f"List {label} schemas",
        description=(
            f"Every {label} schema (race series) with the count of events "
            f"we've ingested for it. `kind` is `unknown` until a `/playlists/` "
            f"frame for that schema arrives (race playlists are only sent "
            f"when the iframe is on the matching sport)."
        ),
    )
    def list_schemas(session: SessionDep) -> dict:
        schemas = session.scalars(
            select(Schema).where(Schema.product == product).order_by(Schema.schema_id)
        ).all()
        out = []
        for s in schemas:
            n = session.scalar(
                select(func.count()).select_from(Event).where(Event.schema_id == s.schema_id)
            ) or 0
            out.append({
                "schema_id": s.schema_id,
                "kind": s.kind,
                "description": s.description,
                "num_participants": s.num_participants,
                "countdown_s": s.countdown_s,
                "events": int(n),
            })
        return {"count": len(out), "schemas": out}

    @router.get(
        f"{prefix}/schemas/{{schema_id}}",
        summary=f"One {label} schema",
        description=(
            f"Schema metadata plus a quick activity summary "
            f"(event count, oldest/newest captured timestamps)."
        ),
        responses={404: {"description": f"Not a {label} schema."}},
    )
    def schema_detail(session: SessionDep, schema_id: int) -> dict:
        s = session.get(Schema, schema_id)
        if s is None or s.product != product:
            raise HTTPException(status_code=404, detail=f"{label} schema not found")
        n = session.scalar(
            select(func.count()).select_from(Event).where(Event.schema_id == schema_id)
        ) or 0
        first_ts = session.scalar(
            select(func.min(Event.captured_ts)).where(Event.schema_id == schema_id)
        )
        last_ts = session.scalar(
            select(func.max(Event.captured_ts)).where(Event.schema_id == schema_id)
        )
        return {
            "schema_id": s.schema_id,
            "product": s.product,
            "kind": s.kind,
            "description": s.description,
            "competition_type": s.competition_type,
            "num_participants": s.num_participants,
            "countdown_s": s.countdown_s,
            "library_id": s.library_id,
            "events": int(n),
            "first_event_captured_ts": float(first_ts) if first_ts else None,
            "last_event_captured_ts": float(last_ts) if last_ts else None,
        }

    @router.get(
        f"{prefix}/schemas/{{schema_id}}/events",
        summary=f"Recent {label} events for one schema",
        description=(
            f"Most recent {label} events for this schema, newest first. "
            f"Each event includes the winner / second / third runner ids "
            f"(NULL until the result frame is captured), surface, distance, "
            f"and the full final order. Use `/events/{{e_block_id}}` for the "
            f"full per-runner detail and odds slot prices."
        ),
        responses={404: {"description": f"Not a {label} schema."}},
    )
    def schema_events(
        session: SessionDep,
        schema_id: int,
        limit: int = Query(50, ge=1, le=500),
    ) -> dict:
        s = session.get(Schema, schema_id)
        if s is None or s.product != product:
            raise HTTPException(status_code=404, detail=f"{label} schema not found")
        rows = session.scalars(
            select(Event)
            .where(Event.schema_id == schema_id, Event.product == product)
            .order_by(Event.captured_ts.desc())
            .limit(limit)
        ).all()
        return {
            "schema_id": schema_id,
            "count": len(rows),
            "events": [
                {
                    "e_block_id": e.e_block_id,
                    "event_time": e.event_time,
                    "server_status": e.server_status,
                    "captured_ts": e.captured_ts,
                    "settled_ts": e.settled_ts,
                    "num_runners": e.num_runners,
                    "winner_id": e.winner_id,
                    "second_id": e.second_id,
                    "third_id": e.third_id,
                    "final_order": e.final_order.split(",") if e.final_order else [],
                    "surface": e.surface,
                    "distance": e.distance,
                    "weather": e.weather,
                    "won_markets": e.won_markets.split(",") if e.won_markets else [],
                }
                for e in rows
            ],
        }

    @router.get(
        f"{prefix}/schemas/{{schema_id}}/winners-by-trap",
        summary=f"Winning-trap distribution for one {label} schema",
        description=(
            f"How often each starting position (trap, post, grid slot) has "
            f"won in this schema. Useful to spot systematic position bias "
            f"in the simulator. Only counts settled events."
        ),
        responses={404: {"description": f"Not a {label} schema."}},
    )
    def winners_by_trap(session: SessionDep, schema_id: int) -> dict:
        s = session.get(Schema, schema_id)
        if s is None or s.product != product:
            raise HTTPException(status_code=404, detail=f"{label} schema not found")
        events = session.scalars(
            select(Event).where(
                Event.schema_id == schema_id,
                Event.product == product,
                Event.winner_id.is_not(None),
            )
        ).all()
        traps: Counter[int] = Counter()
        total = 0
        for e in events:
            runner = session.get(EventRunner, (e.e_block_id, e.winner_id))
            if runner is not None and runner.trap is not None:
                traps[runner.trap] += 1
                total += 1
        return {
            "schema_id": schema_id,
            "total_settled_events": total,
            "winners_by_trap": [
                {
                    "trap": trap,
                    "wins": count,
                    "win_rate": (count / total) if total else 0.0,
                }
                for trap, count in sorted(traps.items())
            ],
        }

    @router.get(
        f"{prefix}/runners/top",
        summary=f"Top {label} runners by win count",
        description=(
            f"Most successful runners across all {label} events we've seen, "
            f"ranked by number of first-place finishes. Pass `schema_id` to "
            f"limit to one race series; otherwise spans every {label} schema. "
            f"`name` is the latest runner name we recorded for that id."
        ),
    )
    def top_runners(
        session: SessionDep,
        limit: int = Query(20, ge=1, le=200),
        schema_id: int | None = Query(None, description="Optional: limit to one schema"),
    ) -> dict:
        stmt = select(Event).where(
            Event.product == product, Event.winner_id.is_not(None)
        )
        if schema_id is not None:
            stmt = stmt.where(Event.schema_id == schema_id)
        events = session.scalars(stmt).all()

        wins: Counter[str] = Counter()
        names: dict[str, str | None] = {}
        for e in events:
            wins[e.winner_id] += 1
            runner = session.get(EventRunner, (e.e_block_id, e.winner_id))
            if runner is not None and runner.name and e.winner_id not in names:
                names[e.winner_id] = runner.name

        ranked = wins.most_common(limit)
        return {
            "product": product,
            "schema_id": schema_id,
            "count": len(ranked),
            "runners": [
                {"runner_id": rid, "wins": w, "name": names.get(rid)}
                for rid, w in ranked
            ],
        }

    return router
