"""OpenAI Chat Completions transport; internal requests retain stage budgets/schemas."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import re
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

PROTOCOL = 'openai_chat_completions'


class ContextWindowError(Exception):
    """Capacity failures bypass prose and auxiliary-review fallbacks."""


class InvalidOutput(ValueError):
    pass


def normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('请输入不含凭据或查询参数的 HTTP(S) Base URL / Enter an HTTP(S) base URL without credentials or query parameters')
    path = parsed.path.rstrip('/')
    for suffix in ('/chat/completions', '/models'):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))


def effective(config: dict) -> dict:
    """Read historical Ollama snapshots without rewriting their persistent JSON."""
    if config.get('protocol') == PROTOCOL:
        return config
    return {**config, 'protocol': PROTOCOL, 'base_url': config['base_url'].rstrip('/') + '/v1',
            'extra_body': {'reasoning_effort': 'none', **config.get('extra_body', {})},
            'structured_output_mode': config.get('structured_output_mode', 'json_object')}


def validate_extra(value: dict) -> dict:
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 16384:
        raise ValueError('高级参数过大 / Extra parameters exceed 16 KiB')
    forbidden = {'model', 'messages', 'stream', 'response_format', 'n', 'tools', 'tool_choice', 'functions', 'function_call',
                 'headers', 'api_key', 'authorization', 'base_url'}
    if forbidden.intersection(k.lower() for k in value):
        raise ValueError('高级参数不能覆盖工作流或鉴权字段 / Extra parameters cannot override workflow or authentication fields')
    if 'max_tokens' in value and 'max_completion_tokens' in value:
        raise ValueError('只能选择一种输出上限字段 / Choose one output token limit field')
    for key in ('max_tokens', 'max_completion_tokens'):
        if key in value and (type(value[key]) is not int or value[key] <= 0):
            raise ValueError('输出上限必须为正整数 / Output token limit must be a positive integer')
    return value


def request_body(config: dict, payload: dict) -> dict:
    config = effective(config)
    options = payload.get('options', {})
    body = {'model': config['model'], 'messages': deepcopy(payload['messages']), 'stream': True,
            'temperature': options.get('temperature', 0), 'max_tokens': options.get('num_predict', 4096)}
    extra = validate_extra(config.get('extra_body', {}))
    if 'max_completion_tokens' in extra:
        body.pop('max_tokens')
    body.update(extra)
    if body.get('temperature') is None:
        body.pop('temperature', None)
    schema = payload.get('format')
    if schema:
        mode = config.get('structured_output_mode', 'json_object')
        if mode == 'json_schema':
            body['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'optimization', 'strict': True, 'schema': schema}}
        else:
            # JSON mode alone does not communicate the required fields to the model.
            body['messages'][0]['content'] += '\nReturn only JSON matching this schema: ' + json.dumps(schema, ensure_ascii=False)
            if mode == 'json_object':
                body['response_format'] = {'type': 'json_object'}
    return body


def fits(config: dict, payload: dict) -> bool:
    body = request_body(config, payload)
    output = body.get('max_completion_tokens', body.get('max_tokens', 4096))
    return len(json.dumps(body['messages'], ensure_ascii=False).encode()) + len(json.dumps(body.get('response_format', {})).encode()) + output + 1024 <= config['context_length']


def check_capacity_error(message: str) -> None:
    if re.search(r'out of memory|not enough (?:system )?memory|insufficient memory|requires more.{0,30}memory|context.{0,40}(?:exceed|too (?:small|long)|limit)|(?:exceed|too long).{0,40}context|input length.{0,40}exceed|(?:input|prompt).{0,30}too long', message, re.I):
        raise ContextWindowError('上下文或内存容量不足，请调整预算或服务端配置 / Context or memory capacity insufficient')


async def provider_status(response: httpx.Response) -> None:
    if response.is_error:
        content = bytearray()
        async for part in response.aiter_bytes():
            content.extend(part[:8192 - len(content)])
            if len(content) >= 8192:
                break
        message = content.decode('utf-8', errors='replace')
        check_capacity_error(message)
        # Only explicit format incompatibility permits synthetic probe negotiation.
        if response.status_code in (400, 422) and re.search(r'response_format|json_schema|json_object', message, re.I) and re.search(r'not support|unsupported|unknown|unrecognized|not allowed|invalid.*(?:type|format)|not available', message, re.I):
            raise UnsupportedFormat('模型不支持此结构化格式 / Structured output format unsupported')
    response.raise_for_status()


class UnsupportedFormat(ValueError):
    pass


async def completion(connection: httpx.AsyncClient, body: dict, activity=None) -> str:
    text, finish, total = '', None, 0
    last_activity = time.monotonic()
    async def consume(item):
        nonlocal text, finish, last_activity
        if item.get('error'):
            check_capacity_error(str(item['error']))
            raise InvalidOutput('模型服务返回错误 / Model service returned an error')
        choices = item.get('choices')
        if not isinstance(choices, list):
            raise InvalidOutput('Invalid completion choices')
        for choice in choices:
            if choice.get('index', 0) != 0:
                continue
            delta = choice.get('delta', choice.get('message', {}))
            value = delta.get('content')
            if value is not None and not isinstance(value, str):
                raise InvalidOutput('Invalid completion content')
            text += value or ''
            if len(text) > 100_000:
                raise InvalidOutput('Output exceeds budget')
            if choice.get('finish_reason') is not None:
                finish = choice['finish_reason']
            if activity and value and time.monotonic() - last_activity >= 1:
                await asyncio.to_thread(activity, len(text))
                last_activity = time.monotonic()
    async with connection.stream('POST', 'chat/completions', json=body) as response:
        await provider_status(response)
        if 'application/json' in response.headers.get('content-type', '').lower():
            data = bytearray()
            async for part in response.aiter_bytes():
                data.extend(part)
                if len(data) > 8 * 1024**2:
                    raise InvalidOutput('Response exceeds limit')
            try:
                await consume(json.loads(data))
            except (ValueError, TypeError, AttributeError) as exc:
                raise InvalidOutput('Invalid completion JSON') from exc
        else:
            data = []
            async def event():
                joined = '\n'.join(data)
                if not joined:
                    return False
                if joined == '[DONE]':
                    return True
                try:
                    await consume(json.loads(joined))
                except (ValueError, TypeError, AttributeError) as exc:
                    if isinstance(exc, ContextWindowError):
                        raise
                    raise InvalidOutput('Invalid completion stream event') from exc
                return False
            async for line in response.aiter_lines():
                total += len(line.encode())
                if total > 8 * 1024**2 or len(line) > 1024**2:
                    raise InvalidOutput('Response exceeds limit')
                if line == '':
                    if await event():
                        break
                    data = []
                elif line.startswith('data:'):
                    data.append(line[5:].lstrip(' '))
            else:
                await event()
    if finish == 'length':
        raise ContextWindowError('输出达到 token 上限，请调整输出上限与上下文预算 / Output token limit reached')
    if finish != 'stop' or not text.strip():
        raise InvalidOutput('输出为空或未正常结束 / Empty or incomplete model output')
    return text
