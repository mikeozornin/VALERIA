"""Validated embeddings client. Credentials and note text never enter diagnostics."""
import asyncio
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path

import httpx


class EmbeddingError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    base_url: str = 'http://127.0.0.1:8080/v1'
    api_key: str = ''
    model: str = 'Qwen/Qwen3-Embedding-4B'
    dimensions: int = 2560
    timeout: float = 30
    batch_size: int = 8
    candidates: int = 30
    tokenizer_path: str = ''
    max_tokens: int = 32000
    instruction: str = 'Retrieve technical notes and past decisions relevant to the development task.'

    @classmethod
    def from_env(cls):
        flag = os.getenv('MCP_MEMORY_HYBRID', 'false').lower()
        if flag not in ('true', 'false'):
            raise ValueError('MCP_MEMORY_HYBRID must be true or false')
        result = cls(
            enabled=flag == 'true',
            base_url=os.getenv('MCP_MEMORY_EMBEDDING_BASE_URL', cls.base_url).rstrip('/'),
            api_key=os.getenv('MCP_MEMORY_EMBEDDING_API_KEY', ''),
            model=os.getenv('MCP_MEMORY_EMBEDDING_MODEL', cls.model),
            dimensions=int(os.getenv('MCP_MEMORY_EMBEDDING_DIMENSIONS', '2560')),
            timeout=float(os.getenv('MCP_MEMORY_EMBEDDING_TIMEOUT', '30')),
            batch_size=int(os.getenv('MCP_MEMORY_EMBEDDING_BATCH_SIZE', '8')),
            candidates=int(os.getenv('MCP_MEMORY_CANDIDATES', '30')),
            tokenizer_path=os.getenv('MCP_MEMORY_TOKENIZER_PATH', ''),
        )
        if not result.base_url.startswith(('http://', 'https://')) or not result.model.strip():
            raise ValueError('Invalid embedding endpoint/model')
        if (not 32 <= result.dimensions <= 2560 or not 1 <= result.batch_size <= 200
                or not 1 <= result.candidates <= 100 or not math.isfinite(result.timeout)
                or result.timeout <= 0):
            raise ValueError('Invalid embedding limits')
        return result

    @property
    def profile(self):
        # Endpoint/model/dimension changes must not mix incompatible vector spaces.
        raw = json.dumps([self.base_url, self.model, self.dimensions, 'document-v1'])
        return hashlib.sha256(raw.encode()).hexdigest()


def normalize(vector, dimensions):
    if not isinstance(vector, list) or len(vector) != dimensions:
        raise EmbeddingError('Invalid embedding dimensions')
    if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in vector):
        raise EmbeddingError('Invalid embedding values')
    norm = math.hypot(*vector)
    if not math.isfinite(norm) or norm == 0:
        raise EmbeddingError('Invalid embedding norm')
    return [x / norm for x in vector]


@lru_cache(maxsize=4)
def _load_tokenizer(path, modified_ns):
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(path)
    tokenizer.no_truncation()
    tokenizer.no_padding()
    return tokenizer


class EmbeddingClient:
    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self.transport = transport
        self._tokenizer = None

    def token_counts(self, texts):
        if self._tokenizer is None:
            try:
                path = Path(self.settings.tokenizer_path)
                self._tokenizer = _load_tokenizer(str(path), path.stat().st_mtime_ns)
            except Exception:
                raise EmbeddingError('Cannot load embedding tokenizer; configure MCP_MEMORY_TOKENIZER_PATH') from None
        return [len(item.ids) for item in self._tokenizer.encode_batch(texts)]

    def validate_lengths(self, texts):
        counts = self.token_counts(texts)
        # Reserve room for the serving implementation's special tokens.
        if any(count > self.settings.max_tokens - 16 for count in counts):
            raise EmbeddingError('Embedding input exceeds token limit; split document explicitly')
        return counts

    async def embed(self, texts):
        if not texts:
            return []
        self.validate_lengths(texts)
        headers = {'Accept-Encoding': 'identity'}
        if self.settings.api_key:
            headers['Authorization'] = f'Bearer {self.settings.api_key}'
        payload = {'model': self.settings.model, 'input': texts, 'encoding_format': 'float'}
        # The deployed Qwen server rejects explicit dimensions=2560 but returns
        # its native 2560 dimensions when the parameter is omitted.
        if self.settings.dimensions != 2560:
            payload['dimensions'] = self.settings.dimensions
        try:
            async with asyncio.timeout(self.settings.timeout):
                async with httpx.AsyncClient(transport=self.transport, timeout=httpx.Timeout(
                        self.settings.timeout, connect=min(10, self.settings.timeout))) as client:
                    for attempt in range(3):
                        try:
                            response = await client.post(self.settings.base_url + '/embeddings',
                                                         json=payload, headers=headers)
                        except httpx.TransportError:
                            if attempt == 2:
                                raise EmbeddingError('Embedding transport unavailable') from None
                            await asyncio.sleep(0.5 * 2 ** attempt)
                            continue
                        if response.status_code == 429 or response.status_code >= 500:
                            if attempt < 2:
                                await asyncio.sleep(0.5 * 2 ** attempt)
                                continue
                        if response.is_error:
                            raise EmbeddingError(f'Embedding API HTTP {response.status_code}')
                        break
        except (TimeoutError, httpx.TimeoutException):
            raise EmbeddingError('Embedding request timed out') from None
        try:
            body = response.json()
            data = body['data']
            if body.get('model', self.settings.model) != self.settings.model:
                raise EmbeddingError('Unexpected embedding model')
            if not isinstance(data, list) or len(data) != len(texts):
                raise EmbeddingError('Invalid embedding count')
            ordered = [None] * len(texts)
            for item in data:
                index = item['index']
                if type(index) is not int or not 0 <= index < len(texts) or ordered[index] is not None:
                    raise EmbeddingError('Invalid embedding indices')
                ordered[index] = normalize(item['embedding'], self.settings.dimensions)
            return ordered
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            raise EmbeddingError('Invalid embedding response') from None

    def embed_sync(self, texts):
        return asyncio.run(self.embed(texts))

    def query(self, query):
        return self.embed_sync([f'Instruct: {self.settings.instruction}\nQuery: {query}'])[0]
