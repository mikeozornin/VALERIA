"""Download the pinned official Qwen3 tokenizer, checking its checksum."""
import argparse
import hashlib
from pathlib import Path
import httpx

REVISION = '5cf2132abc99cad020ac570b19d031efec650f2b'
SHA256 = '83cdf8c3a34f68862319cb1810ee7b1e2c0a44e0864ae930194ddb76bb7feb8d'
URL = f'https://huggingface.co/Qwen/Qwen3-Embedding-4B/resolve/{REVISION}/tokenizer.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    response = httpx.get(URL, follow_redirects=True, timeout=60)
    response.raise_for_status()
    if hashlib.sha256(response.content).hexdigest() != SHA256:
        raise RuntimeError('Tokenizer checksum mismatch')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite a potentially different tokenizer silently.
    with args.output.open('xb') as file:
        file.write(response.content)
    print(args.output)


if __name__ == '__main__':
    main()
