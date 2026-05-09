# Virtustec Wire Protocol

Reference for the WebSocket envelope and resource set used by the Virtustec
virtuals platform served via SportyBet's `/virtual` page. This is what the
capture daemon writes to the journal and what the parser decodes.

## Connection

- WebSocket URL: `wss://virtual-proxy.virtustec.com/vs`
- Reached from the iframe at `https://virtual-games.virtustec.com/desktop-v4/...`
- The iframe is loaded inside the parent page `https://www.sportybet.com/ng/virtual`
- One WebSocket per browser session carries data for **all enabled products**
  (football, dogs, horses, speedway, motorbikes, ...) regardless of which
  league is on screen.
- Session lifetime is roughly 20 minutes. After that every call returns 401
  and the iframe needs to reload (see `runbook.md` "recovery ladder").

## Frame envelope

Every WebSocket frame is a JSON object. There are two types:

### REQUEST (client -> server)

```json
{
  "type": "REQUEST",
  "xs": 42,
  "ts": 1778340225242,
  "req": {
    "method": "GET",
    "resource": "/eventBlocks/event/data",
    "basePath": "/api/client/v0.1",
    "host": "wss://virtual-proxy.virtustec.com",
    "query": { "playlistId": 41104 },
    "headers": { "Content-Type": "application/json" }
  }
}
```

### RESPONSE (server -> client)

```json
{
  "type": "RESPONSE",
  "xs": 42,
  "ts": 1778340225512,
  "res": {
    "statusCode": 200,
    "body": [ /* one or more block objects */ ]
  }
}
```

Pairing is by `xs` (correlation id) within a single WebSocket lifetime. On
WebSocket open or close, the parser clears its in-flight `xs` map so a stale
id from a previous connection cannot mis-pair against a new one.

## Resources

All paths are relative to the basePath `/api/client/v0.1`. Headers and query
parameters are not load-bearing for the parser; only `req.resource`,
`res.statusCode`, and `res.body` are read.

| Resource                       | Purpose                                                                    |
| ------------------------------ | -------------------------------------------------------------------------- |
| `/session/sync`                | Keepalive; `body` is `SessionSettings` with locale + jackpot info          |
| `/session/loginHwId`           | Hardware-fingerprint login (handled by SportyBet JS, parser ignores)       |
| `/session/loginOnlineHash`     | Hash-based login (parser ignores)                                          |
| `/playlists/<...>`             | Schema templates: market definitions, participant rosters, countdown, ... |
| `/eventBlocks/event/data`      | Match instances with closing odds + participant snapshot                   |
| `/eventBlocks/event/result`    | Settled matches with `finalOutcome` and `wonMarkets`                       |
| `/eventBlocks/stats`           | League standings (football only)                                           |
| `/tickets/findByTime`          | Bet history; usually 401 from this proxy. Filter out at capture time       |

## Status codes

| Code | Meaning                                                            |
| ---- | ------------------------------------------------------------------ |
| 200  | Success                                                            |
| 401  | Unauthorized; >5 in 60s indicates session death (recovery trigger) |
| 701  | Server-side game/business error; ignorable individually            |

## Block-level shape

Most data resources return an array of "blocks". Each block has top-level
fields plus nested `data`, `stats`, and/or `events[].data` / `events[].result`
sub-objects. The parser dispatches on the `classType` field at the deepest
populated level; see Appendix A below.

### `/playlists/` template (one element of the body array)

Top-level fields used by the parser:

- `id` (int) - schema id, same as `playlistId` on event blocks
- `gameTypeBase` - rough product hint
- `mode`, `description`, `descriptionTag` - human labels
- `marketTemplateId` - which market set this schema uses
- `filter.competitionType` - `LEAGUE` (league schema) or `CHAMPION` (tournament)
- `filter.competitionSubType`, `filter.numParticipants`, `filter.isTwoLegsGroup`
- `filter.libraryId`, `filter.contentLibrary` - replay catalogue refs
- `schedulerConfiguration[0].dailySchedule[0].countdown` - seconds between matches
- `marketTemplates[].odds[].value` - **slot index** (int). Used by event
  `oddValues[slot]` to identify which (market, odd) a price refers to.
- `participantTemplates[]` - the team / runner roster with base ratings

### `/eventBlocks/event/data` block

```json
{
  "blockType": "FbEventBlock",
  "eBlockId": 12345,
  "playlistId": 41104,
  "serverStatus": "SCHEDULED",
  "eventTime": "2026-05-09T16:00:00Z",
  "data": { "classType": "FbEventBlockData", "matchDay": 17, "phase": "GROUPS", "legOrder": 2, ... },
  "events": [{
    "eventId": "...",
    "data": {
      "classType": "FbParticipantBlockData",
      "participants": [
        { "classType": "FbParticipant", "id": "501", "stars": 5.0 },
        { "classType": "FbParticipant", "id": "336", "stars": 4.5 }
      ],
      "oddValues": ["1.85", "4.20", "3.40", ... ]
    }
  }]
}
```

### `/eventBlocks/event/result` block

Same envelope as the data block but `events[0].result` is populated and
`events[0].data` is empty:

```json
{
  "events": [{
    "result": {
      "data": { "classType": "RaceEventResultData", "finalOrder": ["d2","d1","d3"], "mediaId": "...", "happenings": "..." },
      "finalOutcome": ["2", "1"],
      "wonMarkets": ["Match_Result_Home", "_2_1", "Over_Under_2_5_over"]
    }
  }]
}
```

For football, `finalOutcome` is `[home_score, away_score]` and `wonMarkets`
is a list of strings naming markets that paid out. For races, the winner is
`result.data.finalOrder[0]` (a runner id, or trap number depending on
product); `finalOutcome` is informational.

Note that for race result blocks, `participants[]` is **empty**. The parser
falls back to the `playlistId` -> product mapping it learned from earlier
`/event/data` frames to dispatch correctly. See `src/parse/ingest.py`.

### `/eventBlocks/stats` block (football)

```json
{
  "data": { "classType": "FbEventBlockData", "matchDay": 12 },
  "stats": {
    "classType": "FbEventBlockStats",
    "groupClassification": [{
      "entries": [
        { "participantId": "501", "ranking": 1, "points": 30, "wins": 9, ... }
      ]
    }]
  }
}
```

## Appendix A - classType dispatch

Per-product `classType` values used for dispatch.

### participants[0].classType (event blocks)

| Product    | classType values                                  |
| ---------- | ------------------------------------------------- |
| football   | `FbParticipant`, `FootballParticipant`            |
| dogs       | `DogParticipant`                                  |
| horses     | `HorseParticipant`                                |
| speedway   | `SpeedwayParticipant`                             |
| motorbikes | `MotorbikeParticipant`, `MotorbikesParticipant`   |
| mma        | `MmaParticipant`                                  |

### stats.classType (stats blocks)

| Product    | classType values                                       |
| ---------- | ------------------------------------------------------ |
| football   | `FbEventBlockStats`, `FootballEventBlockStats`         |
| dogs       | `DogEventBlockStats`, `RaceEventBlockStats`            |
| horses     | `HorseEventBlockStats`, `RaceEventBlockStats`          |
| speedway   | `SpeedwayEventBlockStats`, `RaceEventBlockStats`       |
| motorbikes | `MotorbikeEventBlockStats`, `MotorbikesEventBlockStats`, `RaceEventBlockStats` |
| mma        | `MmaEventBlockStats`                                   |

### data.classType (metadata-only blocks; participants absent)

| Product    | classType values                                       |
| ---------- | ------------------------------------------------------ |
| football   | `FbEventBlockData`, `FootballEventBlockData`           |
| dogs       | `DogEventBlockData`, `RaceEventBlockData`              |
| horses     | `HorseEventBlockData`, `RaceEventBlockData`            |
| speedway   | `SpeedwayEventBlockData`, `RaceEventBlockData`         |
| motorbikes | `MotorbikeEventBlockData`, `MotorbikesEventBlockData`, `RaceEventBlockData` |
| mma        | `MmaEventBlockData`                                    |

When dispatch by `classType` fails (typical for race result blocks because
they have no participants and only a generic `RaceEventResultData` inside
`events[0].result`), the parser falls back to looking up the product by
`playlistId` from a previously-seen schema row.

## Journal format (what the daemon writes)

Each line of `vs_YYYY-MM-DD.jsonl` is one JSON record:

| Field             | Type           | Notes                                                    |
| ----------------- | -------------- | -------------------------------------------------------- |
| `ts`              | float          | Wall-clock unix timestamp                                |
| `kind`            | string         | `"open"`, `"close"`, `"iframe_url"`, `"frame"`           |
| `dir`             | `"in"\|"out"`  | Only present for `kind=="frame"`                         |
| `data`            | string         | The raw JSON-RPC envelope (string, not nested). Frame only |
| `url`             | string         | The WS URL or iframe URL. Lifecycle markers only         |
| `session_id`      | string         | Daemon's UUID, present on every record                   |
| `capture_version` | int            | Schema version of this journal record format             |

The legacy prototype journal (in `vs_scraper/captures/`) uses the older
format with key `t` instead of `ts` and no `session_id`. The parser handles
both via `src/parse/journal.py` `iter_frames`.
