"""Checkpointed cross-unit editing, followed by an independent fact review.

Packets are transport units, never source boundaries. Each accepted item is
committed before any repair; resuming filters the frozen plan by checkpoints.
"""
from __future__ import annotations

import asyncio
import json
import time

from . import optimization_budget as calls
from . import optimization_engine as engine
from . import optimization_packet_policy as policy
from . import optimization_packet_store as checkpoints
from . import optimization_provider as provider
from . import optimization_store as store
from . import optimization_summaries as insights
from . import optimization_summary_policy as summary_policy


def unique_object(pairs):
    """Reject duplicate IDs locally while retaining unrelated valid results."""
    values, duplicates = {}, set()
    for key, value in pairs:
        if key in values:
            duplicates.add(key)
        values[key] = value
    return {key: value for key, value in values.items() if key not in duplicates}


async def structured(profile: dict, request: dict) -> dict:
    try:
        value = json.loads(await engine.streamed_text(profile, request),
                           object_pairs_hook=unique_object)
    except (json.JSONDecodeError, TypeError) as exc:
        raise engine.InvalidOutput('Invalid packet JSON') from exc
    if not isinstance(value, dict):
        raise engine.InvalidOutput('Invalid packet object')
    return value


def insight(item: dict, kind: str, text: object, *, repaired: bool = False,
            fallback: bool = False) -> dict:
    source = item['original_text']
    value = {'id': item['id'], 'kind': kind, 'texts': [], 'method': None,
             'notice': None, 'skip_reason': None}
    if not policy.meaningful(source):
        return {**value, 'state': 'not_applicable',
                'skip_reason': 'no_substantive_content'}
    error = (policy.insight_error(source, text, kind)
             if isinstance(text, str) and text.strip() else 'Missing insight')
    if error and not fallback:
        raise ValueError(error)
    if error:
        texts = summary_policy.extract_source(source, kind)
        return {**value, 'state': 'ready' if texts else 'unavailable',
                'texts': texts, 'method': 'source_excerpt', 'notice': 'source_excerpt',
                'error': None if texts else 'No usable source excerpt'}
    return {**value, 'state': 'ready', 'texts': [text.strip()],
            'method': 'model_repair' if repaired else 'model'}


class Processor:
    def __init__(self, manager, run_id: str, *, auxiliary: bool = False):
        self.manager = manager
        self.run_id = run_id
        self.run = store.get(run_id)
        self.profile = {**self.run['config'], '_hotwords': self.run['hotwords'],
                        '_short': self.run['total_chars'] <= 120}
        self.auxiliary = auxiliary
        self.last_attempts = 0
        self.insight_calls: dict[tuple[str, str], int] = {}

    async def request(self, node: dict, request: dict, *, request_limit: int = 3,
                      plain_id: str | None = None) -> dict:
        # Actual HTTP attempts, including transport retries, all enter the ledger.
        before = checkpoints.call_count(self.run_id)
        self.last_attempts = 0
        async def operation():
            if plain_id is not None:
                return {plain_id: await engine.streamed_text(self.profile, request)}
            return await structured(self.profile, request)
        try:
            with calls.scope(self.run_id, node['stage'], node['key']):
                return await self.manager.retry_io(
                    operation, request_limit=request_limit)
        finally:
            self.last_attempts = checkpoints.call_count(self.run_id) - before

    def commit(self, reviews=None, summaries=None, attempts=None):
        checkpoints.finalization(self.run_id, reviews or [], summaries or [],
                                attempts, auxiliary=self.auxiliary)

    async def generate(self, node: dict, items: list[dict], *, depth: int = 0):
        remaining = items
        errors, previous = {}, {}
        # Small tasks spend one editing call and one independent review call.
        maximum = 1 if self.profile.get('packet_joint') else 2
        for attempt in range(maximum):
            if not remaining:
                break
            request = policy.payload(self.profile, remaining, 'generation')
            plain_id = None
            if len(remaining) == 1 and self.profile.get('packet_joint') and self.profile.get('_short') and not self.profile.get('summary_scene_ids'):
                plain_id = remaining[0]['id']
                request = engine.payload_for(self.profile, remaining,
                                             self.run['hotwords'], '', '')
            if errors:
                request['messages'].extend([
                    {'role': 'assistant', 'content': json.dumps(previous, ensure_ascii=False)},
                    {'role': 'user', 'content': '只修复已定位的问题，保留其他有效内容，返回完整正文与JSON：'
                     + json.dumps(errors, ensure_ascii=False)}])
                if not engine.request_fits(self.profile, request):
                    # The source remains complete; omit previous draft copies.
                    request = policy.payload(self.profile, remaining, 'generation')
                    request['messages'][0]['content'] += '\n修复下列问题：' + json.dumps(errors, ensure_ascii=False)
            try:
                values = await self.request(node, request, plain_id=plain_id)
            except provider.OutputLimitError:
                # Keep the configured output ceiling; reduce the work per call.
                if len(remaining) > 1 and depth < 5 and not self.profile.get('packet_joint'):
                    middle = len(remaining) // 2
                    await self.generate(node, remaining[:middle], depth=depth + 1)
                    await self.generate(node, remaining[middle:], depth=depth + 1)
                    return
                values = {}
            except engine.InvalidOutput:
                values = {}
            accepted, rejected = [], []
            errors = {}
            for item in remaining:
                try:
                    text = policy.checked_item(item, values[item['id']], english=bool(self.profile.get('english_source')))
                except (engine.InvalidOutput, KeyError, TypeError, ValueError, AttributeError) as exc:
                    errors[item['id']] = str(exc)[:300]
                    rejected.append(item)
                else:
                    accepted.append((item['ordinal'], text, 'model'))
            checkpoints.generation(self.run_id, accepted)
            previous = {item['id']: values.get(item['id'], '') for item in rejected}
            remaining = rejected
        # Literal cleanup is visible in results and never reported as model editing.
        checkpoints.generation(self.run_id, [
            (item['ordinal'], policy.format_paragraphs(policy.literal_cleanup(item['original_text'])), 'basic_cleanup')
            for item in remaining])

    async def summarize(self, node: dict, items: list[dict], kind: str, *,
                        depth: int = 0, repair_only: bool = False):
        remaining = items
        for attempt in range(1 if repair_only else 2):
            exhausted = [item for item in remaining
                         if self.insight_calls.get((kind, item['id']), 0) >= 3]
            if exhausted:
                self.commit(summaries=[insight(item, kind, None, fallback=True) for item in exhausted])
                remaining = [item for item in remaining if item not in exhausted]
            if not remaining:
                return
            request = policy.payload(self.profile, remaining, kind)
            if attempt or repair_only:
                request['messages'][0]['content'] += (
                    '\n修复格式、数字或长度：只写一句更短的主题重点，不列举全文。'
                    '数字保留来源原值和限定，不能换算单位。')
            started = time.monotonic()
            failure = None
            try:
                allowance = min(3 - self.insight_calls.get((kind, item['id']), 0)
                                for item in remaining)
                values = await self.request({**node, 'stage': kind}, request, request_limit=allowance)
            except provider.OutputLimitError as exc:
                if len(remaining) > 1 and depth < 5:
                    self.commit(attempts=self.attempts(remaining, kind, {}, started,
                                                      'rejected', type(exc).__name__))
                    middle = len(remaining) // 2
                    await self.summarize(node, remaining[:middle], kind, depth=depth + 1,
                                         repair_only=repair_only)
                    await self.summarize(node, remaining[middle:], kind, depth=depth + 1,
                                         repair_only=repair_only)
                    return
                values, failure = {}, str(exc)
            except engine.InvalidOutput as exc:
                values, failure = {}, str(exc)
            except Exception as exc:
                # Record attempted items without converting a transport or budget
                # interruption into a completed insight. The manager pauses it.
                self.commit(attempts=self.attempts(remaining, kind, {}, started,
                                                  'interrupted', type(exc).__name__))
                raise
            finished, rejected, records = [], [], []
            for item in remaining:
                try:
                    result = insight(item, kind, values.get(item['id']),
                                     repaired=bool(attempt or repair_only))
                except ValueError as exc:
                    rejected.append(item)
                    error = failure or str(exc)
                    outcome = 'rejected'
                else:
                    finished.append(result)
                    outcome, error = 'accepted', None
                records.extend(self.attempts([item], kind, values, started, outcome, error))
            self.commit(summaries=finished, attempts=records)
            remaining = rejected
        self.commit(summaries=[insight(item, kind, None, fallback=True) for item in remaining])

    def attempts(self, items, kind, values, started, outcome, error=None):
        if not self.last_attempts:
            return []
        for item in items:
            key = (kind, item['id'])
            self.insight_calls[key] = self.insight_calls.get(key, 0) + self.last_attempts
        return [{'id': item['id'], 'kind': kind, 'phase': 'packet:generate',
                 'calls': self.last_attempts,
                 'outcome': outcome, 'candidate': values.get(item['id'], ''),
                 'error': error[:400] if error else None,
                 'elapsed_ms': int((time.monotonic() - started) * 1000)}
                for item in items]

    async def finalize(self, node: dict, items: list[dict], *, joint: bool = False,
                       depth: int = 0):
        pending = checkpoints.pending(self.run_id)
        groups = policy.group_sources(store.units(self.run_id))
        scenes = policy.scene_sources(store.units(self.run_id),
                                      self.profile.get('summary_scene_ids', []))
        groups = [item for item in groups if ('group', item['id']) in pending]
        scenes = [item for item in scenes if ('scene', item['id']) in pending]
        reviews = [] if self.auxiliary else [
            item for item in items if item.get('review_outcome') == 'pending']
        if not joint:
            groups = [item for item in items if ('group', item['id']) in pending]
            scenes = []
        if not reviews and not groups and not scenes:
            return
        # On a summary-only retry the existing independent review is immutable.
        if self.auxiliary or not reviews:
            for kind, sources in (('group', groups), ('scene', scenes)):
                for packet in policy.pack(sources, maxitems=16, maxchars=6000,
                                          cfg=self.profile, stage=kind):
                    await self.summarize(node, packet, kind)
            return
        review_ids = {item['id'] for item in reviews}
        deferred_groups = [] if joint else [item for item in groups if item['id'] not in review_ids]
        if not joint:
            groups = [item for item in groups if item['id'] in review_ids]
        request = (policy.joint_payload(self.profile, reviews, groups, scenes) if joint
                   else policy.payload(self.profile, reviews, 'finalization'))
        literal_short = bool(joint and self.profile.get('_short') and len(items) == 1
                             and not scenes and all(item['candidate'] == engine.basic_cleanup(item['original_text'])
                                                    for item in reviews))
        if literal_short:
            # Exact/literal output has no new facts to review. Keep the local
            # provenance diagnostic and use the second call for the insight.
            request = policy.payload(self.profile, groups, 'group')
        started = time.monotonic()
        try:
            values = await self.request({**node, 'stage': 'group'} if literal_short else node, request)
        except provider.OutputLimitError:
            if not joint and len(items) > 1 and depth < 5:
                self.commit(attempts=self.attempts(groups, 'group', {}, started,
                                                  'rejected', 'OutputLimitError'))
                middle = len(items) // 2
                await self.finalize(node, items[:middle], depth=depth + 1)
                await self.finalize(node, items[middle:], depth=depth + 1)
                return
            values = {}
        except engine.InvalidOutput:
            values = {}
        except Exception as exc:
            records = [record for kind, sources in (('group', groups), ('scene', scenes))
                       for record in self.attempts(sources, kind, {}, started,
                                                   'interrupted', type(exc).__name__)]
            self.commit(attempts=records)
            raise
        if literal_short:
            review_values, group_values, scene_values = {}, values, {}
        elif joint:
            review_values = values.get('reviews', {})
            group_values, scene_values = values.get('groups', {}), values.get('scenes', {})
        else:
            review_values = values
            group_values = {key: value.get('summary') for key, value in values.items()
                            if isinstance(value, dict)}
            scene_values = {}
        review_values = review_values if isinstance(review_values, dict) else {}
        checked = [(item['ordinal'], policy.review_result(
            review_values.get(item['id']), item['original_text'], item['candidate']))
            for item in reviews]
        accepted, rejected, records = [], [], []
        for kind, sources, responses in (
                ('group', groups, group_values), ('scene', scenes, scene_values)):
            responses = responses if isinstance(responses, dict) else {}
            for item in sources:
                try:
                    result = insight(item, kind, responses.get(item['id']))
                except ValueError as exc:
                    records.extend(self.attempts([item], kind, responses, started,
                                                 'rejected', str(exc)))
                    rejected.append((kind, item))
                else:
                    accepted.append(result)
                    records.extend(self.attempts([item], kind, responses, started, 'accepted'))
        self.commit(checked, accepted, records)
        # Never re-vote an invalid review. Repair insights alone, preserving the
        # already committed independent review and every valid insight.
        for kind in ('group', 'scene'):
            sources = [item for k, item in rejected if k == kind]
            if joint:
                self.commit(summaries=[insight(item, kind, None, fallback=True)
                                       for item in sources])
            elif sources:
                await self.summarize(node, sources, kind, repair_only=True)
        if deferred_groups:
            await self.summarize(node, deferred_groups, 'group')

    async def execute(self):
        nodes = checkpoints.nodes(self.run_id)
        if not self.auxiliary:
            units = store.units(self.run_id)
            checkpoints.generation(self.run_id, [
                (unit['ordinal'], policy.format_paragraphs(policy.literal_cleanup(unit['original_text']))
                 if policy.literal_frame(unit, english=bool(self.profile.get('english_source'))) else engine.basic_cleanup(unit['original_text']), 'basic_cleanup')
                for unit in units if unit['text'] is None and
                (not policy.meaningful(unit['original_text']) or policy.literal_frame(unit, english=bool(self.profile.get('english_source'))))])
            for node in nodes:
                if node['stage'] != 'generation':
                    continue
                units = {str(unit['ordinal']): unit for unit in store.units(self.run_id)}
                items = [{**units[key], 'id': key} for key in node['ids']
                         if units[key]['text'] is None]
                if items:
                    await self.generate(node, items)
            checkpoints.begin_workflow_checks(self.run_id)
        else:
            checkpoints.begin_summaries(self.run_id, auxiliary=True)
        units = store.units(self.run_id)
        # Local non-substantive items need no model call or fictional fact check.
        groups, scenes = policy.group_sources(units), policy.scene_sources(
            units, self.profile.get('summary_scene_ids', []))
        pending = checkpoints.pending(self.run_id)
        local_summaries = [insight(item, kind, None) for kind, items in
                           (('group', groups), ('scene', scenes)) for item in items
                           if (kind, item['id']) in pending and not policy.meaningful(item['original_text'])]
        local_reviews = [] if self.auxiliary else [
            (unit['ordinal'], policy.review_result(None, unit['original_text'], unit['text']))
            for unit in units if unit['review_outcome'] == 'pending'
            and not policy.meaningful(unit['original_text'])]
        self.commit(local_reviews, local_summaries)
        indexed = {'group': {item['id']: item for item in groups},
                   'scene': {item['id']: item for item in scenes}}
        for node in nodes:
            stage = node['stage']
            if stage in ('joint', 'finalization'):
                fresh = {str(unit['ordinal']): {**unit, 'id': str(unit['ordinal']),
                                              'candidate': unit['text']}
                         for unit in store.units(self.run_id)}
                await self.finalize(node, [fresh[key] for key in node['ids']], joint=stage == 'joint')
            elif stage in ('group', 'scene'):
                pending = checkpoints.pending(self.run_id)
                items = [indexed[stage][key] for key in node['ids'] if (stage, key) in pending]
                if items:
                    await self.summarize(node, items, stage)
            elif stage == 'scene_reduction':
                for key in node['ids']:
                    if ('scene', key) not in checkpoints.pending(self.run_id):
                        continue
                    async def record(*args):
                        await insights.record_attempt(self.run_id, 'scene', key, *args)
                    with calls.scope(self.run_id, 'scene', node['key']):
                        result = await insights.scene_title_v2(
                            self.run_id, self.profile, indexed['scene'][key]['original_text'],
                            summary_policy.Circuit(), record)
                    self.commit(summaries=[{'id': key, 'kind': 'scene', 'texts': result.texts,
                        'state': 'ready' if result.texts else 'not_applicable' if result.skip_reason else 'unavailable',
                        'error': None if result.texts or result.skip_reason else 'No usable scene title',
                        'method': result.method, 'notice': result.quality_notice,
                        'skip_reason': result.skip_reason}])
        checkpoints.finish_summaries(self.run_id)
