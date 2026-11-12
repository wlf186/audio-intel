"""Immutable packet plans and atomic batch checkpoints; no transcript rewrites."""
from __future__ import annotations

import asyncio
import json
import sqlite3

from . import db
from . import optimization_store as store
from . import optimization_summaries as insights
from . import optimization_packet_policy as policy
from . import optimization_engine as engine


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE IF NOT EXISTS optimization_packet_nodes (
        run_id TEXT NOT NULL REFERENCES transcript_optimizations(id) ON DELETE CASCADE,
        stage TEXT NOT NULL, ordinal INTEGER NOT NULL, items_json TEXT NOT NULL,
        PRIMARY KEY(run_id,stage,ordinal)
    ) WITHOUT ROWID''')
    columns = {row[1] for row in connection.execute('PRAGMA table_info(optimization_calls)')}
    if 'node_key' not in columns:
        connection.execute('ALTER TABLE optimization_calls ADD COLUMN node_key TEXT')
    connection.execute('UPDATE schema_meta SET version=25 WHERE version<25')


def compile_plan(config: dict, units: list[dict], hotwords: list[str]) -> list[tuple[str, list[str]]]:
    profile = {**config, '_hotwords': hotwords,
               '_short': sum(len(unit['original_text']) for unit in units) <= 120}
    body = [{**unit, 'id': str(i)} for i, unit in enumerate(units)]
    groups = policy.group_sources(units)
    scenes = policy.scene_sources(units, config.get('summary_scene_ids', []))
    substantive = [unit for unit in body if policy.meaningful(unit['original_text'])]
    generation = [unit for unit in substantive if not policy.literal_frame(unit, english=bool(config.get('english_source')))]
    nodes = [('generation', [item['id'] for item in packet]) for packet in
             policy.pack(generation, maxitems=12, cfg=profile, stage='generation')]
    if config.get('packet_joint') and (generation or any(policy.meaningful(s['original_text']) for s in scenes)):
        nodes.append(('joint', [unit['id'] for unit in body]))
        return nodes
    checks = [{**unit, 'candidate': '文' * (len(unit['original_text']) * 2 + 64)}
              for unit in substantive]
    nodes.extend(('finalization', [item['id'] for item in packet]) for packet in
                 policy.pack(checks, maxitems=12, maxchars=2000, cfg=profile, stage='finalization'))
    details = [group for group in groups if group['id'].startswith('d:') and policy.meaningful(group['original_text'])]
    nodes.extend(('group', [item['id'] for item in packet]) for packet in
                 policy.pack(details, maxitems=16, maxchars=6000, cfg=profile, stage='group'))
    ordinary, long_scenes = [], []
    for scene in scenes:
        if not policy.meaningful(scene['original_text']):
            continue
        if engine.request_fits(profile, policy.payload(profile, [scene], 'scene')):
            ordinary.append(scene)
        else:
            long_scenes.append(scene)
    nodes.extend(('scene', [item['id'] for item in packet]) for packet in
                 policy.pack(ordinary, maxitems=16, maxchars=6000, cfg=profile, stage='scene'))
    nodes.extend(('scene_reduction', [scene['id']]) for scene in long_scenes)
    return nodes


def initialize(connection: sqlite3.Connection, run_id: str, config: dict,
               units: list[dict], hotwords: list[str]) -> None:
    connection.executemany("INSERT INTO optimization_summaries(run_id,kind,item_id) VALUES(?,'group',?)",
                           [(run_id, 'd:' + detail['id'])
                            for unit in units for detail in unit.get('_detail_sources', [])])
    insights.update_counts(connection, run_id)
    if not policy.fixed_short_plan(config, units):
        connection.executemany('INSERT INTO optimization_packet_nodes VALUES(?,?,?,?)',
                               [(run_id, stage, i, store.encode(ids))
                                for i, (stage, ids) in enumerate(compile_plan(config, units, hotwords))])


def nodes(run_id: str) -> list[dict]:
    with db.connect() as connection:
        rows = connection.execute('SELECT stage,ordinal,items_json FROM optimization_packet_nodes WHERE run_id=? ORDER BY ordinal',
                                  (run_id,)).fetchall()
    if not rows:
        run, units = store.get(run_id), store.units(run_id)
        if policy.fixed_short_plan(run['config'], units):
            # Reconstructed solely from immutable snapshots, never repacked
            # from pending state. IDs/order equal the original fixed plan.
            return [{'stage': stage, 'key': f'{stage}:{ordinal}', 'ids': ids}
                    for ordinal, (stage, ids) in enumerate(
                        compile_plan(run['config'], units, run['hotwords']))]
    return [{'stage': row[0], 'key': f'{row[0]}:{row[1]}', 'ids': json.loads(row[2])} for row in rows]


def call_count(run_id: str) -> int:
    with db.connect() as connection:
        row = connection.execute('SELECT calls_used FROM transcript_optimizations WHERE id=?', (run_id,)).fetchone()
    if row is None:
        raise asyncio.CancelledError()
    return row[0]


def generation(run_id: str, values: list[tuple[int, str, str]]) -> None:
    if not values:
        return
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if not store._generation_running(connection, run_id):
            raise asyncio.CancelledError()
        for ordinal, text, method in values:
            if method not in ('model', 'basic_cleanup'):
                raise ValueError('Invalid generation method')
            connection.execute('UPDATE optimization_parts SET text=?,generation_method=? WHERE run_id=? AND unit_ordinal=? AND part_ordinal=0 AND text IS NULL',
                               (text, method, run_id, ordinal))
            connection.execute("UPDATE optimization_units SET optimized_text=?,generation_method=?,review_outcome='pending' WHERE run_id=? AND ordinal=? AND optimized_text IS NULL",
                               (text, method, run_id, ordinal))
        store._progress(connection, run_id)
        connection.commit()


def finalization(run_id: str, reviews: list[tuple[int, dict]], summaries: list[dict],
                 attempts: list[dict] | None = None, *, auxiliary: bool = False) -> None:
    """Commit valid reviews/insights together before repairing rejected items."""
    if not reviews and not summaries and not attempts:
        return
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        live = connection.execute(
            "SELECT 1 FROM transcript_optimizations WHERE id=? AND (state='running' AND published=0 AND phase IN ('review','summaries') OR state='succeeded' AND published=1 AND summary_state='running')",
            (run_id,)).fetchone()
        if not live:
            raise asyncio.CancelledError()
        for ordinal, review in reviews:
            outcome, details = review['outcome'], store.encode(review['details'])
            if outcome not in ('pass', 'fact', 'uncertain', 'unavailable'):
                raise ValueError('Invalid review outcome')
            connection.execute('UPDATE optimization_parts SET review_outcome=?,review_details_json=? WHERE run_id=? AND unit_ordinal=? AND review_outcome IS NULL',
                               (outcome, details, run_id, ordinal))
            connection.execute("UPDATE optimization_units SET review_outcome=?,review_status=?,review_reason=?,review_details_json=? WHERE run_id=? AND ordinal=? AND review_outcome='pending'",
                               (outcome, 'accepted' if outcome == 'pass' else 'needs_review' if outcome == 'fact' else None,
                                'review_failed' if outcome == 'fact' else None, details, run_id, ordinal))
        for item in summaries:
            connection.execute("""UPDATE optimization_summaries SET state=?,value_json=?,error=?,generation_method=?,quality_notice=?,skip_reason=?
                WHERE run_id=? AND kind=? AND item_id=? AND state IN ('pending','running')""",
                (item['state'], store.encode(item['texts']), item.get('error'), item.get('method'),
                 item.get('notice'), item.get('skip_reason'), run_id, item['kind'], item['id']))
        for attempt in attempts or []:
            # One bounded preview per insight attempt; scratch plans never persist.
            connection.execute('''INSERT INTO optimization_summary_attempts
                (run_id,kind,item_id,phase,outcome,candidate_json,error,elapsed_ms,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)''',
                (run_id, attempt['kind'], attempt['id'], attempt['phase'], attempt['outcome'],
                 store.encode({'preview': str(attempt.get('candidate', ''))[:600]}),
                 str(attempt['error'])[:400] if attempt.get('error') else None, attempt['elapsed_ms'], db.utcnow()))
            connection.execute('UPDATE optimization_summaries SET attempt_count=attempt_count+? WHERE run_id=? AND kind=? AND item_id=?',
                               (attempt.get('calls', 1), run_id, attempt['kind'], attempt['id']))
            connection.execute('''DELETE FROM optimization_summary_attempts
                WHERE run_id=? AND kind=? AND item_id=? AND id NOT IN (
                    SELECT id FROM optimization_summary_attempts WHERE run_id=? AND kind=? AND item_id=? ORDER BY id DESC LIMIT 12)''',
                (run_id, attempt['kind'], attempt['id'], run_id, attempt['kind'], attempt['id']))
        if not auxiliary:
            connection.execute('''UPDATE transcript_optimizations SET
                reviewed_units=(SELECT COUNT(*) FROM optimization_units WHERE run_id=? AND review_outcome IN ('pass','fact','uncertain','unavailable')),
                needs_review_units=(SELECT COUNT(*) FROM optimization_units WHERE run_id=? AND review_outcome='fact'),
                uncertain_units=(SELECT COUNT(*) FROM optimization_units WHERE run_id=? AND review_outcome='uncertain'),
                unavailable_units=(SELECT COUNT(*) FROM optimization_units WHERE run_id=? AND review_outcome='unavailable'),
                reviewed_parts=(SELECT COUNT(*) FROM optimization_parts WHERE run_id=? AND review_outcome IS NOT NULL),
                revision=revision+1,updated_at=? WHERE id=?''', (run_id, run_id, run_id, run_id, run_id, db.utcnow(), run_id))
            connection.execute("UPDATE transcript_optimizations SET review_state='completed',phase='summaries' WHERE id=? AND reviewed_units=total_units",
                               (run_id,))
        insights.update_counts(connection, run_id)
        connection.commit()


def pending(run_id: str) -> set[tuple[str, str]]:
    with db.connect() as connection:
        return {(row[0], row[1]) for row in connection.execute(
            "SELECT kind,item_id FROM optimization_summaries WHERE run_id=? AND state IN ('pending','running')", (run_id,))}


def begin_summaries(run_id: str, *, auxiliary: bool = False) -> None:
    with db.connect() as connection:
        row = connection.execute("""UPDATE transcript_optimizations SET summary_state='running',
            phase=CASE WHEN published=0 AND review_state='completed' THEN 'summaries' ELSE phase END
            WHERE id=? AND (state='running' AND published=0 OR state='succeeded' AND published=1 AND summary_state='queued')""",
            (run_id,))
        if not row.rowcount:
            raise asyncio.CancelledError()


def begin_workflow_checks(run_id: str) -> None:
    """Claim review and insights in one transaction after body completion."""
    with db.connect() as connection:
        cursor = connection.execute("""UPDATE transcript_optimizations SET
            phase=CASE WHEN reviewed_units=total_units THEN 'summaries' ELSE 'review' END,
            review_state=CASE WHEN reviewed_units=total_units THEN 'completed' ELSE 'running' END,
            summary_state='running',
            total_parts=(SELECT COUNT(*) FROM optimization_parts WHERE run_id=?),
            revision=revision+1,updated_at=?
            WHERE id=? AND state='running' AND published=0 AND completed_units=total_units""",
            (run_id, db.utcnow(), run_id))
        if not cursor.rowcount:
            raise asyncio.CancelledError()


def finish_summaries(run_id: str) -> None:
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if not connection.execute("SELECT 1 FROM transcript_optimizations WHERE id=? AND summary_state='running'",
                                  (run_id,)).fetchone():
            raise asyncio.CancelledError()
        if connection.execute("SELECT 1 FROM optimization_summaries WHERE run_id=? AND state IN ('pending','running')", (run_id,)).fetchone():
            raise ValueError('Unfinished insight nodes')
        failed = connection.execute("SELECT 1 FROM optimization_summaries WHERE run_id=? AND state='unavailable'", (run_id,)).fetchone()
        insights.update_counts(connection, run_id, 'partial' if failed else 'completed')
        connection.commit()
